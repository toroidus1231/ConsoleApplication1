import React, { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../hooks/useApi";
import { useSSE } from "../hooks/useSSE";

export default function Tests() {
  const [device, setDevice] = useState("ups-A");
  const [testName, setTestName] = useState("ups_battery_transfer");
  const [running, setRunning] = useState({});
  const [history, setHistory] = useState([]);
  const [error, setError] = useState("");
  const [allDevices, setAllDevices] = useState([]);
  const [activeTests, setActiveTests] = useState([]);
  const [activeTestsLoading, setActiveTestsLoading] = useState(false);
  const { events } = useSSE();

  useEffect(() => {
    api.get("/tests/history").then((r) => setHistory(r.tests || [])).catch(() => {});
    api.get("/devices").then((r) => setAllDevices(r.devices || [])).catch(() => {});
    const t = setInterval(() => {
      api.get("/tests/history").then((r) => setHistory(r.tests || [])).catch(() => {});
    }, 4000);
    return () => clearInterval(t);
  }, []);

  // Whenever device changes, fetch its active_tests so the test_name
  // dropdown only offers tests the platform actually knows how to run.
  useEffect(() => {
    if (!device) { setActiveTests([]); return; }
    setActiveTestsLoading(true);
    api.get(`/devices/${encodeURIComponent(device)}/active_tests`)
      .then((r) => {
        setActiveTests(r.active_tests || []);
        if (r.active_tests?.length && !r.active_tests.find(t => t.name === testName)) {
          setTestName(r.active_tests[0].name);
        }
      })
      .catch(() => setActiveTests([]))
      .finally(() => setActiveTestsLoading(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [device]);

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
        <div className="card-body" style={{ display: "grid",
              gridTemplateColumns: "1fr 1fr auto", gap: 12, alignItems: "end" }}>
          <div>
            <div className="label" style={{ marginBottom: 4 }}>Device</div>
            <input list="device-options" placeholder="device_id"
                   value={device}
                   onChange={(e) => setDevice(e.target.value)}
                   style={{ width: "100%" }} />
            <datalist id="device-options">
              {allDevices.map((d) => (
                <option key={d.device_id} value={d.device_id}>
                  {d.name} · {d.device_type_slug}
                </option>
              ))}
            </datalist>
          </div>
          <div>
            <div className="label" style={{ marginBottom: 4 }}>
              Test name {activeTestsLoading && <span style={{ color: "var(--text-faint)" }}>· loading…</span>}
            </div>
            {activeTests.length > 0 ? (
              <select value={testName} onChange={(e) => setTestName(e.target.value)}
                      style={{ width: "100%" }}>
                {activeTests.map((t) => (
                  <option key={t.name} value={t.name}>
                    {t.name} ({t.type})
                  </option>
                ))}
              </select>
            ) : (
              <input placeholder="test_name" value={testName}
                     onChange={(e) => setTestName(e.target.value)}
                     style={{ width: "100%" }} />
            )}
          </div>
          <button onClick={launch} disabled={!device || !testName}>Run</button>
        </div>
        {activeTests.length > 0 && (
          <div className="card-body" style={{ borderTop: "1px solid var(--border-subtle)",
                paddingTop: 8, color: "var(--text-tertiary)", fontSize: 11,
                fontFamily: "var(--font-mono)" }}>
            {(() => {
              const t = activeTests.find((x) => x.name === testName);
              if (!t) return null;
              return (
                <>
                  <span>{t.spec_reference || ""}</span>
                  {t.instrument?.vendor && (
                    <span style={{ marginLeft: 12 }}>
                      → {t.instrument.vendor} {t.instrument.model}
                    </span>
                  )}
                </>
              );
            })()}
          </div>
        )}
        {error && <p style={{ color: "var(--fail)", padding: 8 }}>{error}</p>}
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
