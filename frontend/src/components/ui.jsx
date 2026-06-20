// Shared UI primitives used across every page for a consistent look:
// StatusPill, Card, DataTable, and the standard loading/error/empty states.
import { useMemo, useState } from "react";

// ---- Status / severity pills -------------------------------------------------

// Map an arbitrary status or severity string to a pill color class.
// Covers PunchListItem.severity (critical|major|minor|info),
// PunchListItem.status (open|acknowledged|resolved|deferred),
// TestResult.status (passed|failed|aborted|restore_failure|precondition_failed|
// manual_pending|queued|running) and health (ok|error).
const PILL_CLASS = {
  // severity
  critical: "pill-critical",
  major: "pill-major",
  minor: "pill-minor",
  info: "pill-info",
  // punch status
  open: "pill-major",
  acknowledged: "pill-info",
  resolved: "pill-ok",
  deferred: "pill-muted",
  // test status
  passed: "pill-ok",
  failed: "pill-critical",
  aborted: "pill-critical",
  restore_failure: "pill-critical",
  precondition_failed: "pill-major",
  manual_pending: "pill-warn",
  manual_confirmation: "pill-warn",
  queued: "pill-info",
  running: "pill-warn",
  // health / device
  ok: "pill-ok",
  error: "pill-critical",
  unreachable: "pill-critical",
  reachable: "pill-ok",
  // generic
  completed: "pill-ok",
  confirmed: "pill-ok",
};

export function StatusPill({ value, label }) {
  const key = String(value ?? "").toLowerCase();
  const cls = PILL_CLASS[key] || "pill-muted";
  const text = label ?? (value === undefined || value === null || value === "" ? "—" : String(value));
  return <span className={`pill ${cls}`}>{text.replace(/_/g, " ")}</span>;
}

// ---- Card --------------------------------------------------------------------

export function Card({ title, actions, children, className = "", footer }) {
  return (
    <section className={`card ${className}`}>
      {(title || actions) && (
        <header className="card-head">
          {title && <h3 className="card-title">{title}</h3>}
          {actions && <div className="card-actions">{actions}</div>}
        </header>
      )}
      <div className="card-body">{children}</div>
      {footer && <footer className="card-foot">{footer}</footer>}
    </section>
  );
}

// A small labeled metric tile, used on the dashboard.
export function Stat({ label, value, tone }) {
  return (
    <div className={`stat ${tone ? `stat-${tone}` : ""}`}>
      <div className="stat-value">{value}</div>
      <div className="stat-label">{label}</div>
    </div>
  );
}

// ---- Standard async states ---------------------------------------------------

export function Loading({ label = "Loading…" }) {
  return (
    <div className="state state-loading">
      <span className="spinner" aria-hidden="true" />
      <span>{label}</span>
    </div>
  );
}

export function ErrorState({ error, onRetry }) {
  const message = typeof error === "string" ? error : error?.message || "Request failed";
  return (
    <div className="state state-error">
      <div className="state-icon">!</div>
      <div>
        <div className="state-title">Something went wrong</div>
        <div className="state-detail">{message}</div>
      </div>
      {onRetry && (
        <button className="btn btn-ghost" onClick={onRetry}>
          Retry
        </button>
      )}
    </div>
  );
}

export function EmptyState({ title = "Nothing here yet", detail, action }) {
  return (
    <div className="state state-empty">
      <div className="state-title">{title}</div>
      {detail && <div className="state-detail">{detail}</div>}
      {action}
    </div>
  );
}

// ---- DataTable ---------------------------------------------------------------

// columns: [{ key, header, render?(row), sortable?, width? }]
// Generic, sortable, with built-in empty handling.
export function DataTable({ columns, rows, rowKey, onRowClick, empty }) {
  const [sort, setSort] = useState({ key: null, dir: 1 });

  const sorted = useMemo(() => {
    if (!sort.key) return rows;
    const col = columns.find((c) => c.key === sort.key);
    if (!col) return rows;
    const get = (r) => (col.sortValue ? col.sortValue(r) : r[sort.key]);
    return [...rows].sort((a, b) => {
      const av = get(a);
      const bv = get(b);
      if (av == null) return 1;
      if (bv == null) return -1;
      if (av < bv) return -1 * sort.dir;
      if (av > bv) return 1 * sort.dir;
      return 0;
    });
  }, [rows, sort, columns]);

  const toggleSort = (key) =>
    setSort((s) => (s.key === key ? { key, dir: -s.dir } : { key, dir: 1 }));

  if (!rows || rows.length === 0) {
    return empty || <EmptyState />;
  }

  return (
    <div className="table-wrap">
      <table className="data-table">
        <thead>
          <tr>
            {columns.map((c) => (
              <th
                key={c.key}
                style={c.width ? { width: c.width } : undefined}
                className={c.sortable ? "sortable" : undefined}
                onClick={c.sortable ? () => toggleSort(c.key) : undefined}
              >
                {c.header}
                {c.sortable && sort.key === c.key && (
                  <span className="sort-arrow">{sort.dir === 1 ? " ▲" : " ▼"}</span>
                )}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {sorted.map((row, i) => {
            const key = rowKey ? rowKey(row, i) : i;
            return (
              <tr
                key={key}
                className={onRowClick ? "clickable" : undefined}
                onClick={onRowClick ? () => onRowClick(row) : undefined}
              >
                {columns.map((c) => (
                  <td key={c.key} data-label={c.header}>
                    {c.render ? c.render(row) : formatCell(row[c.key])}
                  </td>
                ))}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function formatCell(v) {
  if (v === undefined || v === null || v === "") return "—";
  if (typeof v === "number") return Number.isInteger(v) ? v : v.toFixed(3);
  if (typeof v === "boolean") return v ? "yes" : "no";
  return String(v);
}

// ---- misc helpers ------------------------------------------------------------

// Format an ISO timestamp to the browser's local time (Contracts §12:
// backend is UTC, browser converts for display only).
export function fmtTime(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return String(iso);
  return d.toLocaleString();
}

export function fmtNumber(v, digits = 3) {
  if (v === undefined || v === null || v === "") return "—";
  const n = Number(v);
  if (Number.isNaN(n)) return String(v);
  return Number.isInteger(n) ? String(n) : n.toFixed(digits);
}
