import React, { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../hooks/useApi";
import { useSSE } from "../hooks/useSSE";

export default function Tests() {
  const [device, setDevice] = useState("ups-A");
  const [testName, setTestName] = useState("ups_battery_transfer");
  const [running, setRunning] = useState({});       // test_id -> status
  const [history, setHistory] = useState([]);
  const [error, setError] = useState("");
  const { events } = useSSE();

  useEffect(() => {
    api.get("/tests/history").then((r) => setHistory(r.tests || [])).catch(() => {});
    const t = setInterval(() => {
      api.get("/tests/history").then((r) => setHistory(r.tests || [])).catch(() => {});
    }, 4000);
    return () => clearInterval(t);
  }, []);

  useEffect(() => {
    const next = { ...running };
    for (const e of events.slice(-50)) {
      const id = e.data?.test_id;
      if (!id) continue;
      if (e.type === "test_started")  next[id] = "running";
      else if (e.type === "test_completed") next[id] = e.data.status || "completed";
      else if (e.type === "test_aborted")   next[id] = "aborted";
      else if (e.type === "manual_confirmation_needed") next[id] = "manual_pending";
    }
    setRunning(next);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [events.length]);

  const launch = async () => {
    setError("");
    try {
      const r = await api.post("/tests/run", { device_id: device, test_name: testName });
      setRunning({ ...running, [r.test_id]: "queued" });
    } catch (e) { setError(String(e)); }
  };

  const counts = useMemo(() => {
    const c = { passed: 0, failed: 0, aborted: 0 };
    for (const t of history) c[t.status] = (c[t.status] || 0) + 1;
    return c;
  }, [history]);

  return (
    <>
      <div className="page-header">
        <h1>Active Tests</h1>
        <span className="crumb">{history.length} historical · {Object.keys(running).length} live</span>
      </div>

      <div className="tile-row">
        <div className="tile"><div className="label">Passed</div><div className="value pass">{counts.passed}</div></div>
        <div className="tile"><div className="label">Failed</div><div className="value crit">{counts.failed}</div></div>
        <div className="tile"><div className="label">Aborted</div><div className="value maj">{counts.aborted}</div></div>
        <div className="tile"><div className="label">Live</div><div className="value">{Object.keys(running).length}</div></div>
      </div>

      <div className="card">
        <div className="card-h">Launch test</div>
        <div className="row">
          <input placeholder="device_id" value={device} onChange={(e) => setDevice(e.target.value)} />
          <input placeholder="test_name" value={testName} onChange={(e) => setTestName(e.target.value)} />
          <button onClick={launch} disabled={!device || !testName}>Run</button>
        </div>
        {error && <p style={{ color: "var(--critical)" }}>{error}</p>}
      </div>

      {Object.keys(running).length > 0 && (
        <div className="card" style={{ padding: 0 }}>
          <div className="card-h" style={{ margin: 0, borderRadius: "8px 8px 0 0" }}>Live</div>
          <table>
            <thead><tr><th>Test ID</th><th>Status</th><th></th></tr></thead>
            <tbody>
              {Object.entries(running).map(([id, status]) => (
                <tr key={id}>
                  <td className="mono">{id}</td>
                  <td><span className={`badge badge-${status}`}>{status}</span></td>
                  <td>
                    <Link to={`/tests/${id}`}><button className="ghost">Live chart</button></Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div className="card" style={{ padding: 0 }}>
        <div className="card-h" style={{ margin: 0, borderRadius: "8px 8px 0 0" }}>History</div>
        <table>
          <thead><tr><th>Started</th><th>Device</th><th>Test</th><th>Status</th><th>Duration</th><th></th></tr></thead>
          <tbody>
            {history.slice().sort((a, b) => b.started_at.localeCompare(a.started_at)).map((t) => (
              <tr key={t.test_id}>
                <td className="mono">{t.started_at?.slice(0, 19)}</td>
                <td>{t.device_id}</td>
                <td>{t.test_name}</td>
                <td><span className={`badge badge-${t.status}`}>{t.status}</span></td>
                <td>{(t.duration_seconds || 0).toFixed(1)}s</td>
                <td><Link to={`/tests/${t.test_id}`}><button className="ghost">View</button></Link></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}
