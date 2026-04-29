import React, { useEffect, useRef, useState } from "react";
import { useParams, Link } from "react-router-dom";
import {
  CartesianGrid, Legend, Line, LineChart, ReferenceLine,
  ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import { api } from "../hooks/useApi";

// Module 18: Live test view. Subscribes to /tests/live/{id} and plots
// every monitor[] register as a separate trace in real time. Abort
// thresholds are drawn as dashed reference lines so an operator can
// spot a margin-eroding test before the watchdog trips.

const COLORS = ["#58a6ff", "#56d364", "#f0a05c", "#bc8cff", "#79c0ff", "#ffd33d"];

const MAX_SAMPLES = 200;

export default function TestLive() {
  const { testId } = useParams();
  const [series, setSeries] = useState({}); // { register: [{ts, value}, ...] }
  const [meta, setMeta] = useState(null);
  const [connected, setConnected] = useState(false);
  const seriesRef = useRef(series);
  seriesRef.current = series;

  // Pull test metadata for header.
  useEffect(() => {
    api.get(`/tests/status/${testId}`).then(setMeta).catch(() => {});
    const t = setInterval(() => {
      api.get(`/tests/status/${testId}`).then(setMeta).catch(() => {});
    }, 3000);
    return () => clearInterval(t);
  }, [testId]);

  // SSE strip-chart feed.
  useEffect(() => {
    const url = `/api/v1/tests/live/${testId}`;
    const es = new EventSource(url);
    es.onopen = () => setConnected(true);
    es.onerror = () => setConnected(false);
    es.addEventListener("sample", (e) => {
      let payload;
      try { payload = JSON.parse(e.data); } catch (_) { return; }
      const { register, value, timestamp } = payload;
      const point = { ts: timestamp || new Date().toISOString(), value };
      setSeries((prev) => {
        const next = { ...prev };
        const arr = (next[register] || []).slice(-MAX_SAMPLES + 1);
        arr.push(point);
        next[register] = arr;
        return next;
      });
    });
    return () => es.close();
  }, [testId]);

  const registers = Object.keys(series);
  const merged = mergeForChart(series);

  return (
    <>
      <div className="page-header">
        <h1>Live Test</h1>
        <span className="crumb">
          <Link to="/tests" style={{ color: "var(--text-faint)" }}>Tests</Link>
          {" / "}
          <span className="mono">{testId}</span>
        </span>
        <span style={{ marginLeft: "auto" }} className="conn">
          <span className={"dot " + (connected ? "dot-up" : "dot-down")} />
          {connected ? "stream live" : "stream offline"}
        </span>
      </div>

      {meta && (
        <div className="tile-row">
          <div className="tile"><div className="label">Status</div>
            <div className="value"><span className={`badge badge-${meta.status || "pending"}`}>{meta.status || "—"}</span></div></div>
          <div className="tile"><div className="label">Device</div>
            <div className="value" style={{ fontSize: 14 }}>{meta.device_id}</div></div>
          <div className="tile"><div className="label">Test</div>
            <div className="value" style={{ fontSize: 14 }}>{meta.test_name}</div></div>
          <div className="tile"><div className="label">Duration</div>
            <div className="value" style={{ fontSize: 16 }}>{(meta.duration_seconds || 0).toFixed(2)}s</div></div>
        </div>
      )}

      <div className="card">
        <div className="card-h">
          Live Registers ({registers.length} traces, {merged.length} samples)
        </div>
        <div style={{ height: 360 }}>
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={merged} margin={{ top: 10, right: 30, left: 10, bottom: 10 }}>
              <CartesianGrid stroke="#2a3441" strokeDasharray="3 3" />
              <XAxis dataKey="ts" tick={{ fill: "#8b949e", fontSize: 11 }}
                     tickFormatter={(v) => v.slice(11, 19)} />
              <YAxis tick={{ fill: "#8b949e", fontSize: 11 }} />
              <Tooltip contentStyle={{ background: "#11161e", border: "1px solid #2a3441" }} />
              <Legend wrapperStyle={{ color: "#8b949e", fontSize: 11 }} />
              {registers.map((r, i) => (
                <Line key={r} type="monotone" dataKey={r}
                      stroke={COLORS[i % COLORS.length]} dot={false}
                      isAnimationActive={false} strokeWidth={1.6} />
              ))}
            </LineChart>
          </ResponsiveContainer>
        </div>
      </div>

      {meta?.acceptance_results?.length > 0 && (
        <div className="card">
          <div className="card-h">Acceptance Criteria</div>
          <table>
            <thead>
              <tr>
                <th>Register</th><th>Metric</th><th>Operator</th>
                <th>Expected</th><th>Actual</th><th>Result</th>
              </tr>
            </thead>
            <tbody>
              {meta.acceptance_results.map((a, i) => (
                <tr key={i}>
                  <td className="mono">{a.register}</td>
                  <td>{a.metric}</td>
                  <td>{a.operator}</td>
                  <td>{a.expected}</td>
                  <td>{a.actual?.toFixed?.(2) ?? a.actual}</td>
                  <td>
                    <span className={`badge badge-${a.passed ? "passed" : "failed"}`}>
                      {a.passed ? "passed" : "failed"}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}

function mergeForChart(series) {
  // Recharts wants an array of {ts, register1, register2, ...}. Merge by ts.
  const tsToRow = new Map();
  for (const [reg, points] of Object.entries(series)) {
    for (const p of points) {
      const row = tsToRow.get(p.ts) || { ts: p.ts };
      row[reg] = p.value;
      tsToRow.set(p.ts, row);
    }
  }
  return [...tsToRow.values()].sort((a, b) => a.ts.localeCompare(b.ts));
}
