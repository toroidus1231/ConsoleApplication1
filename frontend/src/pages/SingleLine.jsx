import React, { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../hooks/useApi";

// Single-Line Diagram (SLD) — operator-grade switchgear HMI.
// Live bus voltages, currents, breaker positions, relay trip targets,
// transformer telemetry. Polls /telemetry once per second.

const REFRESH_MS = 1000;

export default function SingleLine() {
  const [t, setT] = useState(null);
  const [selected, setSelected] = useState(null);
  const navigate = useNavigate();

  useEffect(() => {
    let alive = true;
    const tick = async () => {
      try { const d = await api.get("/telemetry"); if (alive) setT(d); } catch (_) {}
    };
    tick();
    const h = setInterval(tick, REFRESH_MS);
    return () => { alive = false; clearInterval(h); };
  }, []);

  if (!t) return <p style={{ color: "var(--text-faint)" }}>Loading telemetry…</p>;

  return (
    <>
      <div className="page-header">
        <h1>Single-Line Diagram · MV/LV Lineup</h1>
        <span className="crumb">DC1-Ashburn · live · {t.timestamp}</span>
      </div>

      <SoeBanner soe={t.soe || []} />

      <div className="row">
        <div className="card grow" style={{ padding: 0, overflow: "hidden" }}>
          <SldSvg t={t} onSelect={setSelected} navigate={navigate} />
        </div>
        <DetailPanel t={t} selected={selected} />
      </div>
    </>
  );
}

// ---------------------------------------------------------------------------
// SoE alarm banner ribbon
// ---------------------------------------------------------------------------

function SoeBanner({ soe }) {
  const top = soe.slice(-3).reverse();
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 4, marginBottom: 12 }}>
      {top.map((e, i) => (
        <div key={i} style={{
          display: "grid", gridTemplateColumns: "100px 80px 180px 1fr",
          gap: 12, alignItems: "center",
          padding: "6px 12px", borderRadius: 6,
          background: SEV_BG[e.severity] || "var(--bg-2)",
          borderLeft: `3px solid ${SEV_BD[e.severity] || "var(--text-faint)"}`,
          fontSize: 12,
        }}>
          <span className="mono" style={{ color: "var(--text-faint)" }}>
            {e.timestamp.slice(11, 19)}
          </span>
          <span className={`badge badge-${SEV_BADGE[e.severity] || "info"}`}>{e.severity}</span>
          <span className="mono" style={{ color: "var(--text-dim)" }}>{e.device_id}</span>
          <span>{e.text}</span>
        </div>
      ))}
    </div>
  );
}

const SEV_BG = {
  critical: "rgba(248, 81, 73, 0.10)",
  alarm:    "rgba(240, 160, 92, 0.10)",
  warn:     "rgba(210, 153, 34, 0.10)",
  info:     "rgba(110, 118, 129, 0.10)",
};
const SEV_BD = {
  critical: "var(--critical)", alarm: "var(--major)", warn: "var(--queued)", info: "var(--info)",
};
const SEV_BADGE = {
  critical: "critical", alarm: "major", warn: "queued", info: "info",
};

// ---------------------------------------------------------------------------
// SLD SVG — the actual one-line drawing
// ---------------------------------------------------------------------------

function SldSvg({ t, onSelect, navigate }) {
  // Layout coordinates (manual — looks like a real one-line)
  // x: column (utility, MV mains, MV bus, XFMR, MTZ inc, LV bus, ATS/UPS)
  // y: rows
  const W = 1700, H = 880;

  const buses = t.buses || {};
  const breakers = t.breakers || {};
  const relays = t.relays || {};

  const energized = (id) => breakers[id]?.position === "closed";

  return (
    <svg viewBox={`0 0 ${W} ${H}`} width="100%" height="780"
         style={{ background: "var(--bg-0)" }}>
      <defs>
        <pattern id="diag" width="6" height="6" patternUnits="userSpaceOnUse"
                 patternTransform="rotate(45)">
          <line x1="0" y1="0" x2="0" y2="6" stroke="rgba(86,211,100,0.3)" strokeWidth="2" />
        </pattern>
        <marker id="arr" viewBox="0 0 10 10" refX="5" refY="5" markerWidth="5" markerHeight="5">
          <polygon points="0,0 10,5 0,10" fill="var(--text-faint)" />
        </marker>
      </defs>

      {/* === Utility feeds === */}
      <UtilSource x={120} y={60} label="UTILITY A · 13.8 kV" />
      <UtilSource x={420} y={60} label="UTILITY B · 13.8 kV" />

      {/* Verticals down to MV mains */}
      <Wire d={`M120 90 L120 160`} live />
      <Wire d={`M420 90 L420 160`} live />

      {/* MV main breakers */}
      <Breaker x={120} y={170} state={breakers["mv-main-A"]?.position}
               label="MV-MAIN-A" rating="1200A"
               onSelect={() => onSelect({type:"breaker",id:"mv-main-A"})} />
      <Breaker x={420} y={170} state={breakers["mv-main-B"]?.position}
               label="MV-MAIN-B" rating="1200A"
               onSelect={() => onSelect({type:"breaker",id:"mv-main-B"})} />

      {/* SEL relays alongside MV mains */}
      <Relay x={50}  y={210} id="sel-mv-main-A" relay={relays["sel-mv-main-A"]}
             onSelect={() => navigate("/relays/sel-mv-main-A")} />
      <Relay x={490} y={210} id="sel-mv-main-B" relay={relays["sel-mv-main-B"]}
             onSelect={() => navigate("/relays/sel-mv-main-B")} />

      {/* Wires from mv mains down to MV bus */}
      <Wire d="M120 220 L120 300" live={energized("mv-main-A")} />
      <Wire d="M420 220 L420 300" live={energized("mv-main-B")} />

      {/* === MV BUS A and B === */}
      <Bus y={300} x1={70} x2={300} bus={buses["mv-bus-A"]} label="MV BUS A · 13.8 kV"
           onSelect={() => onSelect({type:"bus",id:"mv-bus-A"})} />
      <Bus y={300} x1={370} x2={600} bus={buses["mv-bus-B"]} label="MV BUS B · 13.8 kV"
           onSelect={() => onSelect({type:"bus",id:"mv-bus-B"})} />

      {/* MV tie between the two buses */}
      <Wire d="M300 300 L320 300" live={energized("mv-tie")} />
      <Breaker x={335} y={300} state={breakers["mv-tie"]?.position} horizontal
               label="MV-TIE" rating="1200A"
               onSelect={() => onSelect({type:"breaker",id:"mv-tie"})} />
      <Wire d="M350 300 L370 300" live={energized("mv-tie")} />

      {/* InsulGard PD monitors floating off each MV bus */}
      <PDMon x={70} y={345} label="InsulGard PD · MV-A" />
      <PDMon x={530} y={345} label="InsulGard PD · MV-B" />

      {/* === Drop to transformers === */}
      {[
        { id: "xfmr-A1", x: 110 },
        { id: "xfmr-A2", x: 220 },
        { id: "xfmr-B1", x: 410 },
        { id: "xfmr-B2", x: 520 },
      ].map(({ id, x }) => (
        <g key={id}>
          <Wire d={`M${x} 300 L${x} 380`} live={t.xfmrs[id] !== undefined} />
          <Xfmr x={x} y={400} id={id} xfmr={t.xfmrs[id]}
                onSelect={() => onSelect({type:"xfmr",id})} />
          <Wire d={`M${x} 460 L${x} 540`} live />
          {/* MTZ incoming for each */}
          <Breaker x={x} y={550} state={breakers[`mtz-inc-${idToSide(id)}`]?.position}
                   label={`MTZ-INC-${idToSide(id)}`} rating="4000A" small
                   onSelect={() => onSelect({type:"breaker",id:`mtz-inc-${idToSide(id)}`})} />
          <Wire d={`M${x} 600 L${x} 660`} live={energized(`mtz-inc-${idToSide(id)}`)} />
        </g>
      ))}

      {/* === LV buses === */}
      {[
        { id: "lv-bus-A1", x: 110 },
        { id: "lv-bus-A2", x: 220 },
        { id: "lv-bus-B1", x: 410 },
        { id: "lv-bus-B2", x: 520 },
      ].map(({ id, x }) => (
        <g key={id}>
          <Bus y={680} x1={x - 50} x2={x + 50} bus={buses[id]} label={`${id.toUpperCase()} · 480V`}
               compact onSelect={() => onSelect({type:"bus",id})} />
          {/* feeders coming off each LV bus */}
          {[1, 2, 3, 4].map((f) => {
            const fx = x - 40 + (f - 1) * 27;
            return (
              <g key={f}>
                <Wire d={`M${fx} 700 L${fx} 730`} live />
                <FeederBreaker x={fx} y={740} fid={f} sideid={id.split("-").slice(2)[0]} />
                <CTMeter x={fx} y={780} label={`F${f}`} />
              </g>
            );
          })}
        </g>
      ))}

      {/* === ATS + UPS chain (right side) === */}
      <UtilSource x={680} y={60} label="GEN PARALLELING · 1500kW × 2" mini />
      <Wire d="M680 90 L680 540" live />
      <Wire d="M680 540 L820 540" live />
      <ATS x={830} y={550} id="ats-1" onSelect={() => onSelect({type:"ats",id:"ats-1"})} />
      <ATS x={970} y={550} id="ats-2" onSelect={() => onSelect({type:"ats",id:"ats-2"})} />
      <Wire d="M860 600 L860 650" live />
      <Wire d="M1000 600 L1000 650" live />
      <Ups x={830} y={660} id="ups-A" ups={t.upses["ups-A"]} onSelect={() => onSelect({type:"ups",id:"ups-A"})} />
      <Ups x={970} y={660} id="ups-B" ups={t.upses["ups-B"]} onSelect={() => onSelect({type:"ups",id:"ups-B"})} />

      {/* Generator panel */}
      <GenBlock x={1140} y={70} id="gen-1" gen={t.gens["gen-1"]} onSelect={() => onSelect({type:"gen",id:"gen-1"})} />
      <GenBlock x={1140} y={250} id="gen-2" gen={t.gens["gen-2"]} onSelect={() => onSelect({type:"gen",id:"gen-2"})} />

      {/* Static legend */}
      <Legend x={1380} y={60} />
    </svg>
  );
}

function idToSide(xfmrId) {
  // xfmr-A1 -> A1, xfmr-B2 -> B2
  return xfmrId.split("-")[1];
}

// ---------------------------------------------------------------------------
// SVG primitives
// ---------------------------------------------------------------------------

function UtilSource({ x, y, label, mini }) {
  return (
    <g transform={`translate(${x - 50},${y - 30})`}>
      <rect width="100" height="36" rx="4"
            fill="var(--bg-2)" stroke="var(--accent)" strokeWidth="1" />
      <circle cx="14" cy="18" r="6" fill="var(--accent)" />
      <text x="28" y="14" fontSize="9" fill="var(--text-faint)">UTILITY</text>
      <text x="28" y="26" fontSize="10" fill="var(--text)" fontFamily="monospace">{label}</text>
    </g>
  );
}

function Wire({ d, live }) {
  return (
    <path d={d}
          stroke={live ? "var(--pass)" : "var(--text-faint)"}
          strokeWidth={live ? 2.4 : 1.4}
          fill="none"
          strokeDasharray={live ? "none" : "4 4"} />
  );
}

function Breaker({ x, y, state, label, rating, horizontal, onSelect, small }) {
  const status = state || "racked_out";
  const fill = STATE_FILL[status] || "var(--bg-2)";
  const stroke = STATE_STROKE[status] || "var(--text-faint)";
  const r = small ? 12 : 16;
  return (
    <g style={{ cursor: "pointer" }} onClick={onSelect}>
      <circle cx={x} cy={y} r={r} fill={fill} stroke={stroke} strokeWidth="2" />
      {/* X marker for closed */}
      {status === "closed" && (
        <>
          <line x1={x - r * 0.6} y1={y - r * 0.6} x2={x + r * 0.6} y2={y + r * 0.6}
                stroke="var(--bg-0)" strokeWidth="2" />
          <line x1={x + r * 0.6} y1={y - r * 0.6} x2={x - r * 0.6} y2={y + r * 0.6}
                stroke="var(--bg-0)" strokeWidth="2" />
        </>
      )}
      {status === "tripped" && (
        <text x={x} y={y + 4} fontSize="11" fill="white" textAnchor="middle" fontWeight="700">!</text>
      )}
      <text x={x} y={y + r + 14} fontSize="9.5" fill="var(--text)" textAnchor="middle"
            fontFamily="monospace">{label}</text>
      <text x={x} y={y + r + 25} fontSize="9" fill="var(--text-faint)" textAnchor="middle">
        {rating} · <tspan fill={stroke}>{status}</tspan>
      </text>
    </g>
  );
}

const STATE_FILL = {
  closed: "var(--pass)", open: "var(--bg-2)",
  tripped: "var(--fail)", racked_out: "var(--bg-2)",
};
const STATE_STROKE = {
  closed: "var(--pass)", open: "var(--text-faint)",
  tripped: "var(--fail)", racked_out: "var(--text-faint)",
};

function Bus({ y, x1, x2, bus, label, onSelect, compact }) {
  return (
    <g style={{ cursor: "pointer" }} onClick={onSelect}>
      <line x1={x1} y1={y} x2={x2} y2={y} stroke="var(--text)" strokeWidth="6"
            strokeLinecap="round" />
      <text x={x1} y={y - 12} fontSize={compact ? 9 : 10} fill="var(--text-faint)"
            fontFamily="monospace">{label}</text>
      {bus && (
        <text x={x2} y={y - 12} fontSize={compact ? 9 : 10} fill="var(--text)"
              textAnchor="end" fontFamily="monospace">
          {Math.round(bus.voltage_ll_v).toLocaleString()} V · {Math.round(bus.current_a)} A · {bus.frequency_hz.toFixed(2)} Hz
        </text>
      )}
    </g>
  );
}

function Relay({ x, y, id, relay, onSelect }) {
  const tripped = relay?.last_trip_cause;
  return (
    <g style={{ cursor: "pointer" }} onClick={onSelect}>
      <rect x={x} y={y} width="64" height="34" rx="3"
            fill="var(--bg-2)"
            stroke={tripped ? "var(--fail)" : "var(--text-faint)"} strokeWidth="1.4" />
      <text x={x + 32} y={y + 13} fontSize="9" fill="var(--text-faint)" textAnchor="middle">SEL-751</text>
      <text x={x + 32} y={y + 25} fontSize="9.5" fill={tripped ? "var(--fail)" : "var(--text)"}
            textAnchor="middle" fontFamily="monospace">
        {tripped ? "TRIPPED" : "armed"}
      </text>
    </g>
  );
}

function PDMon({ x, y, label }) {
  return (
    <g>
      <rect x={x} y={y} width="92" height="22" rx="3"
            fill="var(--bg-2)" stroke="var(--text-faint)" />
      <text x={x + 46} y={y + 14} fontSize="9" fill="var(--text-dim)" textAnchor="middle">
        {label}
      </text>
    </g>
  );
}

function Xfmr({ x, y, id, xfmr, onSelect }) {
  // Two interlocking circles — IEEE transformer symbol
  const r = 14;
  const dga_warn = xfmr && xfmr.c2h2_ppm > 2;
  return (
    <g style={{ cursor: "pointer" }} onClick={onSelect}>
      <circle cx={x} cy={y - 8} r={r} fill="var(--bg-2)"
              stroke={dga_warn ? "var(--fail)" : "var(--accent)"} strokeWidth="2" />
      <circle cx={x} cy={y + 8} r={r} fill="var(--bg-2)"
              stroke={dga_warn ? "var(--fail)" : "var(--accent)"} strokeWidth="2" />
      <text x={x} y={y + 38} fontSize="9.5" fill="var(--text)" textAnchor="middle"
            fontFamily="monospace">{id.toUpperCase()}</text>
      <text x={x} y={y + 50} fontSize="8.5" fill="var(--text-faint)" textAnchor="middle">
        2500 kVA · 13.8/0.48 kV
      </text>
      {xfmr && (
        <text x={x} y={y + 62} fontSize="9" fill={dga_warn ? "var(--fail)" : "var(--text-dim)"}
              textAnchor="middle" fontFamily="monospace">
          W:{xfmr.winding_temp_c.toFixed(0)}°C · C₂H₂:{xfmr.c2h2_ppm.toFixed(1)}ppm
        </text>
      )}
    </g>
  );
}

function FeederBreaker({ x, y, fid, sideid }) {
  return (
    <g>
      <circle cx={x} cy={y} r="8" fill="var(--pass)" />
      <line x1={x - 4} y1={y - 4} x2={x + 4} y2={y + 4} stroke="var(--bg-0)" strokeWidth="1.6" />
      <line x1={x + 4} y1={y - 4} x2={x - 4} y2={y + 4} stroke="var(--bg-0)" strokeWidth="1.6" />
    </g>
  );
}

function CTMeter({ x, y, label }) {
  return (
    <g>
      <rect x={x - 12} y={y - 6} width="24" height="12" rx="2"
            fill="var(--bg-2)" stroke="var(--accent)" strokeWidth="1" />
      <text x={x} y={y + 4} fontSize="8" fill="var(--text-faint)" textAnchor="middle">{label}</text>
    </g>
  );
}

function ATS({ x, y, id, onSelect }) {
  return (
    <g style={{ cursor: "pointer" }} onClick={onSelect}>
      <rect x={x} y={y - 10} width="60" height="50" rx="3" fill="var(--bg-2)"
            stroke="var(--accent)" strokeWidth="1.4" />
      <text x={x + 30} y={y + 4} fontSize="10" fill="var(--text-faint)" textAnchor="middle">ATS</text>
      <text x={x + 30} y={y + 18} fontSize="11" fill="var(--text)" textAnchor="middle"
            fontFamily="monospace">{id.toUpperCase()}</text>
      <text x={x + 30} y={y + 32} fontSize="9" fill="var(--pass)" textAnchor="middle">SOURCE 1</text>
    </g>
  );
}

function Ups({ x, y, id, ups, onSelect }) {
  return (
    <g style={{ cursor: "pointer" }} onClick={onSelect}>
      <rect x={x} y={y} width="60" height="60" rx="3" fill="var(--bg-2)"
            stroke="var(--accent)" strokeWidth="1.4" />
      <text x={x + 30} y={y + 14} fontSize="10" fill="var(--text-faint)" textAnchor="middle">UPS</text>
      <text x={x + 30} y={y + 28} fontSize="11" fill="var(--text)" textAnchor="middle"
            fontFamily="monospace">{id.toUpperCase()}</text>
      {ups && (
        <>
          <text x={x + 30} y={y + 42} fontSize="9" fill="var(--text-dim)" textAnchor="middle">
            {ups.battery_pct.toFixed(0)}% · {ups.load_pct.toFixed(0)}% load
          </text>
          <text x={x + 30} y={y + 54} fontSize="9" fill="var(--pass)" textAnchor="middle">
            {ups.state}
          </text>
        </>
      )}
    </g>
  );
}

function GenBlock({ x, y, id, gen, onSelect }) {
  return (
    <g style={{ cursor: "pointer" }} onClick={onSelect}>
      <rect x={x} y={y} width="220" height="150" rx="4" fill="var(--bg-2)"
            stroke="var(--accent)" strokeWidth="1.4" />
      <text x={x + 12} y={y + 18} fontSize="11" fill="var(--text-faint)">GEN · {id.toUpperCase()}</text>
      <text x={x + 12} y={y + 38} fontSize="13" fill="var(--text)" fontFamily="monospace">
        Caterpillar 3516B · 1500 kW
      </text>
      {gen && (
        <>
          <text x={x + 12} y={y + 60} fontSize="10" fill="var(--text-dim)" fontFamily="monospace">
            STATE: <tspan fill={gen.state === "running" ? "var(--pass)" : "var(--queued)"}>{gen.state}</tspan>
          </text>
          <text x={x + 12} y={y + 78} fontSize="10" fill="var(--text-dim)" fontFamily="monospace">
            RPM: {gen.rpm.toFixed(0)}     V: {gen.voltage_ll_v.toFixed(0)}     Hz: {gen.frequency_hz.toFixed(2)}
          </text>
          <text x={x + 12} y={y + 96} fontSize="10" fill="var(--text-dim)" fontFamily="monospace">
            OIL: {gen.oil_pressure_psi.toFixed(1)} psi
          </text>
          <text x={x + 12} y={y + 110} fontSize="10" fill="var(--text-dim)" fontFamily="monospace">
            COOL: {gen.coolant_temp_c.toFixed(0)} °C
          </text>
          <text x={x + 12} y={y + 124} fontSize="10" fill="var(--text-dim)" fontFamily="monospace">
            FUEL: {gen.fuel_level_pct.toFixed(1)} %
          </text>
          <text x={x + 12} y={y + 138} fontSize="10" fill="var(--text-dim)" fontFamily="monospace">
            RUN: {gen.runtime_hours.toFixed(0)} h
          </text>
        </>
      )}
    </g>
  );
}

function Legend({ x, y }) {
  return (
    <g transform={`translate(${x},${y})`}>
      <rect width="200" height="180" rx="4" fill="var(--bg-1)"
            stroke="var(--border)" strokeWidth="1" />
      <text x="14" y="20" fontSize="11" fill="var(--text-faint)" fontWeight="600">LEGEND</text>
      <circle cx="22" cy="42" r="8" fill="var(--pass)" />
      <text x="40" y="46" fontSize="10" fill="var(--text)">closed (energized)</text>
      <circle cx="22" cy="64" r="8" fill="var(--bg-2)" stroke="var(--text-faint)" strokeWidth="1.5" />
      <text x="40" y="68" fontSize="10" fill="var(--text)">open</text>
      <circle cx="22" cy="86" r="8" fill="var(--fail)" />
      <text x="40" y="90" fontSize="10" fill="var(--text)">tripped</text>
      <line x1="14" y1="110" x2="32" y2="110" stroke="var(--pass)" strokeWidth="2.4" />
      <text x="40" y="114" fontSize="10" fill="var(--text)">live conductor</text>
      <line x1="14" y1="130" x2="32" y2="130" stroke="var(--text-faint)"
            strokeWidth="1.4" strokeDasharray="4 4" />
      <text x="40" y="134" fontSize="10" fill="var(--text)">de-energized</text>
      <text x="14" y="160" fontSize="10" fill="var(--text-faint)">click any element →</text>
    </g>
  );
}

// ---------------------------------------------------------------------------
// Detail panel — what's selected
// ---------------------------------------------------------------------------

function DetailPanel({ t, selected }) {
  if (!selected) return (
    <div className="card" style={{ width: 320 }}>
      <div className="card-h">Selection</div>
      <p style={{ color: "var(--text-faint)" }}>Click any element on the SLD.</p>
    </div>
  );

  if (selected.type === "bus") {
    const b = t.buses[selected.id]; if (!b) return null;
    return (
      <div className="card" style={{ width: 320 }}>
        <div className="card-h">Bus · {selected.id}</div>
        <Metric label="V (line-line)" value={`${b.voltage_ll_v.toFixed(0)} V`} />
        <Metric label="I (avg)"        value={`${b.current_a.toFixed(0)} A`} />
        <Metric label="Real power"     value={`${b.real_power_kw.toFixed(0)} kW`} />
        <Metric label="Reactive"       value={`${b.reactive_power_kvar.toFixed(0)} kVAR`} />
        <Metric label="Power factor"   value={b.power_factor.toFixed(3)} />
        <Metric label="Frequency"      value={`${b.frequency_hz.toFixed(3)} Hz`} />
        <Metric label="V THD"          value={`${b.thd_voltage_pct.toFixed(2)} %`} />
      </div>
    );
  }
  if (selected.type === "breaker") {
    const b = t.breakers[selected.id]; if (!b) return null;
    return (
      <div className="card" style={{ width: 320 }}>
        <div className="card-h">Breaker · {selected.id}</div>
        <Metric label="Position"        value={<span className={`badge badge-${b.position === "closed" ? "passed" : (b.position === "tripped" ? "failed" : "info")}`}>{b.position}</span>} />
        <Metric label="Rating"          value={`${b.rating_amps} A`} />
        <Metric label="Spring"          value={b.spring_charged ? "charged" : "uncharged"} />
        <Metric label="Contact wear"    value={`${b.contact_wear_pct} %`} />
        <Metric label="Op count"        value={b.ops_count} />
      </div>
    );
  }
  if (selected.type === "xfmr") {
    const x = t.xfmrs[selected.id]; if (!x) return null;
    const arc = x.c2h2_ppm > 2;
    return (
      <div className="card" style={{ width: 320 }}>
        <div className="card-h">Transformer · {selected.id}</div>
        <Metric label="Winding temp"    value={`${x.winding_temp_c.toFixed(1)} °C`} />
        <Metric label="Oil temp"        value={`${x.oil_temp_c.toFixed(1)} °C`} />
        <Metric label="H₂ (DGA)"        value={`${x.h2_ppm.toFixed(0)} ppm`} />
        <Metric label="CH₄ (DGA)"       value={`${x.ch4_ppm.toFixed(1)} ppm`} />
        <Metric label="C₂H₂ (DGA)"     value={
          <span style={{ color: arc ? "var(--fail)" : "var(--text)" }}>
            {x.c2h2_ppm.toFixed(2)} ppm {arc && " · ACTIVE ARCING"}
          </span>
        } />
        <Metric label="Moisture"        value={`${x.moisture_ppm.toFixed(1)} ppm`} />
        <Metric label="PD magnitude"    value={`${x.pd_magnitude_pc.toFixed(1)} pC`} />
      </div>
    );
  }
  if (selected.type === "ups") {
    const u = t.upses[selected.id]; if (!u) return null;
    return (
      <div className="card" style={{ width: 320 }}>
        <div className="card-h">UPS · {selected.id}</div>
        <Metric label="State"           value={<span className="badge badge-passed">{u.state}</span>} />
        <Metric label="Input V"         value={`${u.input_voltage_v.toFixed(1)} V`} />
        <Metric label="Output V"        value={`${u.output_voltage_v.toFixed(1)} V`} />
        <Metric label="Battery"         value={`${u.battery_pct.toFixed(1)} %`} />
        <Metric label="Runtime"         value={`${u.runtime_minutes.toFixed(1)} min`} />
        <Metric label="Load"            value={`${u.load_pct.toFixed(1)} %`} />
      </div>
    );
  }
  if (selected.type === "gen") {
    const g = t.gens[selected.id]; if (!g) return null;
    return (
      <div className="card" style={{ width: 320 }}>
        <div className="card-h">Generator · {selected.id}</div>
        <Metric label="State"           value={<span className="badge badge-queued">{g.state}</span>} />
        <Metric label="RPM"             value={g.rpm.toFixed(0)} />
        <Metric label="V"               value={`${g.voltage_ll_v.toFixed(1)} V`} />
        <Metric label="Freq"            value={`${g.frequency_hz.toFixed(3)} Hz`} />
        <Metric label="Oil pressure"    value={`${g.oil_pressure_psi.toFixed(2)} psi`} />
        <Metric label="Coolant temp"    value={`${g.coolant_temp_c.toFixed(1)} °C`} />
        <Metric label="Fuel"            value={`${g.fuel_level_pct.toFixed(1)} %`} />
        <Metric label="Runtime"         value={`${g.runtime_hours.toFixed(0)} h`} />
      </div>
    );
  }
  if (selected.type === "ats") {
    return (
      <div className="card" style={{ width: 320 }}>
        <div className="card-h">ATS · {selected.id}</div>
        <Metric label="Source"          value={<span className="badge badge-passed">SOURCE 1</span>} />
        <Metric label="Position"        value="Normal" />
        <Metric label="Test mode"       value="Off" />
      </div>
    );
  }
  return null;
}

function Metric({ label, value }) {
  return (
    <div style={{ display: "grid", gridTemplateColumns: "120px 1fr",
                  gap: 8, padding: "5px 0", borderBottom: "1px solid var(--border)" }}>
      <div style={{ color: "var(--text-faint)", fontSize: 11 }}>{label}</div>
      <div style={{ fontSize: 12, fontFamily: "monospace" }}>{value}</div>
    </div>
  );
}
