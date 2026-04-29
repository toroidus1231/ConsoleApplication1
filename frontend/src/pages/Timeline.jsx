import React, { useEffect, useMemo, useState } from "react";
import { api } from "../hooks/useApi";

// Module 18 (Test execution timeline): Gantt-style view of every test in
// /tests/history, one row per (device, test_name), bar position from
// started_at, length from duration_seconds. Color by status.

export default function Timeline() {
  const [tests, setTests] = useState([]);

  useEffect(() => {
    api.get("/tests/history").then((r) => setTests(r.tests || [])).catch(() => {});
  }, []);

  const layout = useMemo(() => {
    if (tests.length === 0) return null;
    const parsed = tests.map((t) => ({
      ...t,
      start: new Date(t.started_at).getTime(),
      end: new Date(t.completed_at || t.started_at).getTime() + (t.duration_seconds || 0) * 1000,
    }));
    const min = Math.min(...parsed.map((t) => t.start));
    const max = Math.max(...parsed.map((t) => t.end));
    const span = Math.max(1, max - min);
    return { rows: parsed, min, max, span };
  }, [tests]);

  return (
    <>
      <div className="page-header">
        <h1>Test Execution Timeline</h1>
        <span className="crumb">{tests.length} tests · spec §5.1 orchestrator schedule</span>
      </div>

      <div className="card">
        <div className="card-h">Schedule</div>
        {!layout && <p style={{ color: "var(--text-faint)" }}>No tests recorded yet.</p>}
        {layout && (
          <>
            <div style={{ display: "grid", gridTemplateColumns: "200px 1fr", gap: 8, marginBottom: 6,
                          color: "var(--text-faint)", fontSize: 11, textTransform: "uppercase",
                          letterSpacing: 0.6 }}>
              <div>Device · Test</div>
              <div style={{ display: "flex", justifyContent: "space-between" }}>
                <span>{new Date(layout.min).toLocaleString()}</span>
                <span>{new Date(layout.max).toLocaleString()}</span>
              </div>
            </div>
            <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
              {layout.rows
                .sort((a, b) => a.start - b.start)
                .map((t) => {
                  const left = ((t.start - layout.min) / layout.span) * 100;
                  const width = Math.max(0.4, ((t.end - t.start) / layout.span) * 100);
                  return (
                    <div className="gantt-row" key={t.test_id}>
                      <div style={{ fontSize: 11, color: "var(--text-dim)", overflow: "hidden",
                                    textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                        <span style={{ color: "var(--text)" }}>{t.device_id}</span>
                        <span style={{ color: "var(--text-faint)" }}> · {t.test_name}</span>
                      </div>
                      <div className="gantt-track">
                        <div className={`gantt-bar ${t.status}`}
                             style={{ left: `${left}%`, width: `${width}%` }}
                             title={`${t.test_name}\n${t.status}\n${t.duration_seconds.toFixed(1)}s`}>
                          {t.status}
                        </div>
                      </div>
                    </div>
                  );
                })}
            </div>
          </>
        )}
      </div>
    </>
  );
}
