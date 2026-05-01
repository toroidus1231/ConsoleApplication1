import React, { useEffect, useState } from "react";
import { useParams, Link } from "react-router-dom";
import { api } from "../hooks/useApi";

// ATS transfer-test commissioning console (ASCO 7000).
// Per spec §4.3: must verify EVERY downstream UPS stays online during
// the simulated utility loss; one unhealthy UPS = outage.
//
// Shows:
//   - Sequence-of-operations table with t-offset and pass/fail per step
//   - Downstream UPS verification matrix (this is what catches outages
//     before they happen — spec §4.3 says "ANY downstream != online")
//   - Overall pass/fail badge

export default function AtsConsole() {
  const { id } = useParams();
  const [rec, setRec] = useState(null);

  useEffect(() => {
    api.get(`/equipment/ats/${id}`).then(setRec).catch(() => {});
  }, [id]);

  if (!rec) return <p style={{ color: "var(--text-faint)" }}>Loading…</p>;

  return (
    <>
      <div className="page-header">
        <h1>ATS · {id.toUpperCase()}</h1>
        <span className="crumb">
          <Link to="/sld" style={{ color: "var(--text-faint)" }}>SLD</Link>
          {" / "}{rec.manufacturer} {rec.model} · {rec.rated_amps}A
        </span>
        <span className={`badge badge-${rec.overall_passed ? "passed" : "failed"}`}
              style={{ marginLeft: "auto" }}>
          {rec.overall_passed ? "TRANSFER TEST PASSED" : "TRANSFER TEST FAILED"}
        </span>
      </div>

      {/* Sequence of operations */}
      <div className="card" style={{ padding: 0 }}>
        <div className="card-h">Sequence of Operations · last transfer test</div>
        <table>
          <thead>
            <tr>
              <th style={{ width: 40 }}>#</th>
              <th style={{ width: 110 }}>t-offset</th>
              <th>Step</th>
              <th>Expected</th>
              <th>Actual</th>
              <th style={{ width: 90 }}>Status</th>
            </tr>
          </thead>
          <tbody>
            {rec.sequence.map((s, i) => (
              <tr key={i}>
                <td className="mono" style={{ color: "var(--text-faint)" }}>{i + 1}</td>
                <td className="mono">{formatT(s.t_offset_ms)}</td>
                <td style={{ fontWeight: 500 }}>{s.step}</td>
                <td className="mono" style={{ color: "var(--text-dim)" }}>{s.expected}</td>
                <td className="mono">{s.actual}</td>
                <td>
                  <span className={`badge badge-${s.passed ? "passed" : "failed"}`}>
                    {s.passed ? "passed" : "failed"}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Downstream UPS verification — the spec §4.3 catch */}
      <div className="card">
        <div className="card-h">
          Downstream UPS verification
          <span style={{ marginLeft: 12, fontSize: 11, color: "var(--text-faint)" }}>
            spec §4.3 — ANY downstream UPS not online → outage
          </span>
        </div>
        <table>
          <thead>
            <tr>
              <th>UPS</th><th>Site</th>
              <th>Min input V during transfer</th>
              <th>Battery % after</th>
              <th>Stayed online?</th>
            </tr>
          </thead>
          <tbody>
            {rec.downstream_ups.map((u, i) => (
              <tr key={i}>
                <td className="mono" style={{ fontWeight: 500 }}>{u.id}</td>
                <td>{u.site}</td>
                <td className="mono">{u.min_input_v_during} V</td>
                <td className="mono">{u.battery_pct_after} %</td>
                <td>
                  <span className={`badge badge-${u.stayed_online ? "passed" : "failed"}`}>
                    {u.stayed_online ? "yes" : "DROPPED"}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Visualization of the transfer timeline */}
      <div className="card">
        <div className="card-h">Transfer timeline</div>
        <TransferTimeline sequence={rec.sequence} />
      </div>
    </>
  );
}

function formatT(ms) {
  if (ms === 0) return "T₀";
  if (ms < 1000) return `+${ms}ms`;
  if (ms < 60_000) return `+${(ms / 1000).toFixed(1)}s`;
  if (ms < 3_600_000) return `+${(ms / 60_000).toFixed(1)}min`;
  return `+${(ms / 3_600_000).toFixed(2)}h`;
}

function TransferTimeline({ sequence }) {
  const W = 1200, H = 90;
  const padX = 60;
  const axisY = 56;
  const totalMs = Math.max(1, sequence[sequence.length - 1].t_offset_ms);
  const xFor = (ms) => padX + Math.pow(ms / totalMs, 0.42) * (W - 2 * padX);

  return (
    <div>
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" preserveAspectRatio="xMidYMid meet">
        <line x1={padX} y1={axisY} x2={W - padX} y2={axisY}
              stroke="var(--border-strong)" strokeWidth="2" />
        <text x={padX} y={20} fontSize="13" fill="var(--text-tertiary)"
              fontFamily="var(--font-mono)">
          T₀ utility loss
        </text>
        <text x={W - padX} y={20} fontSize="13" fill="var(--text-tertiary)"
              fontFamily="var(--font-mono)" textAnchor="end">
          retransfer + cooldown
        </text>
        {sequence.map((s, i) => {
          const x = xFor(s.t_offset_ms);
          return (
            <g key={i}>
              <circle cx={x} cy={axisY} r="12"
                      fill={s.passed ? "var(--pass)" : "var(--fail)"}
                      stroke="var(--bg-base)" strokeWidth="2" />
              <text x={x} y={axisY + 5} fontSize="14" fill="var(--text-inverse)"
                    textAnchor="middle" fontFamily="var(--font-mono)" fontWeight="700">
                {i + 1}
              </text>
            </g>
          );
        })}
      </svg>
      <div className="timeline-legend">
        {sequence.map((s, i) => (
          <div key={i} className="timeline-legend-row">
            <span className={s.passed ? "tl-num pass" : "tl-num fail"}>{i + 1}</span>
            <span className="tl-time">{formatT(s.t_offset_ms)}</span>
            <span className="tl-step">{s.step || s.name || ""}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
