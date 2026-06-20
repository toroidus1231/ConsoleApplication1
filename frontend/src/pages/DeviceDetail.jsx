// DeviceDetail (Contracts §6.4 "/devices/:id").
// GET /devices/:id  +  GET /devices/:id/history  +  poll_result SSE.
// Shows live register values, register history, available tests (run them),
// and prior test results.
import { useEffect, useMemo, useState } from "react";
import { useParams, Link, useNavigate } from "react-router-dom";
import { useApi } from "../hooks/useApi.js";
import { useFetch } from "../hooks/useFetch.js";
import LiveRegisters from "../components/LiveRegisters.jsx";
import HistoryChart from "../components/HistoryChart.jsx";
import {
  Card,
  StatusPill,
  DataTable,
  Loading,
  ErrorState,
  EmptyState,
  fmtTime,
} from "../components/ui.jsx";

export default function DeviceDetail() {
  const { id } = useParams();
  const api = useApi();
  const navigate = useNavigate();
  const { data, loading, error, reload } = useFetch(`/devices/${id}`, [id]);

  const [register, setRegister] = useState("");
  const [history, setHistory] = useState(null);
  const [histLoading, setHistLoading] = useState(false);
  const [histError, setHistError] = useState(null);
  const [running, setRunning] = useState(null);
  const [runError, setRunError] = useState(null);

  const device = data?.device;
  const lastPoll = data?.last_poll;
  const testsAvailable = data?.tests_available || [];
  const testsRun = data?.tests_run || [];

  // Register names: prefer last poll measurements, else Config Context.
  const registerNames = useMemo(() => {
    if (lastPoll?.measurements) return Object.keys(lastPoll.measurements);
    const ctx = device?.config_context || {};
    const regs = ctx.registers || ctx.oids || ctx.objects || [];
    return regs.map((r) => r.name).filter(Boolean);
  }, [lastPoll, device]);

  useEffect(() => {
    if (!register && registerNames.length) setRegister(registerNames[0]);
  }, [registerNames, register]);

  const loadHistory = async () => {
    if (!register) return;
    setHistLoading(true);
    setHistError(null);
    try {
      const to = new Date().toISOString().slice(0, 10);
      const fromDate = new Date(Date.now() - 7 * 86400000).toISOString().slice(0, 10);
      const res = await api.get(
        `/devices/${id}/history?register=${encodeURIComponent(register)}&from=${fromDate}&to=${to}`
      );
      setHistory(res.points || []);
    } catch (err) {
      setHistError(err.message);
    } finally {
      setHistLoading(false);
    }
  };

  const runTest = async (testName) => {
    setRunning(testName);
    setRunError(null);
    try {
      const res = await api.post("/tests/run", {
        device_id: id,
        test_name: testName,
      });
      if (res.test_id) navigate(`/tests/${res.test_id}`);
    } catch (err) {
      setRunError(err.message);
      setRunning(null);
    }
  };

  if (loading) return <div className="page"><Loading label="Loading device…" /></div>;
  if (error) return <div className="page"><ErrorState error={error} onRetry={reload} /></div>;
  if (!device) return <div className="page"><EmptyState title="Device not found" /></div>;

  return (
    <div className="page">
      <header className="page-head">
        <div className="breadcrumb">
          <Link to="/devices" className="link">Devices</Link> / {device.name || id}
        </div>
        <h1>{device.name || id}</h1>
        <div className="meta-row">
          <StatusPill value={device.protocol} />
          <span className="meta-item">{device.device_type_slug}</span>
          <span className="meta-item">{device.primary_ip}</span>
          <span className="meta-item">{device.site} · {device.rack} · U{device.position}</span>
        </div>
      </header>

      <div className="grid-2">
        <Card
          title="Live Registers"
          actions={lastPoll && <span className="muted small">poll {fmtTime(lastPoll.timestamp_ns ? new Date(lastPoll.timestamp_ns / 1e6).toISOString() : null)}</span>}
        >
          <LiveRegisters
            deviceId={id}
            initial={lastPoll?.measurements}
            initialTimestampNs={lastPoll?.timestamp_ns}
          />
          {lastPoll && lastPoll.success === false && (
            <div className="inline-error">
              Last poll reported errors: {Object.keys(lastPoll.errors || {}).join(", ") || "unknown"}
            </div>
          )}
        </Card>

        <Card
          title="Register History"
          actions={
            <div className="form-row tight">
              <select
                className="input"
                value={register}
                onChange={(e) => setRegister(e.target.value)}
              >
                {registerNames.length === 0 && <option value="">No registers</option>}
                {registerNames.map((n) => (
                  <option key={n} value={n}>{n}</option>
                ))}
              </select>
              <button className="btn btn-ghost" onClick={loadHistory} disabled={!register || histLoading}>
                {histLoading ? "Loading…" : "Load"}
              </button>
            </div>
          }
        >
          {histError && <ErrorState error={histError} onRetry={loadHistory} />}
          {!history && !histError && (
            <EmptyState title="Pick a register and load its history." />
          )}
          {history && <HistoryChart points={history} label={register} />}
        </Card>
      </div>

      <Card title="Active Tests">
        {runError && <ErrorState error={runError} />}
        {testsAvailable.length === 0 ? (
          <EmptyState
            title="No active tests defined"
            detail="Tests come from active_tests[] in this device type's Config Context."
          />
        ) : (
          <DataTable
            columns={[
              { key: "name", header: "Test", render: (t) => t.name || t },
              { key: "description", header: "Description", render: (t) => t.description || "—" },
              {
                key: "preconditions",
                header: "Preconditions",
                render: (t) => (Array.isArray(t.preconditions) ? t.preconditions.length : 0),
              },
              {
                key: "run",
                header: "",
                render: (t) => {
                  const name = t.name || t;
                  return (
                    <button
                      className="btn btn-primary btn-sm"
                      onClick={() => runTest(name)}
                      disabled={running === name}
                    >
                      {running === name ? "Queuing…" : "Run"}
                    </button>
                  );
                },
              },
            ]}
            rows={testsAvailable}
            rowKey={(t) => t.name || t}
          />
        )}
      </Card>

      <Card title="Test History">
        <DataTable
          columns={[
            {
              key: "test_name",
              header: "Test",
              render: (t) => (
                <Link to={`/tests/${t.test_id}`} className="link">
                  {t.test_name}
                </Link>
              ),
            },
            { key: "status", header: "Status", render: (t) => <StatusPill value={t.status} /> },
            { key: "started_at", header: "Started", render: (t) => fmtTime(t.started_at) },
            {
              key: "duration_seconds",
              header: "Duration",
              render: (t) => (t.duration_seconds != null ? `${t.duration_seconds.toFixed(1)}s` : "—"),
            },
          ]}
          rows={testsRun}
          rowKey={(t) => t.test_id}
          empty={<EmptyState title="No tests run on this device yet." />}
        />
      </Card>
    </div>
  );
}
