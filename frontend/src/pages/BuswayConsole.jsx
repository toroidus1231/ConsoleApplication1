import React, { useEffect, useState } from "react";
import { useParams, Link } from "react-router-dom";
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip,
         ResponsiveContainer, ReferenceLine, BarChart, Bar } from "recharts";
import { api } from "../hooks/useApi";

// Vertiv busway commissioning console.
// Aggregates the three instrument-driven tests (Megger / Hipot / DLRO)
// plus operator sign-offs into one acceptance record per IEC 61439-6
// and NETA ATS-2017 §7.4.

export default function BuswayConsole() {
  const { id } = useParams();
  const [rec, setRec] = useState(null);

  useEffect(() => {
    api.get(`/equipment/busway/${id}`).then(setRec).catch(() => {});
  }, [id]);

  if (!rec) return <p style={{ color: "var(--text-faint)" }}>Loading…</p>;
  if (rec.no_prior_run) {
    return (
      <>
        <div className="page-header">
          <h1>Busway · {id}</h1>
          <span className="crumb">no prior commissioning on file</span>
        </div>
        <div className="card padded">
          <p style={{ color: "var(--text-secondary)" }}>
            No commissioning runs recorded for <code>{id}</code>.
          </p>
        </div>
      </>
    );
  }

  const { megger, hipot, dlro, manual_signoffs, automation_summary, passed } = rec;
  const traceDecimated = (hipot?.trace || []).filter((_, i) => i % 4 === 0);

  return (
    <>
      <div className="page-header">
        <h1>Busway · {id}</h1>
        <span className="crumb">
          <Link to="/sld" style={{ color: "var(--text-faint)" }}>SLD</Link>
          {" / "}NETA ATS-17 §7.4 · IEC 61439-6 acceptance
        </span>
        <span className={`badge badge-${passed ? "passed" : "failed"}`}
              style={{ marginLeft: "auto" }}>
          {passed ? "ACCEPTED" : "DEFICIENCY"}
        </span>
      </div>

      {/* Identity + automation summary tiles */}
      <div className="tile-row">
        <Tile label="Manufacturer / Model"
              value={`${hipot?.manufacturer || "Vertiv"} ${hipot?.model || ""}`}
              fallback={`${megger ? "" : "—"}`} />
        <Tile label="Rated current"
              value={`${(megger?.rated_amps || hipot?.rated_amps || dlro?.rated_amps || guessAmps(id))} A`} />
        <Tile label="Voltage class"
              value={`${hipot?.voltage_class_v || 600} V`} />
        <Tile label="Automated steps"
              value={`${automation_summary.automated_steps} of ${automation_summary.automated_steps + automation_summary.manual_steps}`} />
        <Tile label="Automation"
              value={`${automation_summary.automated_pct.toFixed(0)} %`}
              color="var(--accent)" />
        <Tile label="Overall"
              value={passed ? "ACCEPTED" : "DEFICIENCY"}
              color={passed ? "var(--pass)" : "var(--fail)"} />
      </div>

      {/* MEGGER */}
      {megger && (
        <div className="card">
          <div className="card-h">
            Insulation resistance · Megger {megger.instrument.model} ·
            applied {megger.test_voltage_v} VDC for {megger.applied_seconds}s
            <span className={`badge badge-${megger.passed ? "passed" : "failed"}`}
                  style={{ marginLeft: 12 }}>
              {megger.passed ? "PASS" : "FAIL"}
            </span>
          </div>
          <div className="card-body">
            <table>
              <thead><tr>
                <th>Pair</th><th>Resistance (MΩ)</th><th>Acceptance</th><th>Status</th>
              </tr></thead>
              <tbody>
                {megger.readings.map((r, i) => (
                  <tr key={i}>
                    <td className="mono">{r.from}–{r.to}</td>
                    <td className="mono">{r.megohm.toFixed(0)} MΩ</td>
                    <td className="mono">≥ {megger.acceptance_megohm} MΩ</td>
                    <td><span className={`badge badge-${r.passed ? "passed" : "failed"}`}>
                        {r.passed ? "pass" : "fail"}</span></td>
                  </tr>
                ))}
              </tbody>
            </table>
            <div style={{ marginTop: 8, color: "var(--text-tertiary)", fontFamily: "var(--font-mono)", fontSize: 11 }}>
              cal cert: {megger.instrument.cal_cert}
            </div>
          </div>
        </div>
      )}

      {/* HIPOT */}
      {hipot && (
        <div className="card">
          <div className="card-h">
            DC withstand · Vitrek {hipot.instrument.model} ·
            {" "}{hipot.target_kv} kV / {hipot.hold_seconds}s · trip {hipot.leakage_trip_ma} mA
            <span className={`badge badge-${hipot.passed ? "passed" : "failed"}`}
                  style={{ marginLeft: 12 }}>
              {hipot.passed ? "PASS" : "FAIL"}
            </span>
          </div>
          <div className="card-body">
            <div style={{ display: "flex", gap: 24, alignItems: "baseline", marginBottom: 8 }}>
              <span style={{ fontFamily: "var(--font-mono)" }}>
                peak leakage <b style={{ color: "var(--pass)" }}>{hipot.peak_leakage_ma.toFixed(4)} mA</b>
              </span>
              <span style={{ fontFamily: "var(--font-mono)" }}>
                R = {hipot.insulation_megohm.toFixed(0)} MΩ
              </span>
            </div>
            <div style={{ height: 220 }}>
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={traceDecimated} margin={{ top: 5, right: 30, left: 5, bottom: 5 }}>
                  <CartesianGrid stroke="#2a3441" strokeDasharray="3 3" />
                  <XAxis dataKey="t_seconds" type="number"
                         tickFormatter={(s) => `${s}s`}
                         tick={{ fill: "#8b949e", fontSize: 11 }} />
                  <YAxis yAxisId="v" domain={[0, hipot.target_kv * 1.1]}
                         tick={{ fill: "#8b949e", fontSize: 11 }}
                         label={{ value: "kV", angle: -90, position: "insideLeft", fill: "#8b949e", fontSize: 10 }} />
                  <YAxis yAxisId="i" orientation="right"
                         domain={[0, hipot.leakage_trip_ma * 1.2]}
                         tick={{ fill: "#8b949e", fontSize: 11 }}
                         label={{ value: "mA", angle: 90, position: "insideRight", fill: "#8b949e", fontSize: 10 }} />
                  <Tooltip contentStyle={{ background: "#11161e", border: "1px solid #2a3441" }} />
                  <ReferenceLine yAxisId="i" y={hipot.leakage_trip_ma}
                                 stroke="var(--fail)" strokeDasharray="4 4" />
                  <Line yAxisId="v" type="monotone" dataKey="voltage_kv"
                        stroke="#58a6ff" dot={false} strokeWidth={2} name="V" />
                  <Line yAxisId="i" type="monotone" dataKey="leakage_ma"
                        stroke="#f0a05c" dot={false} strokeWidth={2} name="I" />
                </LineChart>
              </ResponsiveContainer>
            </div>
          </div>
        </div>
      )}

      {/* DLRO */}
      {dlro && (
        <div className="card">
          <div className="card-h">
            Joint resistance · Megger {dlro.instrument.model} ·
            {" "}{dlro.test_current_a} A DC · acceptance ≤ {dlro.joint_acceptance_uohm} µΩ
            <span className={`badge badge-${dlro.passed ? "passed" : "failed"}`}
                  style={{ marginLeft: 12 }}>
              {dlro.passed ? "PASS" : "FAIL"}
            </span>
          </div>
          <div className="card-body">
            <div style={{ marginBottom: 8, fontFamily: "var(--font-mono)" }}>
              {dlro.joints.length} bolted joints · max <b style={{ color: dlro.passed ? "var(--pass)" : "var(--fail)" }}>{dlro.max_joint_uohm} µΩ</b>
            </div>
            <div style={{ height: 200 }}>
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={dlro.joints.map(j => ({
                  joint: j.joint, A: j.uohm_per_phase.A,
                  B: j.uohm_per_phase.B, C: j.uohm_per_phase.C, max: j.max_uohm,
                }))}>
                  <CartesianGrid stroke="#2a3441" strokeDasharray="3 3" />
                  <XAxis dataKey="joint" tick={{ fill: "#8b949e", fontSize: 11 }}
                         label={{ value: "joint #", position: "insideBottom", fill: "#8b949e", fontSize: 10, offset: -2 }} />
                  <YAxis tick={{ fill: "#8b949e", fontSize: 11 }}
                         label={{ value: "µΩ", angle: -90, position: "insideLeft", fill: "#8b949e", fontSize: 10 }} />
                  <Tooltip contentStyle={{ background: "#11161e", border: "1px solid #2a3441" }} />
                  <ReferenceLine y={dlro.joint_acceptance_uohm}
                                 stroke="var(--fail)" strokeDasharray="4 4"
                                 label={{ value: `≤ ${dlro.joint_acceptance_uohm} µΩ`, fill: "var(--fail)", fontSize: 10 }} />
                  <Bar dataKey="max" fill="#58a6ff" />
                </BarChart>
              </ResponsiveContainer>
            </div>
          </div>
        </div>
      )}

      {/* MANUAL SIGN-OFFS */}
      {Object.keys(manual_signoffs).length > 0 && (
        <div className="card">
          <div className="card-h">Operator sign-offs · IEC 61439-6 §10.2 / NETA §7.4</div>
          <div className="card-body">
            <table>
              <thead><tr>
                <th>Step</th><th>Operator</th><th>Note</th><th>Status</th>
              </tr></thead>
              <tbody>
                {Object.entries(manual_signoffs).map(([k, v]) => (
                  <tr key={k}>
                    <td>{v.label}</td>
                    <td className="mono">{v.operator || "—"}</td>
                    <td style={{ color: "var(--text-tertiary)", fontSize: 12 }}>
                      {v.note || (v.joints_checked ? `${v.joints_checked} joints checked` : "") || (v.evidence_image_count ? `${v.evidence_image_count} images attached` : "")}
                    </td>
                    <td><span className={`badge badge-${v.passed ? "passed" : "failed"}`}>
                        {v.passed ? "signed" : "open"}</span></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </>
  );
}

function Tile({ label, value, color }) {
  return (
    <div className="tile">
      <div className="label">{label}</div>
      <div className="value" style={{ fontSize: 18, color: color || "var(--text)", fontFamily: "var(--font-mono)" }}>
        {value}
      </div>
    </div>
  );
}

function guessAmps(id) {
  if (id.includes("4000")) return 4000;
  if (id.includes("3200")) return 3200;
  if (id.includes("1250")) return 1250;
  if (id.startsWith("mtg-feed")) return 4000;
  if (id.startsWith("mtg-row")) return 3200;
  if (id.startsWith("impb")) return 1250;
  return "—";
}
