import { useEffect, useState } from "react";

const EVENT_TYPES = [
  "device_discovered",
  "poll_result",
  "test_started",
  "test_completed",
  "test_aborted",
  "punch_item",
  "manual_confirmation_needed",
  "worker_health",
  "firmware_mismatch",
];

// useSSE subscribes to /api/v1/system/events and keeps a rolling buffer of
// the last 200 events plus connected/disconnected state.
export function useSSE() {
  const [events, setEvents] = useState([]);
  const [connected, setConnected] = useState(false);

  useEffect(() => {
    const url = `/api/v1/system/events`;
    const source = new EventSource(url);

    source.onopen = () => setConnected(true);
    source.onerror = () => setConnected(false);

    const handlers = EVENT_TYPES.map((type) => {
      const fn = (e) => {
        let data = {};
        try { data = JSON.parse(e.data); } catch (_) {}
        setEvents((prev) => [...prev.slice(-199), { type, data, ts: Date.now() }]);
      };
      source.addEventListener(type, fn);
      return [type, fn];
    });

    return () => {
      handlers.forEach(([type, fn]) => source.removeEventListener(type, fn));
      source.close();
    };
  }, []);

  return { events, connected };
}
