import React, { useEffect, useState } from "react";
import { api } from "../hooks/useApi";
import { useSSE } from "../hooks/useSSE";

// Module 18: Dashboard.
//
// Shows top-line health (NetBox/Influx/MinIO/orchestrator), punch list
// summary, and a live event feed coming off /api/v1/system/events.
export default function Dashboard() {
  const [health, setHealth] = useState({});
  const [summary, setSummary] = useState({ total: 0, by_severity: {}, by_category: {} });
  const { events, connected } = useSSE();

  useEffect(() => {
    api.get("/system/health").then(setHealth).catch(() => {});
    api.get("/punchlist/summary").then(setSummary).catch(() => {});
    const t = setInterval(() => {
      api.get("/punchlist/summary").then(setSummary).catch(() => {});
    }, 10000);
    return () => clearInterval(t);
  }, []);

  return (
    <>
      <h1>Facility Overview</h1>
      <div>
        <span className="summary-tile">
          <div className="count">{summary.total}</div>
          <div>punch items</div>
        </span>
        {["critical", "major", "minor", "info"].map((s) => (
          <span key={s} className="summary-tile">
            <div className={`count severity-${s}`}>{summary.by_severity?.[s] || 0}</div>
            <div>{s}</div>
          </span>
        ))}
      </div>

      <section className="card">
        <strong>System health</strong>
        <ul>
          {Object.entries(health).map(([k, v]) => (
            <li key={k}>{k}: {v}</li>
          ))}
        </ul>
      </section>

      <section className="card">
        <strong>Live events</strong> {connected ? "🟢" : "🔴"}
        <table>
          <thead><tr><th>Time</th><th>Type</th><th>Data</th></tr></thead>
          <tbody>
            {events.slice().reverse().slice(0, 30).map((e, i) => (
              <tr key={i}>
                <td>{new Date(e.ts).toLocaleTimeString()}</td>
                <td>{e.type}</td>
                <td><code>{JSON.stringify(e.data)}</code></td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </>
  );
}
