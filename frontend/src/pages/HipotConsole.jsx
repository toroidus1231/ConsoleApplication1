import React, { useEffect, useState } from "react";
import { useParams, Link } from "react-router-dom";
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip,
         ResponsiveContainer, Legend, ReferenceLine, ReferenceArea } from "recharts";
import { api } from "../hooks/useApi";

// Cable hipot (DC) commissioning console.
// Shows the Vitrek 95X test trace: voltage ramp + leakage current trend
// over the full ramp + hold window, with the trip-threshold reference
// line and the test-passed envelope. Cal-cert hash is shown — that's
// what makes it a commissioning record vs a service log.

export default function HipotConsole() {
  const { id } = useParams();
  const [rec, setRec] = useState(null);

  useEffect(() => {
    api.get(`/equipment/cable/${id}`).then(setRec).catch(() => {});
  }, [id]);

  if (!rec) return <p style={{ color: "var(--text-faint)" }}>Loading…</p>;
  if (rec.no_prior_run) {
    return (
      <>
        <div className="page-header">
          <h1>Cable Hipot · {id}</h1>
          <span className="crumb">
            <Link to="/sld" style={{ color: "var(--text-faint)" }}>SLD</Link>
            {" / "}no prior run on file
          </span>
        </div>
        <div className="card padded">
          <p style={{ color: "var(--text-secondary)" }}>
            No DC hipot test has been recorded for <code>{id}</code>.
            Schedule a run from the Active Tests page or via the
            commissioning checklist.
          </p>
        </div>
      </>
    );
  }
  const trace = rec.trace.filter((_, i) => i % 4 === 0); // decimate
  const peak = Math.max(...rec.trace.map(p => p.leakage_ma));

  return (
    <>
      <div className="page-header">
        <h1>Cable Hipot · {id}</h1>
        <span className="crumb">
          <Link to="/sld" style={{ color: "var(--text-faint)" }}>SLD</Link>
          {" / "}DC at 80% rated · {rec.target_kv.toFixed(2)} kV target
        </span>
        <span className={`badge badge-${rec.passed ? "passed" : "failed"}`}
              style={{ marginLeft: "auto" }}>
          {rec.passed ? "PASSED" : "FAILED"}
        </span>
      </div>

      <div className="tile-row">
        <Tile label="Test type"        value={rec.test_type} />
        <Tile label="Target voltage"   value={`${rec.target_kv.toFixed(2)} kV`} />
        <Tile label="Ramp"             value={`${rec.ramp_seconds.toFixed(0)}s`} />
        <Tile label="Hold"             value={`${rec.hold_seconds.toFixed(0)}s`} />
        <Tile label="Trip threshold"   value={`${rec.leakage_trip_threshold_ma.toFixed(2)} mA`} />
        <Tile label="Peak leakage"     value={`${peak.toFixed(3)} mA`}
              color={peak < rec.leakage_trip_threshold_ma * 0.8 ? "var(--pass)" : "var(--major)"} />
      </div>

      {/* Instrument identity / cal cert */}
      <div className="card">
        <div className="card-h">Instrument · calibration certificate</div>
        <div className="kv">
          <div className="k">Vendor / model</div>
          <div className="v">{rec.instrument.vendor} · {rec.instrument.model}</div>
          <div className="k">Serial</div>
          <div className="v mono">{rec.instrument.serial}</div>
          <div className="k">Cal cert hash</div>
          <div className="v mono" style={{ wordBreak: "break-all" }}>{rec.instrument.cal_cert}</div>
        </div>
      </div>

      <div className="card">
        <div className="card-h">Voltage / Leakage Trace</div>
        <div style={{ height: 320 }}>
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={trace} margin={{ top: 5, right: 30, left: 5, bottom: 5 }}>
              <CartesianGrid stroke="#2a3441" strokeDasharray="3 3" />
              <XAxis dataKey="t_seconds" type="number"
                     tickFormatter={(s) => `${s}s`}
                     tick={{ fill: "#8b949e", fontSize: 11 }} />
              <YAxis yAxisId="v" domain={[0, rec.target_kv * 1.1]}
                     tick={{ fill: "#8b949e", fontSize: 11 }}
                     label={{ value: "kV", angle: -90, position: "insideLeft", fill: "#8b949e", fontSize: 10 }} />
              <YAxis yAxisId="i" orientation="right" domain={[0, rec.leakage_trip_threshold_ma * 1.2]}
                     tick={{ fill: "#8b949e", fontSize: 11 }}
                     label={{ value: "mA", angle: 90, position: "insideRight", fill: "#8b949e", fontSize: 10 }} />
              <Tooltip contentStyle={{ background: "#11161e", border: "1px solid #2a3441" }} />
              <Legend wrapperStyle={{ color: "#8b949e", fontSize: 11 }} />
              <ReferenceArea x1={0} x2={rec.ramp_seconds} yAxisId="v"
                             fill="var(--accent)" fillOpacity={0.04}
                             label={{ value: "RAMP", position: "insideTop", fill: "var(--text-faint)", fontSize: 10 }} />
              <ReferenceArea x1={rec.ramp_seconds} x2={rec.ramp_seconds + rec.hold_seconds} yAxisId="v"
                             fill="var(--pass)" fillOpacity={0.04}
                             label={{ value: "HOLD", position: "insideTop", fill: "var(--text-faint)", fontSize: 10 }} />
              <ReferenceLine yAxisId="i" y={rec.leakage_trip_threshold_ma}
                             stroke="var(--fail)" strokeDasharray="4 4"
                             label={{ value: "trip threshold", fill: "var(--fail)", fontSize: 10 }} />
              <Line yAxisId="v" type="monotone" dataKey="voltage_kv"
                    stroke="#58a6ff" dot={false} strokeWidth={2} name="Voltage (kV)" />
              <Line yAxisId="i" type="monotone" dataKey="leakage_ma"
                    stroke="#f0a05c" dot={false} strokeWidth={2} name="Leakage (mA)" />
            </LineChart>
          </ResponsiveContainer>
        </div>
      </div>

      <div className="card" style={{ padding: 0 }}>
        <div className="card-h">Trend across commissioning runs</div>
        <table>
          <thead><tr><th>Days ago</th><th>Max leakage</th><th>Status</th></tr></thead>
          <tbody>
            {rec.history.map((h, i) => (
              <tr key={i}>
                <td className="mono">{h.date_offset_days}</td>
                <td className="mono">{h.max_leakage_ma.toFixed(3)} mA</td>
                <td><span className={`badge badge-${h.passed ? "passed" : "failed"}`}>
                    {h.passed ? "passed" : "failed"}</span></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

function Tile({ label, value, color }) {
  return (
    <div className="tile">
      <div className="label">{label}</div>
      <div className="value" style={{ fontSize: 18, color: color || "var(--text)", fontFamily: "monospace" }}>
        {value}
      </div>
    </div>
  );
}
