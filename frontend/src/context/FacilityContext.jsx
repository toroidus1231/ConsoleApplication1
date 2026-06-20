// FacilityContext — global app state (Contracts §6.1).
//
// Responsibilities:
//   * Holds facility info (GET /system/config) and a live system-health
//     snapshot (GET /system/health), refreshed on a timer + on worker_health.
//   * Owns the single SSE connection (useSSE) and fans events out to consumers.
//   * Maintains a queue of pending manual_confirmation_needed events so a modal
//     can prompt the engineer (§6.2) anywhere in the app.
//   * Tracks the API key presence so ApiKeyGate can gate the UI.
import {
  createContext,
  useContext,
  useEffect,
  useState,
  useCallback,
  useRef,
} from "react";
import { useApi, getApiKey, setApiKey as persistApiKey } from "../hooks/useApi.js";
import { useSSE } from "../hooks/useSSE.js";

const FacilityContext = createContext(null);

const HEALTH_POLL_MS = 15000;

export function FacilityProvider({ children }) {
  const api = useApi();
  const sse = useSSE();

  const [apiKey, setApiKeyState] = useState(getApiKey());
  const [config, setConfig] = useState(null);
  const [health, setHealth] = useState(null);
  const [healthError, setHealthError] = useState(null);
  // Queue of pending manual confirmations (each: {test_id, prompt, ts}).
  const [confirmations, setConfirmations] = useState([]);
  const seenConfirmations = useRef(new Set());

  const hasKey = Boolean(apiKey);

  const saveApiKey = useCallback((key) => {
    persistApiKey(key);
    setApiKeyState(key || "");
  }, []);

  const refreshHealth = useCallback(async () => {
    try {
      const data = await api.get("/system/health");
      setHealth(data);
      setHealthError(null);
    } catch (err) {
      setHealthError(err.message);
    }
  }, [api]);

  // Initial load of facility config + health once a key is present.
  useEffect(() => {
    if (!hasKey) return;
    let active = true;
    (async () => {
      try {
        const cfg = await api.get("/system/config");
        if (active) setConfig(cfg);
      } catch {
        // Non-fatal: dashboard still works without the config banner.
      }
    })();
    refreshHealth();
    const t = setInterval(refreshHealth, HEALTH_POLL_MS);
    return () => {
      active = false;
      clearInterval(t);
    };
  }, [hasKey, api, refreshHealth]);

  // worker_health events should refresh the health snapshot promptly (§6.2).
  useEffect(() => {
    if (!hasKey) return undefined;
    return sse.subscribe("worker_health", () => {
      refreshHealth();
    });
  }, [hasKey, sse, refreshHealth]);

  // Queue incoming manual_confirmation_needed events for the modal (§6.2).
  useEffect(() => {
    return sse.subscribe("manual_confirmation_needed", (data) => {
      const id = data.test_id || `${data.prompt}-${Date.now()}`;
      if (seenConfirmations.current.has(id)) return;
      seenConfirmations.current.add(id);
      setConfirmations((prev) => [
        ...prev,
        { test_id: data.test_id, prompt: data.prompt, ts: Date.now() },
      ]);
    });
  }, [sse]);

  const dismissConfirmation = useCallback((testId) => {
    setConfirmations((prev) => prev.filter((c) => c.test_id !== testId));
  }, []);

  const value = {
    api,
    sse,
    apiKey,
    hasKey,
    saveApiKey,
    facilityName: config?.facility_name || config?.facility || "Commissioning Platform",
    config,
    health,
    healthError,
    refreshHealth,
    confirmations,
    dismissConfirmation,
  };

  return (
    <FacilityContext.Provider value={value}>{children}</FacilityContext.Provider>
  );
}

export function useFacility() {
  const ctx = useContext(FacilityContext);
  if (!ctx) {
    throw new Error("useFacility must be used within a FacilityProvider");
  }
  return ctx;
}
