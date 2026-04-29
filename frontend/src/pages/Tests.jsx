import React, { useEffect, useState } from "react";
import { api } from "../hooks/useApi";
import { useSSE } from "../hooks/useSSE";

// Module 18: test execution view. Supports launching a test, viewing live
// status updates from SSE, and confirming manual_pending tests.
export default function Tests() {
  const [device, setDevice] = useState("");
  const [testName, setTestName] = useState("");
  const [running, setRunning] = useState({});  // test_id -> last status from SSE
  const [error, setError] = useState("");
  const { events } = useSSE();

  // Reduce SSE events into running map.
  useEffect(() => {
    const next = { ...running };
    for (const e of events) {
      const id = e.data?.test_id;
      if (!id) continue;
      if (e.type === "test_started") next[id] = "running";
      else if (e.type === "test_completed") next[id] = e.data.status || "completed";
      else if (e.type === "test_aborted") next[id] = "aborted";
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

  const confirm = async (id) => {
    try {
      await api.post(`/tests/confirm/${id}`, { confirmed_by: "engineer" });
      setRunning({ ...running, [id]: "running" });
    } catch (e) { setError(String(e)); }
  };

  return (
    <>
      <h1>Active Tests</h1>
      <div className="card">
        <input placeholder="device_id" value={device} onChange={(e) => setDevice(e.target.value)} />
        <input placeholder="test_name" value={testName} onChange={(e) => setTestName(e.target.value)} />
        <button onClick={launch} disabled={!device || !testName}>Run</button>
        {error && <p className="severity-critical">{error}</p>}
      </div>

      <table>
        <thead><tr><th>Test ID</th><th>Status</th><th>Action</th></tr></thead>
        <tbody>
          {Object.entries(running).map(([id, status]) => (
            <tr key={id}>
              <td><code>{id}</code></td>
              <td><span className={`status-${status}`}>{status}</span></td>
              <td>
                {status === "manual_pending" && (
                  <button onClick={() => confirm(id)}>Confirm</button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}
