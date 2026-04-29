import React, { useEffect, useRef, useState } from "react";
import { api } from "../hooks/useApi";

// Module 18 (Discovery view): poll /discovery/status and /discovery/results,
// show live-progressing classified vs unclassified counts and a per-device
// card grid coloured by match status.

export default function Discovery() {
  const [scanId, setScanId] = useState(null);
  const [status, setStatus] = useState(null);
  const [devices, setDevices] = useState([]);
  const [subnets, setSubnets] = useState("10.4.0.0/24");
  const pollRef = useRef(null);

  const startScan = async () => {
    const r = await api.post("/discovery/scan", { subnets: subnets.split(",").map(s => s.trim()) });
    setScanId(r.scan_id);
  };

  const refreshExisting = async () => {
    // Pre-populate from the seeded scan if no scan ID has been started yet.
    try {
      const s = await api.get("/discovery/status/scan-1");
      setScanId(s.scan_id);
    } catch (_) {}
  };

  useEffect(() => { refreshExisting(); }, []);

  useEffect(() => {
    if (!scanId) return;
    const tick = async () => {
      try {
        const s = await api.get(`/discovery/status/${scanId}`);
        setStatus(s);
        const r = await api.get(`/discovery/results/${scanId}`);
        setDevices(r.devices || []);
      } catch (_) {}
    };
    tick();
    pollRef.current = setInterval(tick, 1500);
    return () => clearInterval(pollRef.current);
  }, [scanId]);

  const total = (status?.devices_found ?? 0);
  const classified = status?.devices_classified ?? 0;
  const unmatched = status?.devices_unmatched ?? 0;
  const pct = total > 0 ? (classified / total) * 100 : 0;

  return (
    <>
      <div className="page-header">
        <h1>Discovery</h1>
        <span className="crumb">{scanId ? `Scan ${scanId} · ${status?.status || "idle"}` : "no scan"}</span>
      </div>

      <div className="card">
        <div className="row">
          <input style={{ flex: 1 }} value={subnets}
                 onChange={(e) => setSubnets(e.target.value)}
                 placeholder="comma-separated CIDR(s) e.g. 10.4.0.0/24, 10.4.1.0/24" />
          <button onClick={startScan}>Start scan</button>
        </div>
      </div>

      <div className="tile-row">
        <div className="tile"><div className="label">Devices found</div>
          <div className="value">{total}</div></div>
        <div className="tile"><div className="label">Classified</div>
          <div className="value pass">{classified}</div></div>
        <div className="tile"><div className="label">Unmatched</div>
          <div className="value maj">{unmatched}</div></div>
        <div className="tile"><div className="label">Errors</div>
          <div className="value crit">{status?.errors?.length || 0}</div></div>
      </div>

      <div className="card">
        <div className="card-h">Sweep progress</div>
        <div style={{ background: "var(--bg-2)", borderRadius: 6, height: 14, overflow: "hidden" }}>
          <div style={{
            width: `${pct}%`, height: "100%",
            background: "linear-gradient(90deg, var(--accent-dim), var(--accent))",
            transition: "width 0.4s ease",
          }} />
        </div>
        <div style={{ marginTop: 8, color: "var(--text-faint)", fontSize: 11 }}>
          {classified} of {total} classified ({pct.toFixed(1)}%)
        </div>
      </div>

      <div className="card">
        <div className="card-h">Discovered devices</div>
        <div style={{
          display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(220px, 1fr))",
          gap: 8,
        }}>
          {devices.map((d) => (
            <div key={d.ip + d.protocol} className="card" style={{
              padding: 10, marginBottom: 0,
              borderLeft: `3px solid ${d.matched_exact ? "var(--pass)" : (d.device_type_slug === "unknown" ? "var(--major)" : "var(--minor)")}`,
            }}>
              <div style={{ fontSize: 12, fontWeight: 600 }}>{d.identity}</div>
              <div style={{ fontSize: 11, color: "var(--text-faint)" }}>{d.protocol} · <span className="mono">{d.ip}</span></div>
              <div style={{ marginTop: 4 }}>
                <span className={`badge badge-${d.matched_exact ? "passed" : "minor"}`}
                      style={{ fontSize: 9, padding: "1px 6px" }}>
                  {d.device_type_slug}
                </span>
              </div>
            </div>
          ))}
        </div>
      </div>
    </>
  );
}
