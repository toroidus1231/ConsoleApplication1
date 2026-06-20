// Dashboard / FacilityOverview (Module 18, Contracts §6.4 "/").
// Data: GET /punchlist/summary + GET /system/health + live SSE counts.
import { Link } from "react-router-dom";
import { useFacility } from "../context/FacilityContext.jsx";
import { useFetch } from "../hooks/useFetch.js";
import {
  Card,
  Stat,
  StatusPill,
  Loading,
  ErrorState,
  DataTable,
  fmtTime,
} from "../components/ui.jsx";

const SEVERITIES = ["critical", "major", "minor", "info"];

function HealthRow({ name, value }) {
  return (
    <div className="health-row">
      <span className="health-name">{name}</span>
      <StatusPill value={value} />
    </div>
  );
}

export default function Dashboard() {
  const { health, healthError, refreshHealth, sse } = useFacility();
  const summary = useFetch("/punchlist/summary");

  const recentEvents = [...sse.events].slice(-12).reverse();

  return (
    <div className="page">
      <header className="page-head">
        <h1>Facility Overview</h1>
        <p className="page-sub">
          Live commissioning status — punch list, system health, and real-time events.
        </p>
      </header>

      {/* --- Punch list summary --- */}
      <Card
        title="Punch List"
        actions={<Link className="btn btn-ghost" to="/punchlist">Open punch list</Link>}
      >
        {summary.loading && <Loading />}
        {summary.error && <ErrorState error={summary.error} onRetry={summary.reload} />}
        {summary.data && (
          <>
            <div className="stat-row">
              <Stat label="Total open items" value={summary.data.total ?? 0} />
              {SEVERITIES.map((sev) => (
                <Stat
                  key={sev}
                  label={sev}
                  tone={sev}
                  value={summary.data.by_severity?.[sev] ?? 0}
                />
              ))}
            </div>
            {summary.data.by_category && (
              <div className="chip-row">
                {Object.entries(summary.data.by_category).map(([cat, n]) => (
                  <span className="chip" key={cat}>
                    {cat}: <strong>{n}</strong>
                  </span>
                ))}
              </div>
            )}
          </>
        )}
      </Card>

      <div className="grid-2">
        {/* --- System health --- */}
        <Card
          title="System Health"
          actions={
            <button className="btn btn-ghost" onClick={refreshHealth}>
              Refresh
            </button>
          }
        >
          {healthError && <ErrorState error={healthError} onRetry={refreshHealth} />}
          {!health && !healthError && <Loading label="Querying health…" />}
          {health && (
            <>
              <div className="health-section-label">Infrastructure</div>
              <HealthRow name="NetBox" value={health.netbox} />
              <HealthRow name="InfluxDB" value={health.influxdb} />
              <HealthRow name="MinIO" value={health.minio} />

              <div className="health-section-label">Protocol Workers</div>
              {health.workers && Object.keys(health.workers).length > 0 ? (
                <DataTable
                  columns={[
                    { key: "worker", header: "Worker" },
                    { key: "devices", header: "Devices" },
                    {
                      key: "errors",
                      header: "Errors",
                      render: (r) => (
                        <span className={r.errors > 0 ? "text-danger" : ""}>
                          {r.errors}
                        </span>
                      ),
                    },
                  ]}
                  rows={Object.entries(health.workers).map(([worker, w]) => ({
                    worker,
                    devices: w?.devices ?? 0,
                    errors: w?.errors ?? 0,
                  }))}
                  rowKey={(r) => r.worker}
                />
              ) : (
                <div className="muted">No worker telemetry.</div>
              )}
            </>
          )}
        </Card>

        {/* --- Live SSE feed + counters --- */}
        <Card
          title="Live Activity"
          actions={
            <span className={`badge ${sse.connected ? "badge-ok" : "badge-muted"}`}>
              {sse.connected ? "SSE connected" : "SSE offline"}
            </span>
          }
        >
          <div className="stat-row">
            <Stat label="Polls" value={sse.counts.poll_result || 0} />
            <Stat label="Tests started" value={sse.counts.test_started || 0} />
            <Stat label="Tests done" value={sse.counts.test_completed || 0} />
            <Stat label="Discovered" value={sse.counts.device_discovered || 0} />
            <Stat
              label="Punch items"
              tone="major"
              value={sse.counts.punch_item || 0}
            />
          </div>

          <div className="event-feed">
            {recentEvents.length === 0 && (
              <div className="muted">Waiting for events…</div>
            )}
            {recentEvents.map((e, i) => (
              <div className="event-line" key={`${e.ts}-${i}`}>
                <span className="event-time">{fmtTime(new Date(e.ts).toISOString())}</span>
                <StatusPill value={eventTone(e.type)} label={e.type.replace(/_/g, " ")} />
                <span className="event-detail">{describeEvent(e)}</span>
              </div>
            ))}
          </div>
        </Card>
      </div>
    </div>
  );
}

function eventTone(type) {
  if (type.includes("aborted") || type === "punch_item") return "critical";
  if (type === "manual_confirmation_needed") return "manual_pending";
  if (type === "test_completed") return "passed";
  return "info";
}

function describeEvent(e) {
  const d = e.data || {};
  switch (e.type) {
    case "poll_result":
      return `device ${d.device_id} — ${Object.keys(d.measurements || {}).length} registers`;
    case "test_started":
      return `test ${d.test_id} on ${d.device_id}`;
    case "test_completed":
      return `test ${d.test_id} → ${d.status}`;
    case "test_aborted":
      return `test ${d.test_id} aborted`;
    case "punch_item":
      return `${d.severity || ""} ${d.category || ""} on ${d.device_name || d.device_id || ""}`;
    case "worker_health":
      return `${d.device_id || ""} ${d.status || ""}`;
    case "device_discovered":
      return `${d.name || d.device_id || "device"}`;
    case "manual_confirmation_needed":
      return d.prompt || `test ${d.test_id}`;
    default:
      return JSON.stringify(d);
  }
}
