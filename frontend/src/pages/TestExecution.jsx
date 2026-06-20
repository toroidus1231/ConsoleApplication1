// TestExecution (Contracts §6.4 "/tests").
// GET /tests/history?device_id=&status=  +  test_started/completed/aborted SSE.
// Also renders the power dependency graph color-coded by latest test status
// (§5.1) using the device list.
import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useApi } from "../hooks/useApi.js";
import { useFacility } from "../context/FacilityContext.jsx";
import PowerDepGraph from "../components/PowerDepGraph.jsx";
import {
  Card,
  DataTable,
  StatusPill,
  Loading,
  ErrorState,
  EmptyState,
  fmtTime,
} from "../components/ui.jsx";

const STATUSES = [
  "",
  "passed",
  "failed",
  "aborted",
  "restore_failure",
  "precondition_failed",
  "manual_pending",
];

export default function TestExecution() {
  const api = useApi();
  const { sse } = useFacility();
  const [statusFilter, setStatusFilter] = useState("");
  const [deviceFilter, setDeviceFilter] = useState("");
  const [tests, setTests] = useState(null);
  const [devices, setDevices] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  // Live status overrides keyed by test_id from SSE.
  const [liveStatus, setLiveStatus] = useState({});

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      const params = new URLSearchParams();
      if (statusFilter) params.set("status", statusFilter);
      if (deviceFilter) params.set("device_id", deviceFilter);
      const qs = params.toString();
      const data = await api.get(`/tests/history${qs ? `?${qs}` : ""}`);
      setTests(data.tests || []);
    } catch (err) {
      setError(err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [statusFilter, deviceFilter]);

  // Load devices once for the power graph.
  useEffect(() => {
    api
      .get("/devices")
      .then((d) => setDevices(d.devices || []))
      .catch(() => setDevices([]));
  }, [api]);

  // Wire live test lifecycle events (§6.2).
  useEffect(() => {
    const offStart = sse.subscribe("test_started", (d) =>
      setLiveStatus((p) => ({ ...p, [d.test_id]: "running" }))
    );
    const offDone = sse.subscribe("test_completed", (d) => {
      setLiveStatus((p) => ({ ...p, [d.test_id]: d.status || "passed" }));
      load();
    });
    const offAbort = sse.subscribe("test_aborted", (d) => {
      setLiveStatus((p) => ({ ...p, [d.test_id]: "aborted" }));
      load();
    });
    return () => {
      offStart();
      offDone();
      offAbort();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sse]);

  const deviceOptions = useMemo(() => {
    const map = new Map();
    devices.forEach((d) => map.set(d.device_id, d.name || d.device_id));
    (tests || []).forEach((t) => {
      if (!map.has(t.device_id)) map.set(t.device_id, t.device_id);
    });
    return [...map.entries()];
  }, [devices, tests]);

  // Latest status per device for the power graph coloring.
  const statusByDevice = useMemo(() => {
    const m = {};
    (tests || []).forEach((t) => {
      // tests are typically newest-first; keep first seen per device.
      if (!(t.device_id in m)) m[t.device_id] = liveStatus[t.test_id] || t.status;
    });
    Object.entries(liveStatus).forEach(([, s]) => void s);
    return m;
  }, [tests, liveStatus]);

  const rows = useMemo(
    () =>
      (tests || []).map((t) => ({
        ...t,
        _status: liveStatus[t.test_id] || t.status,
      })),
    [tests, liveStatus]
  );

  return (
    <div className="page">
      <header className="page-head">
        <h1>Test Execution</h1>
        <p className="page-sub">
          Active test history and live execution. Conflicting power chains are serialized (§5.1).
        </p>
      </header>

      <Card title="Power Dependency Graph">
        <PowerDepGraph devices={devices} statusByDevice={statusByDevice} />
      </Card>

      <Card>
        <div className="form-row">
          <select className="input" value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}>
            {STATUSES.map((s) => (
              <option key={s} value={s}>{s ? s.replace(/_/g, " ") : "All statuses"}</option>
            ))}
          </select>
          <select className="input grow" value={deviceFilter} onChange={(e) => setDeviceFilter(e.target.value)}>
            <option value="">All devices</option>
            {deviceOptions.map(([id, name]) => (
              <option key={id} value={id}>{name}</option>
            ))}
          </select>
          <button className="btn btn-ghost" onClick={load}>Refresh</button>
        </div>
      </Card>

      <Card title="Test History">
        {loading && <Loading label="Loading tests…" />}
        {error && <ErrorState error={error} onRetry={load} />}
        {!loading && !error && (
          <DataTable
            columns={[
              {
                key: "test_name",
                header: "Test",
                render: (t) => (
                  <Link to={`/tests/${t.test_id}`} className="link">
                    {t.test_name || t.test_id}
                  </Link>
                ),
              },
              {
                key: "device_id",
                header: "Device",
                render: (t) => (
                  <Link to={`/devices/${t.device_id}`} className="link">
                    {t.device_id}
                  </Link>
                ),
              },
              { key: "_status", header: "Status", render: (t) => <StatusPill value={t._status} /> },
              { key: "started_at", header: "Started", sortable: true, render: (t) => fmtTime(t.started_at) },
              {
                key: "duration_seconds",
                header: "Duration",
                render: (t) => (t.duration_seconds != null ? `${Number(t.duration_seconds).toFixed(1)}s` : "—"),
              },
            ]}
            rows={rows}
            rowKey={(t) => t.test_id}
            empty={<EmptyState title="No tests" detail="Run a test from a device's detail page." />}
          />
        )}
      </Card>
    </div>
  );
}
