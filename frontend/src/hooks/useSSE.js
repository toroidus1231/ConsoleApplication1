// useSSE — opens a single EventSource to the platform's SSE endpoint
// (GET /api/v1/system/events, Contracts §4.10) and surfaces the event stream
// to React. The FacilityContext opens exactly one of these on mount and fans
// events out to consumers via context (§6.2).
//
// Note on auth: the EventSource API cannot set custom headers, so it cannot
// send X-API-Key. In this on-prem deployment the frontend is same-origin with
// the API; the key is appended as a query param as a best-effort fallback for
// backends that accept it there. SSE is server->client only with EventSource's
// built-in auto-reconnect (§6.2).
import { useState, useEffect, useRef, useCallback } from "react";
import { API_BASE, getApiKey } from "./useApi.js";

export const SSE_PATH = "/system/events";

// The event types the platform emits (Contracts §3.1 Event, §6.2 routing).
export const EVENT_TYPES = [
  "device_discovered",
  "poll_result",
  "test_started",
  "test_completed",
  "test_aborted",
  "punch_item",
  "worker_health",
  "manual_confirmation_needed",
];

export function useSSE(path = SSE_PATH) {
  const [connected, setConnected] = useState(false);
  // Rolling buffer of the last N events (most recent last).
  const [events, setEvents] = useState([]);
  // Latest event payload per type, e.g. { poll_result: {...}, ... }.
  const [latest, setLatest] = useState({});
  // Monotonic counts per type (useful for live dashboard counters).
  const [counts, setCounts] = useState({});
  // Local subscribers registered via subscribe(); keyed by event type.
  const subscribers = useRef(new Map());

  const subscribe = useCallback((type, handler) => {
    const set = subscribers.current.get(type) || new Set();
    set.add(handler);
    subscribers.current.set(type, set);
    return () => {
      const s = subscribers.current.get(type);
      if (s) s.delete(handler);
    };
  }, []);

  useEffect(() => {
    const key = getApiKey();
    const url = `${API_BASE}${path}${key ? `?api_key=${encodeURIComponent(key)}` : ""}`;
    const source = new EventSource(url);

    source.onopen = () => setConnected(true);
    source.onerror = () => setConnected(false); // EventSource auto-reconnects

    const handle = (type) => (e) => {
      let data = {};
      try {
        data = e.data ? JSON.parse(e.data) : {};
      } catch {
        data = { raw: e.data };
      }
      const evt = { type, data, ts: Date.now() };
      setEvents((prev) => [...prev.slice(-199), evt]);
      setLatest((prev) => ({ ...prev, [type]: data }));
      setCounts((prev) => ({ ...prev, [type]: (prev[type] || 0) + 1 }));
      const set = subscribers.current.get(type);
      if (set) set.forEach((fn) => fn(data, evt));
    };

    EVENT_TYPES.forEach((type) => source.addEventListener(type, handle(type)));
    // keepalive frames are emitted every 30s by the backend; ignore payload
    // but treat as proof of life.
    source.addEventListener("keepalive", () => setConnected(true));
    // Generic message fallback (event types the backend may add later).
    source.onmessage = handle("message");

    return () => source.close();
  }, [path]);

  return { connected, events, latest, counts, subscribe };
}
