// PunchList (Contracts §6.4 "/punchlist").
// GET /punchlist?severity=&category=&status=  +  PATCH /punchlist/{id}
// +  punch_item SSE (new items appended live, §6.2).
// Clicking a row opens a detail drawer with the attestation evidence link.
import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useApi } from "../hooks/useApi.js";
import { useFacility } from "../context/FacilityContext.jsx";
import {
  Card,
  DataTable,
  StatusPill,
  Loading,
  ErrorState,
  EmptyState,
  fmtTime,
} from "../components/ui.jsx";

const SEVERITIES = ["", "critical", "major", "minor", "info"];
const CATEGORIES = [
  "",
  "identity",
  "firmware",
  "network",
  "power",
  "cooling",
  "security",
  "safety",
  "test_failure",
  "physical",
];
const STATUSES = ["", "open", "acknowledged", "resolved", "deferred"];

export default function PunchList() {
  const api = useApi();
  const { sse } = useFacility();
  const [severity, setSeverity] = useState("");
  const [category, setCategory] = useState("");
  const [status, setStatus] = useState("");
  const [items, setItems] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [selected, setSelected] = useState(null);
  const [updating, setUpdating] = useState(false);

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      const params = new URLSearchParams();
      if (severity) params.set("severity", severity);
      if (category) params.set("category", category);
      if (status) params.set("status", status);
      const qs = params.toString();
      const data = await api.get(`/punchlist${qs ? `?${qs}` : ""}`);
      setItems(data.items || []);
    } catch (err) {
      setError(err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [severity, category, status]);

  // Live punch_item events: prepend if it matches current filters (§6.2).
  useEffect(() => {
    return sse.subscribe("punch_item", (data) => {
      const matches =
        (!severity || data.severity === severity) &&
        (!category || data.category === category) &&
        (!status || (data.status || "open") === status);
      if (!matches) return;
      setItems((prev) => {
        if (!prev) return [data];
        if (prev.some((i) => i.id === data.id)) return prev;
        return [data, ...prev];
      });
    });
  }, [sse, severity, category, status]);

  const updateStatus = async (item, newStatus) => {
    setUpdating(true);
    try {
      const resp = await api.patch(`/punchlist/${item.id}`, {
        status: newStatus,
        resolved_by: "engineer",
      });
      const updated = resp.item || { ...item, status: newStatus };
      setItems((prev) => (prev ? prev.map((i) => (i.id === item.id ? updated : i)) : prev));
      setSelected((s) => (s && s.id === item.id ? updated : s));
    } catch (err) {
      setError(err);
    } finally {
      setUpdating(false);
    }
  };

  const counts = useMemo(() => {
    const c = { critical: 0, major: 0, minor: 0, info: 0 };
    (items || []).forEach((i) => {
      if (c[i.severity] != null) c[i.severity] += 1;
    });
    return c;
  }, [items]);

  return (
    <div className="page">
      <header className="page-head">
        <h1>Punch List</h1>
        <p className="page-sub">
          Commissioning defects from discovery, reconciliation, active tests, and physical checks.
        </p>
      </header>

      <Card>
        <div className="form-row">
          <select className="input" value={severity} onChange={(e) => setSeverity(e.target.value)}>
            {SEVERITIES.map((s) => (
              <option key={s} value={s}>{s ? s : "All severities"}</option>
            ))}
          </select>
          <select className="input" value={category} onChange={(e) => setCategory(e.target.value)}>
            {CATEGORIES.map((c) => (
              <option key={c} value={c}>{c ? c.replace(/_/g, " ") : "All categories"}</option>
            ))}
          </select>
          <select className="input" value={status} onChange={(e) => setStatus(e.target.value)}>
            {STATUSES.map((s) => (
              <option key={s} value={s}>{s ? s : "All statuses"}</option>
            ))}
          </select>
          <div className="spacer" />
          <div className="chip-row tight">
            <span className="chip pill-critical">{counts.critical} critical</span>
            <span className="chip pill-major">{counts.major} major</span>
            <span className="chip pill-minor">{counts.minor} minor</span>
          </div>
          <button className="btn btn-ghost" onClick={load}>Refresh</button>
        </div>
      </Card>

      <div className={selected ? "split" : ""}>
        <Card title={`Items${items ? ` (${items.length})` : ""}`} className="split-main">
          {loading && <Loading label="Loading punch list…" />}
          {error && <ErrorState error={error} onRetry={load} />}
          {!loading && !error && (
            <DataTable
              columns={[
                { key: "severity", header: "Severity", sortable: true, render: (i) => <StatusPill value={i.severity} /> },
                { key: "category", header: "Category", sortable: true },
                {
                  key: "device_name",
                  header: "Device",
                  render: (i) =>
                    i.device_id ? (
                      <Link to={`/devices/${i.device_id}`} className="link" onClick={(e) => e.stopPropagation()}>
                        {i.device_name || i.device_id}
                      </Link>
                    ) : (
                      i.device_name || "—"
                    ),
                },
                { key: "expected", header: "Expected" },
                { key: "actual", header: "Actual" },
                { key: "status", header: "Status", sortable: true, render: (i) => <StatusPill value={i.status} /> },
              ]}
              rows={items}
              rowKey={(i) => i.id}
              onRowClick={(i) => setSelected(i)}
              empty={<EmptyState title="No punch items" detail="Nothing matches the current filters." />}
            />
          )}
        </Card>

        {selected && (
          <Card
            className="split-aside"
            title="Item detail"
            actions={<button className="btn btn-ghost btn-sm" onClick={() => setSelected(null)}>Close</button>}
          >
            <div className="kv">
              <span>Severity</span>
              <StatusPill value={selected.severity} />
            </div>
            <div className="kv"><span>Category</span><b>{selected.category}</b></div>
            <div className="kv"><span>Source</span><b>{selected.source || "—"}</b></div>
            <div className="kv"><span>Site / Rack</span><b>{selected.site} · {selected.rack}</b></div>
            <div className="kv"><span>Expected</span><b>{selected.expected || "—"}</b></div>
            <div className="kv"><span>Actual</span><b>{selected.actual || "—"}</b></div>
            <div className="kv col"><span>Remediation</span><p>{selected.remediation || "—"}</p></div>

            <div className="kv col">
              <span>Evidence</span>
              {selected.evidence_hash ? (
                <Link to={`/attestation?hash=${selected.evidence_hash}`} className="mono link">
                  {selected.evidence_hash}
                </Link>
              ) : (
                <span className="muted">No attestation evidence</span>
              )}
            </div>
            {selected.test_id && (
              <div className="kv">
                <span>Test</span>
                <Link to={`/tests/${selected.test_id}`} className="link">{selected.test_id}</Link>
              </div>
            )}
            {selected.resolved_by && (
              <div className="kv"><span>Resolved by</span><b>{selected.resolved_by} · {fmtTime(selected.resolved_at)}</b></div>
            )}

            <div className="drawer-actions">
              {["acknowledged", "resolved", "deferred", "open"].map((s) => (
                <button
                  key={s}
                  className={`btn btn-sm ${s === "resolved" ? "btn-primary" : "btn-ghost"}`}
                  disabled={updating || selected.status === s}
                  onClick={() => updateStatus(selected, s)}
                >
                  {s}
                </button>
              ))}
            </div>
          </Card>
        )}
      </div>
    </div>
  );
}
