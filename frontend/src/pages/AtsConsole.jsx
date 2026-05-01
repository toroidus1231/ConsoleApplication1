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

function clusterPhases(sequence) {
  // Group consecutive steps whose time gap to the previous step is less than
  // a phase boundary (60 s). Each cluster becomes a phase card.
  const phases = [];
  const boundaryMs = 60_000;
  for (let i = 0; i < sequence.length; i++) {
    const s = sequence[i];
    const last = phases[phases.length - 1];
    if (!last || s.t_offset_ms - last.endMs >= boundaryMs) {
      phases.push({ steps: [s], startMs: s.t_offset_ms, endMs: s.t_offset_ms });
    } else {
      last.steps.push(s);
      last.endMs = s.t_offset_ms;
    }
  }
  return phases;
}

function phaseTitle(phase, index, totalPhases) {
  const names = phase.steps.map((s) => (s.step || "").toLowerCase());
  if (names.some((n) => n.includes("transfer to source"))) return "Transfer to alternate";
  if (names.some((n) => n.includes("return to utility")))  return "Return to utility";
  if (names.some((n) => n.includes("cooldown")))           return "Cooldown";
  if (names.every((n) => n.startsWith("pre-test")))        return "Pre-test";
  if (index === 0) return "Pre-test";
  if (index === totalPhases - 1) return "Cooldown";
  return `Phase ${index + 1}`;
}

function TransferTimeline({ sequence }) {
  const phases = clusterPhases(sequence);
  return (
    <div className="ats-pipeline">
      {phases.map((p, i) => {
        const allPassed = p.steps.every((s) => s.passed);
        const range = p.startMs === p.endMs
          ? formatT(p.startMs)
          : `${formatT(p.startMs)} → ${formatT(p.endMs)}`;
        return (
          <React.Fragment key={i}>
            <div className={`ats-phase ${allPassed ? "ok" : "bad"}`}>
              <div className="ats-phase-head">
                <span className="ats-phase-idx">{String(i + 1).padStart(2, "0")}</span>
                <span className="ats-phase-name">{phaseTitle(p, i, phases.length)}</span>
                <span className={`ats-phase-status ${allPassed ? "ok" : "bad"}`}>
                  {allPassed ? "PASS" : "FAIL"}
                </span>
              </div>
              <div className="ats-phase-range">{range}</div>
              <ul className="ats-phase-steps">
                {p.steps.map((s, j) => (
                  <li key={j} className={s.passed ? "ok" : "bad"}>
                    <span className="ats-step-t">{formatT(s.t_offset_ms)}</span>
                    <span className="ats-step-name">{s.step || s.name || ""}</span>
                  </li>
                ))}
              </ul>
            </div>
            {i < phases.length - 1 && <div className="ats-phase-link" aria-hidden />}
          </React.Fragment>
        );
      })}
    </div>
  );
}
