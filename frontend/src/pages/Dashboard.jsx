import React, { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../hooks/useApi";
import { useSSE } from "../hooks/useSSE";

export default function Dashboard() {
  const [health, setHealth] = useState({});
  const [summary, setSummary] = useState({ total: 0, by_severity: {}, by_category: {} });
  const [tests, setTests] = useState([]);
  const [devices, setDevices] = useState([]);
  const { events, connected } = useSSE();

  useEffect(() => {
    const tick = async () => {
      try {
        setHealth(await api.get("/system/health"));
        setSummary(await api.get("/punchlist/summary"));
        setTests((await api.get("/tests/history")).tests || []);
        setDevices((await api.get("/devices")).devices || []);
      } catch (_) {}
    };
    tick();
    const t = setInterval(tick, 5000);
    return () => clearInterval(t);
  }, []);

  const testCounts = useMemo(() => {
    const c = { passed: 0, failed: 0, aborted: 0 };
    for (const t of tests) c[t.status] = (c[t.status] || 0) + 1;
    return c;
  }, [tests]);

  const deviceCounts = useMemo(() => {
    const c = {};
    for (const d of devices) c[d.test_status || "pending"] = (c[d.test_status || "pending"] || 0) + 1;
    return c;
  }, [devices]);

  return (
    <>
      <div className="page-header">
        <h1>Facility Overview</h1>
        <span className="crumb">DC1-Ashburn · live commissioning dashboard</span>
      </div>

      <div className="tile-row">
        <div className="tile"><div className="label">Devices</div>
          <div className="value">{devices.length}</div>
          <div style={{ fontSize: 11, color: "var(--text-faint)", marginTop: 4 }}>
            {deviceCounts.passed || 0} passed · {(deviceCounts.failed || 0) + (deviceCounts.aborted || 0)} failed
          </div>
        </div>
        <div className="tile"><div className="label">Punch list</div>
          <div className="value">{summary.total}</div>
          <div style={{ fontSize: 11, color: "var(--text-faint)", marginTop: 4 }}>
            <span className="severity-critical">{summary.by_severity?.critical || 0} critical</span>
            {" · "}{summary.by_severity?.major || 0} major
          </div>
        </div>
        <div className="tile"><div className="label">Tests run</div>
          <div className="value">{tests.length}</div>
          <div style={{ fontSize: 11, color: "var(--text-faint)", marginTop: 4 }}>
            {testCounts.passed} passed · {testCounts.failed + testCounts.aborted} failed
          </div>
        </div>
        <div className="tile"><div className="label">SSE</div>
          <div className="value" style={{ color: connected ? "var(--pass)" : "var(--fail)" }}>
            {connected ? "live" : "down"}
          </div>
          <div style={{ fontSize: 11, color: "var(--text-faint)", marginTop: 4 }}>
            {events.length} events buffered
          </div>
        </div>
      </div>

      <div className="row">
        <div className="card grow">
          <div className="card-h">System health</div>
          <table>
            <tbody>
              {Object.entries(health).map(([k, v]) => (
                <tr key={k}>
                  <td>{k}</td>
                  <td><span className={`badge badge-${v === "ok" ? "passed" : "failed"}`}>{v}</span></td>
                </tr>
              ))}
            </tbody>
          </table>
          <div style={{ marginTop: 8, fontSize: 11, color: "var(--text-faint)" }}>
            <Link to="/power">Power DAG →</Link>{"  ·  "}
            <Link to="/timeline">Timeline →</Link>{"  ·  "}
            <Link to="/attestation">Attestation →</Link>
          </div>
        </div>

        <div className="card grow">
          <div className="card-h">Punch list by severity</div>
          <SeverityBars counts={summary.by_severity || {}} total={summary.total} />
          <div style={{ marginTop: 12, fontSize: 11, color: "var(--text-faint)" }}>
            <Link to="/punchlist">All items →</Link>
          </div>
        </div>
      </div>

      <div className="card">
        <div className="card-h">Live events {connected ? "🟢" : "🔴"}</div>
        <table>
          <thead><tr><th style={{ width: 90 }}>Time</th><th style={{ width: 180 }}>Type</th><th>Data</th></tr></thead>
          <tbody>
            {events.slice().reverse().slice(0, 15).map((e, i) => (
              <tr key={i}>
                <td className="mono">{new Date(e.ts).toLocaleTimeString()}</td>
                <td><span className="badge badge-info">{e.type}</span></td>
                <td className="mono" style={{ fontSize: 11, color: "var(--text-dim)" }}>
                  {JSON.stringify(e.data).slice(0, 200)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

function SeverityBars({ counts, total }) {
  const order = [
    { key: "critical", color: "var(--critical)" },
    { key: "major", color: "var(--major)" },
    { key: "minor", color: "var(--minor)" },
    { key: "info", color: "var(--info)" },
  ];
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      {order.map(({ key, color }) => {
        const n = counts[key] || 0;
        const pct = total > 0 ? (n / total) * 100 : 0;
        return (
          <div key={key} style={{ display: "grid", gridTemplateColumns: "80px 1fr 40px", gap: 8, alignItems: "center" }}>
            <div style={{ textTransform: "uppercase", fontSize: 10.5, color: "var(--text-faint)" }}>{key}</div>
            <div style={{ background: "var(--bg-2)", height: 14, borderRadius: 3, overflow: "hidden" }}>
              <div style={{ width: `${pct}%`, height: "100%", background: color, transition: "width 0.4s ease" }} />
            </div>
            <div style={{ textAlign: "right", fontVariantNumeric: "tabular-nums" }}>{n}</div>
          </div>
        );
      })}
    </div>
  );
}
