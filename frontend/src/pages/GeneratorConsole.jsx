import React, { useEffect, useState } from "react";
import { useParams, Link } from "react-router-dom";
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip,
         ResponsiveContainer, Legend, ReferenceLine, Area, AreaChart } from "recharts";
import { api } from "../hooks/useApi";

// Generator commissioning console — Caterpillar 3516B 1500 kW.
//
//   - Live gauge cluster (RPM, V, Hz, oil pressure, coolant, fuel)
//   - Synchroscope (rotating phase-angle indicator + V/Hz delta vs bus)
//   - 4h load-bank test trace (kW, V, Hz, oil/coolant ramps)
//   - Black-start sequence checklist (8 steps with t-offset and pass/fail)

export default function GeneratorConsole() {
  const { id } = useParams();
  const [rec, setRec] = useState(null);
  const [tel, setTel] = useState(null);

  useEffect(() => {
    api.get(`/equipment/gen/${id}`).then(setRec).catch(() => {});
    let alive = true;
    const tick = async () => {
      try { const d = await api.get("/telemetry"); if (alive) setTel(d); } catch (_) {}
    };
    tick();
    const h = setInterval(tick, 1000);
    return () => { alive = false; clearInterval(h); };
  }, [id]);

  if (!rec) return <p style={{ color: "var(--text-faint)" }}>Loading…</p>;
  const live = tel?.gens?.[id] || {};
  const bus = tel?.buses?.["mv-bus-A"] || {};

  return (
    <>
      <div className="page-header">
        <h1>Generator · {id.toUpperCase()}</h1>
        <span className="crumb">
          <Link to="/sld" style={{ color: "var(--text-faint)" }}>SLD</Link>
          {" / "}{rec.manufacturer} {rec.model} · {rec.rated_kw} kW
        </span>
        <span className={`badge badge-${live.state === "running" ? "passed" : "queued"}`}
              style={{ marginLeft: "auto" }}>
          {(live.state || "standby").toUpperCase()}
        </span>
      </div>

      {/* Live engine gauges */}
      <div className="tile-row">
        <Gauge label="RPM"          value={live.rpm}                max={2000} warn={1900}  unit="" />
        <Gauge label="Voltage L-L"  value={live.voltage_ll_v}       max={520}  warn={500} unit="V" />
        <Gauge label="Frequency"    value={live.frequency_hz}       max={62}   warn={60.5} unit="Hz" precise />
        <Gauge label="Oil pressure" value={live.oil_pressure_psi}   max={80}   warn={70}  crit={75} unit="psi" />
        <Gauge label="Coolant"      value={live.coolant_temp_c}     max={110}  warn={92}  crit={104} unit="°C" />
        <Gauge label="Fuel"         value={live.fuel_level_pct}     max={100}  warn={20} crit={10} unit="%" lowAlarm />
        <Gauge label="Runtime"      value={live.runtime_hours}      max={20000} unit="h" precise />
      </div>

      <div className="row">
        {/* Synchroscope (rotating phasor) */}
        <div className="card" style={{ width: 380 }}>
          <div className="card-h">Synchroscope · Gen vs MV-A bus</div>
          <Synchroscope gen={live} bus={bus} />
          <div className="kv" style={{ marginTop: 8 }}>
            <div className="k">ΔV</div>
            <div className="v">{((live.voltage_ll_v || 0) - bus.voltage_ll_v).toFixed(0)} V</div>
            <div className="k">Δf</div>
            <div className="v">{((live.frequency_hz || 0) - (bus.frequency_hz || 0)).toFixed(3)} Hz</div>
            <div className="k">25-check</div>
            <div className="v">
              {Math.abs((live.frequency_hz || 0) - (bus.frequency_hz || 0)) < 0.2 && live.state === "running"
                ? <span className="badge badge-passed">SYNC OK</span>
                : <span className="badge badge-queued">not in window</span>}
            </div>
          </div>
        </div>

        {/* Load-bank curve */}
        <div className="card grow">
          <div className="card-h">Load-Bank Test · 4 h step ramp · {rec.rated_kw} kW rated</div>
          <div style={{ height: 240 }}>
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={rec.loadbank_trace}
                         margin={{ top: 5, right: 30, left: 5, bottom: 5 }}>
                <CartesianGrid stroke="#2a3441" strokeDasharray="3 3" />
                <XAxis dataKey="t_seconds" type="number"
                       domain={[0, 4*3600]}
                       ticks={[0, 3600, 7200, 10800, 14400]}
                       tickFormatter={(s) => `${(s/3600).toFixed(0)}h`}
                       tick={{ fill: "#8b949e", fontSize: 11 }} />
                <YAxis yAxisId="kw" tick={{ fill: "#8b949e", fontSize: 11 }}
                       label={{ value: "kW", angle: -90, position: "insideLeft", fill: "#8b949e", fontSize: 10 }} />
                <YAxis yAxisId="t" orientation="right" tick={{ fill: "#8b949e", fontSize: 11 }}
                       label={{ value: "°C", angle: 90, position: "insideRight", fill: "#8b949e", fontSize: 10 }} />
                <Tooltip contentStyle={{ background: "#11161e", border: "1px solid #2a3441" }} />
                <Legend wrapperStyle={{ color: "#8b949e", fontSize: 11 }} />
                <ReferenceLine y={rec.rated_kw} yAxisId="kw" stroke="var(--major)"
                               strokeDasharray="4 4"
                               label={{ value: `Rated ${rec.rated_kw} kW`, fill: "var(--major)", fontSize: 10 }} />
                <Line yAxisId="kw" type="monotone" dataKey="kw" stroke="#58a6ff"
                      dot={false} strokeWidth={2} name="Real Power kW" />
                <Line yAxisId="t"  type="monotone" dataKey="oil_temp_c" stroke="#f0a05c"
                      dot={false} strokeDasharray="4 4" name="Oil °C" />
                <Line yAxisId="t"  type="monotone" dataKey="coolant_temp_c" stroke="#56d364"
                      dot={false} strokeDasharray="4 4" name="Coolant °C" />
              </LineChart>
            </ResponsiveContainer>
          </div>
        </div>
      </div>

      {/* Startup sequence */}
      <div className="card" style={{ padding: 0 }}>
        <div className="card-h">Black-Start Sequence · last automatic test</div>
        <table>
          <thead><tr><th style={{width: 30}}>#</th><th>Step</th><th>Expected</th><th>Actual</th><th>Status</th></tr></thead>
          <tbody>
            {rec.startup_sequence.map((s, i) => (
              <tr key={i}>
                <td className="mono" style={{ color: "var(--text-faint)" }}>{i + 1}</td>
                <td style={{ fontWeight: 500 }}>{s.step}</td>
                <td className="mono" style={{ color: "var(--text-dim)" }}>{s.expected}</td>
                <td className="mono">{s.actual}</td>
                <td>
                  {s.passed === true && <span className="badge badge-passed">passed</span>}
                  {s.passed === false && <span className="badge badge-failed">failed</span>}
                  {s.passed === null && <span className="badge badge-info">pending</span>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

function Gauge({ label, value, max, warn, crit, unit, precise, lowAlarm }) {
  if (value === undefined || value === null)
    return <div className="tile"><div className="label">{label}</div>
                <div className="value" style={{ color: "var(--text-faint)" }}>—</div></div>;
  const v = Math.max(0, Math.min(max, value));
  const pct = (v / max) * 100;
  let color = "var(--pass)";
  if (lowAlarm) {
    color = value < (crit ?? 0) ? "var(--fail)"
          : value < (warn ?? 0) ? "var(--major)" : "var(--pass)";
  } else {
    color = value >= (crit ?? max + 1) ? "var(--fail)"
          : value >= (warn ?? max + 1) ? "var(--major)" : "var(--pass)";
  }
  return (
    <div className="tile">
      <div className="label">{label}</div>
      <div className="value" style={{ fontSize: 22, color }}>
        {precise ? value.toFixed(3) : value.toFixed(1)}
        <span style={{ fontSize: 12, color: "var(--text-faint)" }}> {unit}</span>
      </div>
      <div style={{ background: "var(--bg-2)", borderRadius: 3, height: 6, marginTop: 6, overflow: "hidden" }}>
        <div style={{ width: `${pct}%`, height: "100%", background: color, transition: "width 0.4s" }} />
      </div>
    </div>
  );
}

function Synchroscope({ gen, bus }) {
  // Synchroscope rotates at the slip frequency Δf. Pointer angle ∝ time.
  const W = 320, H = 280;
  const cx = W / 2, cy = H / 2;
  const R = 100;
  const df = (gen.frequency_hz || 0) - (bus.frequency_hz || 0);
  // Rotation in degrees — slow rotation if running; static at "12 o'clock" if stopped
  const t = Date.now() / 1000;
  const angle = gen.state === "running" ? (df * 360 * t) % 360 : 0;
  const rad = ((angle - 90) * Math.PI) / 180;
  const px = cx + R * Math.cos(rad), py = cy + R * Math.sin(rad);

  // Sync window: ±10° around 0 (12 o'clock) is the "permissive close" zone
  const arcStart = -10, arcEnd = 10;
  const arcPath = describeArc(cx, cy, R, arcStart, arcEnd);
  return (
    <svg viewBox={`0 0 ${W} ${H}`} width="100%" height={H}>
      <circle cx={cx} cy={cy} r={R + 8} fill="none" stroke="var(--border)" strokeWidth="2" />
      <circle cx={cx} cy={cy} r={R} fill="var(--bg-1)" stroke="var(--border)" />
      {/* Permissive close arc */}
      <path d={arcPath} stroke="var(--pass)" strokeWidth="6" fill="none" strokeOpacity="0.55" />
      {/* Hour ticks */}
      {[0, 30, 60, 90, 120, 150, 180, 210, 240, 270, 300, 330].map((deg) => {
        const r1 = ((deg - 90) * Math.PI) / 180;
        const x1 = cx + (R - 4) * Math.cos(r1), y1 = cy + (R - 4) * Math.sin(r1);
        const x2 = cx + (R + 4) * Math.cos(r1), y2 = cy + (R + 4) * Math.sin(r1);
        return <line key={deg} x1={x1} y1={y1} x2={x2} y2={y2} stroke="var(--text-faint)" strokeWidth="1" />;
      })}
      {/* Vertical reference */}
      <line x1={cx} y1={cy - R - 14} x2={cx} y2={cy - R} stroke="var(--accent)" strokeWidth="2" />
      <text x={cx} y={cy - R - 18} fontSize="10" fill="var(--accent)" textAnchor="middle"
            fontFamily="monospace">SYNC</text>
      {/* Pointer */}
      <line x1={cx} y1={cy} x2={px} y2={py}
            stroke="var(--major)" strokeWidth="3" strokeLinecap="round" />
      <circle cx={cx} cy={cy} r="6" fill="var(--major)" />
      {/* Labels */}
      <text x={cx} y={H - 12} fontSize="10" fill="var(--text-faint)" textAnchor="middle"
            fontFamily="monospace">
        Δf = {df.toFixed(3)} Hz · {gen.state || "stopped"}
      </text>
    </svg>
  );
}

function polarToCartesian(cx, cy, r, deg) {
  const rad = ((deg - 90) * Math.PI) / 180;
  return { x: cx + r * Math.cos(rad), y: cy + r * Math.sin(rad) };
}
function describeArc(cx, cy, r, startDeg, endDeg) {
  const start = polarToCartesian(cx, cy, r, endDeg);
  const end = polarToCartesian(cx, cy, r, startDeg);
  const largeArc = endDeg - startDeg <= 180 ? 0 : 1;
  return `M ${start.x} ${start.y} A ${r} ${r} 0 ${largeArc} 0 ${end.x} ${end.y}`;
}
