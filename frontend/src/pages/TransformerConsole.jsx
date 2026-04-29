import React, { useEffect, useState } from "react";
import { useParams, Link } from "react-router-dom";
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip,
         ResponsiveContainer, Legend, ReferenceLine } from "recharts";
import { api } from "../hooks/useApi";

// Transformer commissioning console. The big-ticket pages a CX engineer
// actually opens for an oil-filled XFMR:
//
//   - Duval triangle DGA fault diagnosis (active-arcing zone if C2H2 high)
//   - Dissolved-gas time series (last 90 d, weekly samples)
//   - Winding/oil temp gauges with live telemetry
//   - TTR results (turns ratio per tap × phase, with deviation)
//   - Polarization Index curve
//   - Hipot history

export default function TransformerConsole() {
  const { id } = useParams();
  const [rec, setRec] = useState(null);
  const [tel, setTel] = useState(null);

  useEffect(() => {
    api.get(`/equipment/xfmr/${id}`).then(setRec).catch(() => {});
    let alive = true;
    const tick = async () => {
      try { const d = await api.get("/telemetry"); if (alive) setTel(d); } catch (_) {}
    };
    tick();
    const h = setInterval(tick, 1500);
    return () => { alive = false; clearInterval(h); };
  }, [id]);

  if (!rec) return <p style={{ color: "var(--text-faint)" }}>Loading…</p>;
  const live = tel?.xfmrs?.[id];
  const arc = live && live.c2h2_ppm > 2;

  return (
    <>
      <div className="page-header">
        <h1>Transformer · {id.toUpperCase()}</h1>
        <span className="crumb">
          <Link to="/sld" style={{ color: "var(--text-faint)" }}>SLD</Link>
          {" / "}{rec.rated_kva} kVA · {rec.voltage_class} · {rec.vector_group}
        </span>
        {arc && (
          <span className="badge badge-critical" style={{ marginLeft: "auto" }}>
            ACTIVE ARCING — C₂H₂ {live.c2h2_ppm.toFixed(2)} ppm
          </span>
        )}
      </div>

      {/* Live gauges */}
      <div className="tile-row">
        <Gauge label="Winding"  unit="°C" value={live?.winding_temp_c} max={120} warn={95}  crit={110} />
        <Gauge label="Oil"      unit="°C" value={live?.oil_temp_c}     max={110} warn={85}  crit={100} />
        <Gauge label="H₂"       unit="ppm" value={live?.h2_ppm}        max={500} warn={120} crit={300} />
        <Gauge label="C₂H₂"     unit="ppm" value={live?.c2h2_ppm}      max={20}  warn={2}   crit={5}   alarm={arc}/>
        <Gauge label="Moisture" unit="ppm" value={live?.moisture_ppm}  max={30}  warn={15}  crit={25}  />
        <Gauge label="PD"       unit="pC"  value={live?.pd_magnitude_pc} max={200} warn={50} crit={120}/>
      </div>

      <div className="row">
        {/* Duval Triangle */}
        <div className="card" style={{ width: 460 }}>
          <div className="card-h">Duval Triangle (DGA fault diagnosis)</div>
          <Duval ch4={live?.ch4_ppm || 18} c2h4={1.4} c2h2={live?.c2h2_ppm || 0.8} />
          <p style={{ fontSize: 11, color: "var(--text-faint)", marginTop: 6 }}>
            Sample point: CH₄ {live?.ch4_ppm?.toFixed(1) || "—"} · C₂H₄ 1.4 · C₂H₂ {live?.c2h2_ppm?.toFixed(2) || "—"} ppm.
            Zones: PD low-energy, T1/T2/T3 thermal, D1/D2 discharge.
          </p>
        </div>

        {/* DGA gas history */}
        <div className="card grow">
          <div className="card-h">Dissolved Gas Analysis · 90 day trend</div>
          <div style={{ height: 260 }}>
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={rec.dga_history} margin={{ top: 5, right: 20, left: 5, bottom: 5 }}>
                <CartesianGrid stroke="#2a3441" strokeDasharray="3 3" />
                <XAxis dataKey="days_ago" reversed type="number" domain={[91, 0]}
                       tick={{ fill: "#8b949e", fontSize: 11 }}
                       label={{ value: "days ago", offset: -3, position: "insideBottom", fill: "#8b949e", fontSize: 10 }} />
                <YAxis tick={{ fill: "#8b949e", fontSize: 11 }} />
                <Tooltip contentStyle={{ background: "#11161e", border: "1px solid #2a3441" }} />
                <Legend wrapperStyle={{ color: "#8b949e", fontSize: 11 }} />
                <ReferenceLine y={2} stroke="var(--fail)" strokeDasharray="4 4"
                               label={{ value: "C₂H₂ alarm 2 ppm", fill: "var(--fail)", fontSize: 10 }} />
                <Line type="monotone" dataKey="h2"   stroke="#58a6ff" name="H₂"    dot={false} />
                <Line type="monotone" dataKey="ch4"  stroke="#56d364" name="CH₄"   dot={false} />
                <Line type="monotone" dataKey="c2h6" stroke="#bc8cff" name="C₂H₆"  dot={false} />
                <Line type="monotone" dataKey="c2h4" stroke="#f0a05c" name="C₂H₄"  dot={false} />
                <Line type="monotone" dataKey="c2h2" stroke="#f85149" name="C₂H₂"  dot={false} strokeWidth={2} />
              </LineChart>
            </ResponsiveContainer>
          </div>
        </div>
      </div>

      {/* TTR + PI */}
      <div className="row">
        <div className="card grow" style={{ padding: 0 }}>
          <div className="card-h">Transformer Turns Ratio (TTR)</div>
          <table>
            <thead>
              <tr><th>Tap</th><th>Phase</th><th>Expected</th><th>Measured</th><th>Deviation</th><th>Status</th></tr>
            </thead>
            <tbody>
              {rec.ttr.map((r, i) => (
                <tr key={i}>
                  <td className="mono">{r.tap}</td>
                  <td>{r.phase}</td>
                  <td className="mono">{r.expected.toFixed(4)}</td>
                  <td className="mono">{r.measured.toFixed(4)}</td>
                  <td className="mono" style={{ color: Math.abs(r.deviation_pct) > 0.5 ? "var(--fail)" : "var(--text)" }}>
                    {r.deviation_pct >= 0 ? "+" : ""}{r.deviation_pct.toFixed(3)}%
                  </td>
                  <td>
                    <span className={`badge badge-${r.passed ? "passed" : "failed"}`}>
                      {r.passed ? "passed" : "failed"}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="card" style={{ width: 380 }}>
          <div className="card-h">Polarization Index · PI = {rec.polarization_index.value.toFixed(2)}
            <span className={`badge badge-${rec.polarization_index.passed ? "passed" : "failed"}`}
                  style={{ marginLeft: 8, fontSize: 9 }}>
              {rec.polarization_index.passed ? "passed (>2.0)" : "failed (<2.0)"}
            </span>
          </div>
          <div style={{ height: 200 }}>
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={rec.polarization_index.curve}>
                <CartesianGrid stroke="#2a3441" strokeDasharray="3 3" />
                <XAxis dataKey="minute" tick={{ fill: "#8b949e", fontSize: 11 }}
                       label={{ value: "min", offset: -3, position: "insideBottom", fill: "#8b949e", fontSize: 10 }} />
                <YAxis tick={{ fill: "#8b949e", fontSize: 11 }}
                       label={{ value: "MΩ", angle: -90, position: "insideLeft", fill: "#8b949e", fontSize: 10 }} />
                <Tooltip contentStyle={{ background: "#11161e", border: "1px solid #2a3441" }} />
                <Line type="monotone" dataKey="resistance_mohm" stroke="#58a6ff" dot={false} strokeWidth={2}/>
              </LineChart>
            </ResponsiveContainer>
          </div>
        </div>
      </div>

      {/* Hipot history */}
      <div className="card" style={{ padding: 0 }}>
        <div className="card-h">Hipot history (last 4 commissioning runs · trending)</div>
        <table>
          <thead><tr><th>Days ago</th><th>Voltage</th><th>Leakage</th><th>Duration</th><th>Status</th></tr></thead>
          <tbody>
            {rec.hipot_history.map((h, i) => (
              <tr key={i}>
                <td className="mono">{h.date_offset_days}</td>
                <td className="mono">{h.voltage_kv.toFixed(2)} kV</td>
                <td className="mono">{h.leakage_ma.toFixed(3)} mA</td>
                <td className="mono">{h.duration_seconds}s</td>
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

// ---------------------------------------------------------------------------
// Components
// ---------------------------------------------------------------------------

function Gauge({ label, unit, value, max, warn, crit, alarm }) {
  if (value === undefined || value === null)
    return <div className="tile"><div className="label">{label}</div>
                <div className="value" style={{ color: "var(--text-faint)" }}>—</div></div>;
  const v = Math.max(0, Math.min(max, value));
  const pct = (v / max) * 100;
  const color = alarm ? "var(--fail)"
              : value >= (crit ?? max) ? "var(--fail)"
              : value >= (warn ?? max) ? "var(--major)"
              : "var(--pass)";
  return (
    <div className="tile">
      <div className="label">{label}</div>
      <div className="value" style={{ fontSize: 22, color }}>
        {value.toFixed(2)} <span style={{ fontSize: 12, color: "var(--text-faint)" }}>{unit}</span>
      </div>
      <div style={{ background: "var(--bg-2)", borderRadius: 3, height: 6, marginTop: 6, overflow: "hidden" }}>
        <div style={{ width: `${pct}%`, height: "100%", background: color, transition: "width 0.4s" }} />
      </div>
    </div>
  );
}

// Duval Triangle 1 — ternary plot of CH4 / C2H4 / C2H2 percentages.
// Six fault zones (PD, T1, T2, T3, D1, D2, DT). Sample point overlaid.
function Duval({ ch4, c2h4, c2h2 }) {
  const total = ch4 + c2h4 + c2h2;
  const a = ch4  / total;  // bottom-left vertex
  const b = c2h4 / total;  // bottom-right
  const c = c2h2 / total;  // top
  // Ternary → cartesian
  const W = 400, H = 360;
  const cx = W / 2, cy = H - 40;
  const side = 320;
  // Vertices
  const V_CH4  = { x: cx - side / 2, y: cy };           // bottom-left (CH4)
  const V_C2H4 = { x: cx + side / 2, y: cy };           // bottom-right (C2H4)
  const V_C2H2 = { x: cx,            y: cy - side * Math.sqrt(3) / 2 }; // top

  const tern = (a_, b_, c_) => ({
    x: a_ * V_CH4.x + b_ * V_C2H4.x + c_ * V_C2H2.x,
    y: a_ * V_CH4.y + b_ * V_C2H4.y + c_ * V_C2H2.y,
  });

  // Approximate Duval-1 zone boundaries in ternary (CH4, C2H4, C2H2) %.
  // Each zone is a polygon expressed as ternary triples.
  const ZONES = [
    { name: "PD", color: "#5b6cff",
      pts: [[0.98, 0.00, 0.02], [0.97, 0.01, 0.02], [0.97, 0.03, 0.00], [0.98, 0.02, 0.00]] },
    { name: "T1", color: "#58a6ff",
      pts: [[0.97, 0.01, 0.02], [0.76, 0.20, 0.04], [0.50, 0.50, 0.00], [0.97, 0.03, 0.00]] },
    { name: "T2", color: "#56d364",
      pts: [[0.76, 0.20, 0.04], [0.50, 0.46, 0.04], [0.20, 0.80, 0.00], [0.50, 0.50, 0.00]] },
    { name: "T3", color: "#9eff68",
      pts: [[0.50, 0.46, 0.04], [0.00, 0.96, 0.04], [0.00, 1.00, 0.00], [0.20, 0.80, 0.00]] },
    { name: "D1", color: "#f0a05c",
      pts: [[0.97, 0.00, 0.03], [0.64, 0.23, 0.13], [0.13, 0.00, 0.87], [0.87, 0.00, 0.13]] },
    { name: "D2", color: "#f85149",
      pts: [[0.64, 0.23, 0.13], [0.31, 0.40, 0.29], [0.00, 0.71, 0.29], [0.13, 0.00, 0.87],
            [0.31, 0.00, 0.69]] },
    { name: "DT", color: "#bc8cff",
      pts: [[0.31, 0.40, 0.29], [0.50, 0.46, 0.04], [0.76, 0.20, 0.04],
            [0.64, 0.23, 0.13]] },
  ];

  return (
    <svg viewBox={`0 0 ${W} ${H}`} width="100%" style={{ maxHeight: 360 }}>
      {ZONES.map((z) => {
        const points = z.pts.map(([a_, b_, c_]) => {
          const p = tern(a_, b_, c_);
          return `${p.x.toFixed(1)},${p.y.toFixed(1)}`;
        }).join(" ");
        return (
          <g key={z.name}>
            <polygon points={points} fill={z.color} fillOpacity="0.18"
                     stroke={z.color} strokeWidth="1" />
            <text x={tern(...avg(z.pts)).x} y={tern(...avg(z.pts)).y}
                  fontSize="10" fontWeight="600" fill={z.color} textAnchor="middle"
                  fontFamily="monospace">
              {z.name}
            </text>
          </g>
        );
      })}
      {/* Triangle outline */}
      <polygon
        points={`${V_CH4.x},${V_CH4.y} ${V_C2H4.x},${V_C2H4.y} ${V_C2H2.x},${V_C2H2.y}`}
        fill="none" stroke="var(--text)" strokeWidth="1.4" />
      {/* Vertex labels */}
      <text x={V_CH4.x - 6} y={V_CH4.y + 16} fontSize="11" fill="var(--text)" textAnchor="end">
        CH₄ 100%
      </text>
      <text x={V_C2H4.x + 6} y={V_C2H4.y + 16} fontSize="11" fill="var(--text)">
        C₂H₄ 100%
      </text>
      <text x={V_C2H2.x} y={V_C2H2.y - 8} fontSize="11" fill="var(--text)" textAnchor="middle">
        C₂H₂ 100%
      </text>
      {/* Sample point */}
      <circle cx={tern(a, b, c).x} cy={tern(a, b, c).y} r="6"
              fill="var(--major)" stroke="white" strokeWidth="2" />
      <text x={tern(a, b, c).x + 10} y={tern(a, b, c).y + 4} fontSize="10"
            fill="var(--major)" fontFamily="monospace">
        sample
      </text>
    </svg>
  );
}

function avg(pts) {
  const n = pts.length;
  return [
    pts.reduce((s, p) => s + p[0], 0) / n,
    pts.reduce((s, p) => s + p[1], 0) / n,
    pts.reduce((s, p) => s + p[2], 0) / n,
  ];
}
