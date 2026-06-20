// LiveRegisters — renders a device's current register/measurement values and
// updates them live from poll_result SSE events for that device (Contracts
// §6.2: poll_result -> device detail register values).
import { useEffect, useState } from "react";
import { useFacility } from "../context/FacilityContext.jsx";
import { fmtNumber, fmtTime, EmptyState } from "./ui.jsx";

export default function LiveRegisters({ deviceId, initial, initialTimestampNs }) {
  const { sse } = useFacility();
  const [values, setValues] = useState(initial || {});
  const [updatedAt, setUpdatedAt] = useState(
    initialTimestampNs ? new Date(initialTimestampNs / 1e6).toISOString() : null
  );
  const [pulse, setPulse] = useState({});

  // Re-seed when the device changes or a fresh last_poll arrives.
  useEffect(() => {
    setValues(initial || {});
  }, [deviceId, initial]);

  // Subscribe to poll_result for this device only.
  useEffect(() => {
    if (!deviceId) return undefined;
    return sse.subscribe("poll_result", (data) => {
      if (String(data.device_id) !== String(deviceId)) return;
      const measurements = data.measurements || {};
      setValues((prev) => ({ ...prev, ...measurements }));
      setUpdatedAt(new Date().toISOString());
      // brief highlight on changed registers
      const changed = {};
      Object.keys(measurements).forEach((k) => (changed[k] = Date.now()));
      setPulse(changed);
    });
  }, [deviceId, sse]);

  const names = Object.keys(values);
  if (names.length === 0) {
    return <EmptyState title="No register values yet" detail="Waiting for the next poll cycle." />;
  }

  return (
    <div>
      <div className="live-meta">
        <span className={`live-dot ${sse.connected ? "on" : "off"}`} />
        Updated {fmtTime(updatedAt)}
      </div>
      <div className="register-grid">
        {names.sort().map((name) => (
          <div
            key={name}
            className={`register-tile ${pulse[name] ? "pulse" : ""}`}
          >
            <div className="register-name">{name}</div>
            <div className="register-value">{fmtNumber(values[name])}</div>
          </div>
        ))}
      </div>
    </div>
  );
}
