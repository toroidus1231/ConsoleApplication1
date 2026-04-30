// Industrial-HMI renders for each equipment type. Used by the right-rail
// detail panel when the user toggles SCHEMATIC -> EQUIPMENT.
//
// These are stylized faceplates, not photorealistic — but use real industrial
// conventions (HV/LV bushings, conservator tanks, breaker status LEDs,
// UPS bypass paths, ATS source selectors).

import React from "react";

const W = 300, H = 360;

export default function EquipmentHMI({ type, id, data }) {
  switch (type) {
    case "xfmr":    return <XfmrHMI id={id} d={data} />;
    case "breaker": return <BreakerHMI id={id} d={data} />;
    case "gen":     return <GenHMI id={id} d={data} />;
    case "ups":     return <UpsHMI id={id} d={data} />;
    case "ats":     return <AtsHMI id={id} d={data} />;
    case "bus":     return <BusHMI id={id} d={data} />;
    case "relay":   return <RelayHMI id={id} d={data} />;
    case "source":  return <SourceHMI id={id} d={data} />;
    default:        return null;
  }
}

// Helpers
const fmt = (n, d=1) => n == null || isNaN(n) ? "—" : n.toFixed(d);
const Frame = ({ children, label }) => (
  <div className="hmi-frame">
    <div className="hmi-label">{label}</div>
    <svg viewBox={`0 0 ${W} ${H}`} className="hmi-svg">{children}</svg>
  </div>
);

// ---------------------------------------------------------------------------
// Transformer — oil-filled, three-phase, ONAN-cooled
// HV bushings on top (13.8 kV), LV bushings on side (480 V), conservator
// tank, radiator fins on both flanks, oil-temp gauge, ground stud.
// ---------------------------------------------------------------------------
function XfmrHMI({ id, d }) {
  const oilT = d?.oil_temp_c, windT = d?.winding_temp_c;
  const c2h2 = d?.c2h2_ppm ?? 0;
  const arc = c2h2 > 2;
  const oilPct = Math.max(0, Math.min(1, (oilT - 20) / 80));
  const tankFill = arc ? "var(--fail-dim)" : "rgba(60, 90, 120, 0.35)";
  const tankStroke = arc ? "var(--fail)" : "var(--text-secondary)";

  return (
    <Frame label={`${id?.toUpperCase()} · 2500 kVA · 13.8/0.48 kV · ONAN`}>
      {/* HV bushings (3-phase, top) */}
      {[110, 150, 190].map((x, i) => (
        <g key={`hv${i}`}>
          <line x1={x} y1={20} x2={x} y2={70} stroke="var(--text-tertiary)" strokeWidth="2.4" />
          {[28, 36, 44, 52, 60].map((y, j) => (
            <ellipse key={j} cx={x} cy={y} rx="6" ry="2.5"
                     fill="var(--bg-elev-2)" stroke="var(--text-tertiary)" strokeWidth="1" />
          ))}
        </g>
      ))}
      <text x="150" y="14" textAnchor="middle" className="hmi-tag">HV · 13.8 kV</text>

      {/* Conservator tank (oil expansion) */}
      <rect x="105" y="68" width="90" height="14" rx="4"
            fill="var(--bg-elev-2)" stroke="var(--text-tertiary)" strokeWidth="1.2" />
      <line x1="150" y1="82" x2="150" y2="92" stroke="var(--text-tertiary)" strokeWidth="1.5" />

      {/* Main tank */}
      <rect x="60" y="92" width="180" height="180" rx="6"
            fill={tankFill} stroke={tankStroke} strokeWidth="2" />

      {/* Oil-level window */}
      <rect x="218" y="110" width="14" height="60" rx="2"
            fill="var(--bg-base)" stroke="var(--text-tertiary)" strokeWidth="1" />
      <rect x="218" y={110 + 60 * (1 - oilPct)} width="14" height={60 * oilPct}
            fill="rgba(120, 200, 255, 0.55)" />

      {/* Winding-temp gauge (left) */}
      <circle cx="80" cy="140" r="14"
              fill="var(--bg-elev-2)" stroke={arc ? "var(--fail)" : "var(--pass)"} strokeWidth="1.5" />
      <text x="80" y="144" textAnchor="middle" className="hmi-gauge">{fmt(windT, 0)}</text>
      <text x="80" y="166" textAnchor="middle" className="hmi-tag">WIND °C</text>

      {/* Oil-temp gauge (right) */}
      <circle cx="80" cy="200" r="14"
              fill="var(--bg-elev-2)" stroke="var(--info)" strokeWidth="1.5" />
      <text x="80" y="204" textAnchor="middle" className="hmi-gauge">{fmt(oilT, 0)}</text>
      <text x="80" y="226" textAnchor="middle" className="hmi-tag">OIL °C</text>

      {/* Radiator fins (right flank) */}
      {[0, 1, 2, 3, 4, 5].map((i) => (
        <rect key={i} x={245} y={110 + i * 26} width="22" height="20" rx="1"
              fill="var(--bg-elev-2)" stroke="var(--text-tertiary)" strokeWidth="0.8" />
      ))}

      {/* DGA badge */}
      <rect x="120" y="186" width="100" height="40" rx="4"
            fill={arc ? "var(--fail-dim)" : "var(--bg-elev-2)"}
            stroke={arc ? "var(--fail)" : "var(--border-subtle)"} strokeWidth="1" />
      <text x="170" y="200" textAnchor="middle" className="hmi-tag">C₂H₂ DGA</text>
      <text x="170" y="218" textAnchor="middle"
            className={arc ? "hmi-val-fail" : "hmi-val"}>{fmt(c2h2, 2)} ppm</text>

      {/* LV bushings (3-phase, bottom) */}
      {[110, 150, 190].map((x, i) => (
        <g key={`lv${i}`}>
          <line x1={x} y1={272} x2={x} y2={310} stroke="var(--text-tertiary)" strokeWidth="2" />
          {[280, 290, 300].map((y, j) => (
            <ellipse key={j} cx={x} cy={y} rx="5" ry="2"
                     fill="var(--bg-elev-2)" stroke="var(--text-tertiary)" strokeWidth="1" />
          ))}
        </g>
      ))}
      <text x="150" y="326" textAnchor="middle" className="hmi-tag">LV · 480 V</text>

      {/* Ground stud */}
      <line x1="60" y1="272" x2="40" y2="296" stroke="var(--text-tertiary)" strokeWidth="1.6" />
      <line x1="34" y1="296" x2="46" y2="296" stroke="var(--text-tertiary)" strokeWidth="2" />
      <line x1="36" y1="300" x2="44" y2="300" stroke="var(--text-tertiary)" strokeWidth="2" />
      <line x1="38" y1="304" x2="42" y2="304" stroke="var(--text-tertiary)" strokeWidth="2" />
      <text x="22" y="316" className="hmi-tag">GND</text>

      {arc && <text x="150" y="346" textAnchor="middle" className="hmi-alarm">⚠ ACTIVE ARCING — STOP TESTING</text>}
    </Frame>
  );
}

// ---------------------------------------------------------------------------
// Breaker — draw-out vacuum cubicle faceplate
// Status LEDs (CLOSED / OPEN / SPRING / READY / TRIP), mechanical position
// flag, racking handle slot, ops counter, close/trip pushbuttons.
// ---------------------------------------------------------------------------
function BreakerHMI({ id, d }) {
  const pos = (d?.position || "racked_out").toLowerCase();
  const closed = pos === "closed";
  const tripped = pos === "tripped";
  const racked = pos !== "racked_out";
  const charged = !!d?.spring_charged;
  const wear = d?.contact_wear_pct ?? 0;
  const ops = d?.ops_count ?? 0;

  const led = (active, color, x, y, label) => (
    <g>
      <circle cx={x} cy={y} r="6"
              fill={active ? color : "var(--bg-elev-2)"}
              stroke={active ? color : "var(--text-faint)"} strokeWidth="1.3"
              style={active ? { filter: `drop-shadow(0 0 4px ${color})` } : null} />
      <text x={x + 14} y={y + 4} className="hmi-tag">{label}</text>
    </g>
  );

  return (
    <Frame label={`${id?.toUpperCase()} · ${d?.rating_amps || "—"}A vacuum CB`}>
      {/* Cubicle outline */}
      <rect x="30" y="20" width="240" height="320" rx="6"
            fill="var(--bg-elev-1)" stroke="var(--text-tertiary)" strokeWidth="1.6" />
      <rect x="40" y="32" width="220" height="22" rx="3"
            fill="var(--bg-elev-2)" stroke="var(--border-subtle)" strokeWidth="1" />
      <text x="150" y="48" textAnchor="middle" className="hmi-tag">METAL-CLAD CUBICLE</text>

      {/* Mechanical position window */}
      <rect x="50" y="70" width="200" height="44" rx="4"
            fill="var(--bg-base)" stroke="var(--text-tertiary)" strokeWidth="1.2" />
      <text x="60" y="86" className="hmi-tag">MECH POS</text>
      <text x="150" y="100" textAnchor="middle"
            className={tripped ? "hmi-val-fail" : closed ? "hmi-val-pass" : "hmi-val"}
            style={{ fontSize: 18, fontWeight: 700 }}>
        {pos.toUpperCase().replace("_", " ")}
      </text>

      {/* Status LED column */}
      {led(closed, "var(--pass)", 60, 134, "BREAKER CLOSED")}
      {led(!closed && pos === "open", "var(--text-tertiary)", 60, 154, "BREAKER OPEN")}
      {led(tripped, "var(--fail)", 60, 174, "TRIP / LOCKOUT")}
      {led(charged, "var(--info)", 60, 194, "SPRING CHARGED")}
      {led(racked, "var(--cyan)", 60, 214, "RACKED IN")}
      {led(racked && charged && !tripped, "var(--pass)", 60, 234, "READY")}

      {/* Ops counter / contact wear */}
      <rect x="180" y="128" width="80" height="56" rx="4"
            fill="var(--bg-base)" stroke="var(--border-subtle)" strokeWidth="1" />
      <text x="220" y="142" textAnchor="middle" className="hmi-tag">OPERATIONS</text>
      <text x="220" y="160" textAnchor="middle" className="hmi-counter">{ops}</text>
      <text x="220" y="178" textAnchor="middle" className="hmi-tag">{`wear ${wear}%`}</text>

      {/* Pushbuttons */}
      <circle cx="200" cy="220" r="12"
              fill="var(--pass-dim)" stroke="var(--pass)" strokeWidth="1.5" />
      <text x="200" y="225" textAnchor="middle" className="hmi-btn">I</text>
      <text x="200" y="244" textAnchor="middle" className="hmi-tag">CLOSE</text>

      <circle cx="240" cy="220" r="12"
              fill="var(--fail-dim)" stroke="var(--fail)" strokeWidth="1.5" />
      <text x="240" y="225" textAnchor="middle" className="hmi-btn">O</text>
      <text x="240" y="244" textAnchor="middle" className="hmi-tag">TRIP</text>

      {/* Racking handle slot */}
      <rect x="40" y="270" width="220" height="40" rx="3"
            fill="var(--bg-elev-2)" stroke="var(--border-subtle)" strokeWidth="1" />
      <text x="50" y="284" className="hmi-tag">RACKING</text>
      <line x1="50" y1="295" x2="250" y2="295" stroke="var(--text-faint)" strokeWidth="1" strokeDasharray="3 3" />
      <circle cx={racked ? 240 : 60} cy="295" r="5"
              fill="var(--accent)" stroke="var(--accent)" strokeWidth="1" />
      <text x="60" y="308" className="hmi-tag">DISCONNECTED</text>
      <text x="240" y="308" textAnchor="end" className="hmi-tag">CONNECTED</text>
    </Frame>
  );
}

// ---------------------------------------------------------------------------
// Generator — Cat 3516B skid: engine block + alternator + radiator + control
// panel with tachometer, voltmeter, freq meter, and status indicators.
// ---------------------------------------------------------------------------
function GenHMI({ id, d }) {
  const running = d?.state === "running";
  const rpm = d?.rpm ?? 0;
  const v = d?.voltage_ll_v ?? 0;
  const f = d?.frequency_hz ?? 0;
  const oil = d?.oil_pressure_psi ?? 0;
  const cool = d?.coolant_temp_c ?? 0;
  const fuel = d?.fuel_level_pct ?? 0;

  // Tachometer needle: 0 RPM → -135°, 1800 RPM → +135°
  const tachAngle = -135 + Math.min(1, rpm / 1800) * 270;
  const cx = 60, cy = 220, R = 28;
  const needleX = cx + R * 0.85 * Math.cos((tachAngle - 90) * Math.PI / 180);
  const needleY = cy + R * 0.85 * Math.sin((tachAngle - 90) * Math.PI / 180);

  return (
    <Frame label={`${id?.toUpperCase()} · CAT 3516B · 1500 kW · diesel`}>
      {/* Skid base */}
      <rect x="20" y="40" width="260" height="120" rx="4"
            fill="var(--bg-elev-1)" stroke="var(--text-tertiary)" strokeWidth="1.4" />

      {/* Radiator (left) */}
      <rect x="26" y="50" width="36" height="100" rx="2"
            fill="var(--bg-elev-2)" stroke="var(--text-tertiary)" strokeWidth="1" />
      {[0, 1, 2, 3, 4, 5, 6].map((i) => (
        <line key={i} x1="30" y1={56 + i * 14} x2="58" y2={56 + i * 14}
              stroke="var(--text-faint)" strokeWidth="1" />
      ))}
      <text x="44" y="160" textAnchor="middle" className="hmi-tag">RAD</text>

      {/* V16 engine block */}
      <rect x="70" y="62" width="120" height="80" rx="3"
            fill={running ? "var(--warn-dim)" : "var(--bg-elev-2)"}
            stroke={running ? "var(--warn)" : "var(--text-tertiary)"} strokeWidth="1.4" />
      {/* Cylinder banks (V16 = two banks of 8) */}
      {[0, 1, 2, 3, 4, 5, 6, 7].map((i) => (
        <g key={i}>
          <rect x={76 + i * 14} y="68" width="10" height="14" rx="1"
                fill="var(--bg-base)" stroke="var(--text-faint)" strokeWidth="0.7" />
          <rect x={76 + i * 14} y="122" width="10" height="14" rx="1"
                fill="var(--bg-base)" stroke="var(--text-faint)" strokeWidth="0.7" />
        </g>
      ))}
      <text x="130" y="106" textAnchor="middle" className="hmi-tag" style={{ fontSize: 9 }}>
        V16 · 78.1 L
      </text>

      {/* Alternator (right) */}
      <rect x="196" y="60" width="76" height="84" rx="3"
            fill="var(--bg-elev-2)" stroke="var(--text-tertiary)" strokeWidth="1.4" />
      <circle cx="234" cy="102" r="22"
              fill="var(--bg-base)" stroke="var(--accent)" strokeWidth="1.2" />
      <circle cx="234" cy="102" r="14"
              fill="var(--bg-elev-2)" stroke="var(--text-tertiary)" strokeWidth="1" />
      <text x="234" y="106" textAnchor="middle" className="hmi-tag">ALT</text>
      <text x="234" y="156" textAnchor="middle" className="hmi-tag">1500 kW</text>

      {/* Coupling */}
      <rect x="186" y="98" width="14" height="12"
            fill="var(--bg-base)" stroke="var(--text-faint)" strokeWidth="0.8" />

      {/* Control panel */}
      <rect x="20" y="170" width="260" height="180" rx="4"
            fill="var(--bg-elev-1)" stroke="var(--text-tertiary)" strokeWidth="1.4" />
      <rect x="26" y="176" width="248" height="20" rx="2"
            fill="var(--bg-elev-2)" stroke="var(--border-subtle)" strokeWidth="1" />
      <text x="150" y="190" textAnchor="middle" className="hmi-tag">EMCP 4.4 CONTROL PANEL</text>

      {/* Tachometer */}
      <circle cx={cx} cy={cy} r={R}
              fill="var(--bg-base)" stroke="var(--text-tertiary)" strokeWidth="1.2" />
      <line x1={cx} y1={cy} x2={needleX} y2={needleY}
            stroke={running ? "var(--warn)" : "var(--text-faint)"} strokeWidth="2" />
      <circle cx={cx} cy={cy} r="2.5" fill="var(--text-tertiary)" />
      <text x={cx} y={cy + 42} textAnchor="middle" className="hmi-tag">RPM</text>
      <text x={cx} y={cy + 56} textAnchor="middle" className="hmi-counter">{fmt(rpm, 0)}</text>

      {/* Voltmeter / Freq display */}
      <rect x="110" y="200" width="80" height="40" rx="3"
            fill="var(--bg-base)" stroke="var(--info)" strokeWidth="1.2" />
      <text x="150" y="214" textAnchor="middle" className="hmi-tag">V_LL</text>
      <text x="150" y="232" textAnchor="middle" className="hmi-counter">{fmt(v, 0)} V</text>

      <rect x="200" y="200" width="70" height="40" rx="3"
            fill="var(--bg-base)" stroke="var(--info)" strokeWidth="1.2" />
      <text x="235" y="214" textAnchor="middle" className="hmi-tag">FREQ</text>
      <text x="235" y="232" textAnchor="middle" className="hmi-counter">{fmt(f, 2)}</text>

      {/* Indicator strip — right column, away from RPM gauge */}
      <text x="120" y="262" className="hmi-tag">OIL</text>
      <text x="200" y="262" className="hmi-val">{fmt(oil, 1)} psi</text>
      <text x="120" y="278" className="hmi-tag">COOLANT</text>
      <text x="200" y="278" className="hmi-val">{fmt(cool, 0)} °C</text>
      <text x="120" y="294" className="hmi-tag">FUEL</text>
      <text x="200" y="294" className="hmi-val">{fmt(fuel, 0)} %</text>

      {/* Status LEDs — placed below the tach so they don't collide with labels */}
      <circle cx="40" cy="312" r="6"
              fill={running ? "var(--pass)" : "var(--bg-elev-2)"}
              stroke={running ? "var(--pass)" : "var(--text-faint)"} strokeWidth="1.3"
              style={running ? { filter: "drop-shadow(0 0 4px var(--pass))" } : null} />
      <text x="52" y="316" className="hmi-tag">RUNNING</text>

      <circle cx="120" cy="312" r="6"
              fill={!running ? "var(--warn)" : "var(--bg-elev-2)"}
              stroke={!running ? "var(--warn)" : "var(--text-faint)"} strokeWidth="1.3" />
      <text x="132" y="316" className="hmi-tag">AUTO / STANDBY</text>

      {/* Big mushroom E-stop */}
      <circle cx="240" cy="320" r="14"
              fill="var(--fail)" stroke="#7a1010" strokeWidth="2" />
      <circle cx="240" cy="320" r="9" fill="#a31515" />
      <text x="240" y="338" textAnchor="middle" className="hmi-tag" style={{ fill: "var(--fail)" }}>E-STOP</text>
    </Frame>
  );
}

// ---------------------------------------------------------------------------
// UPS — double-conversion topology mimic: AC in → rectifier → DC link →
// inverter → AC out, with battery string and static bypass branch.
// ---------------------------------------------------------------------------
function UpsHMI({ id, d }) {
  const state = (d?.state || "online").toLowerCase();
  const onBattery = state === "battery";
  const onBypass = state === "bypass";
  const normal = state === "online";
  const bat = d?.battery_pct ?? 0;
  const load = d?.load_pct ?? 0;
  const inV = d?.input_voltage_v ?? 0;
  const outV = d?.output_voltage_v ?? 0;
  const runtime = d?.runtime_minutes ?? 0;

  const live = (active, color = "var(--pass)") => active ? color : "var(--text-faint)";
  const flowDash = (active) => active ? "0" : "4 4";

  return (
    <Frame label={`${id?.toUpperCase()} · 480 kVA · double-conversion`}>
      {/* Cabinet outline */}
      <rect x="20" y="20" width="260" height="320" rx="6"
            fill="var(--bg-elev-1)" stroke="var(--text-tertiary)" strokeWidth="1.4" />

      {/* AC INPUT */}
      <text x="40" y="60" className="hmi-tag">AC IN</text>
      <text x="40" y="76" className="hmi-val">{fmt(inV, 0)} V</text>

      {/* Rectifier block */}
      <rect x="80" y="50" width="50" height="40" rx="3"
            fill="var(--bg-elev-2)" stroke="var(--info)" strokeWidth="1.4" />
      <text x="105" y="68" textAnchor="middle" className="hmi-tag">RECT</text>
      <text x="105" y="84" textAnchor="middle" className="hmi-tag" style={{ fontSize: 9 }}>AC→DC</text>

      {/* DC link → inverter */}
      <rect x="160" y="50" width="50" height="40" rx="3"
            fill="var(--bg-elev-2)" stroke="var(--info)" strokeWidth="1.4" />
      <text x="185" y="68" textAnchor="middle" className="hmi-tag">INV</text>
      <text x="185" y="84" textAnchor="middle" className="hmi-tag" style={{ fontSize: 9 }}>DC→AC</text>

      {/* Output */}
      <text x="245" y="60" className="hmi-tag" textAnchor="middle">AC OUT</text>
      <text x="245" y="76" className="hmi-val" textAnchor="middle">{fmt(outV, 0)} V</text>

      {/* Conductor wires (active path coloring) */}
      <line x1="60" y1="70" x2="80" y2="70"
            stroke={live(!onBypass)} strokeWidth="2" strokeDasharray={flowDash(!onBypass)} />
      <line x1="130" y1="70" x2="160" y2="70"
            stroke={live(!onBypass)} strokeWidth="2" strokeDasharray={flowDash(!onBypass)} />
      <line x1="210" y1="70" x2="240" y2="70"
            stroke={live(true)} strokeWidth="2" />

      {/* Static bypass branch (above) */}
      <text x="20" y="116" className="hmi-tag">BYPASS</text>
      <line x1="60" y1="120" x2="80" y2="120"
            stroke={live(onBypass, "var(--warn)")} strokeWidth="2"
            strokeDasharray={flowDash(onBypass)} />
      <rect x="80" y="108" width="130" height="24" rx="3"
            fill={onBypass ? "var(--warn-dim)" : "var(--bg-elev-2)"}
            stroke={onBypass ? "var(--warn)" : "var(--text-tertiary)"} strokeWidth="1.2" />
      <text x="145" y="124" textAnchor="middle" className="hmi-tag">STATIC BYPASS SCR</text>
      <line x1="210" y1="120" x2="240" y2="120"
            stroke={live(onBypass, "var(--warn)")} strokeWidth="2"
            strokeDasharray={flowDash(onBypass)} />

      {/* Battery string (below) */}
      <text x="20" y="166" className="hmi-tag">BATTERY</text>
      <line x1="105" y1="90" x2="105" y2="160"
            stroke={live(onBattery, "var(--cyan)")} strokeWidth="2"
            strokeDasharray={flowDash(onBattery)} />
      <rect x="60" y="170" width="180" height="64" rx="4"
            fill={onBattery ? "rgba(34,211,238,0.10)" : "var(--bg-elev-2)"}
            stroke={onBattery ? "var(--cyan)" : "var(--text-tertiary)"} strokeWidth="1.4" />
      <text x="150" y="188" textAnchor="middle" className="hmi-tag">240-CELL VRLA STRING</text>

      {/* Battery cells visualization */}
      {[0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11].map((i) => {
        const cellHealthy = (i / 12) <= (bat / 100);
        return (
          <rect key={i} x={70 + i * 14} y={196} width="10" height="20" rx="1"
                fill={cellHealthy ? "var(--cyan)" : "var(--bg-base)"}
                stroke="var(--text-tertiary)" strokeWidth="0.7" />
        );
      })}
      <text x="150" y="228" textAnchor="middle" className="hmi-tag">{fmt(bat, 0)}% · {fmt(runtime, 0)} min runtime</text>

      {/* Status LEDs */}
      <text x="30" y="266" className="hmi-tag">STATUS</text>
      <circle cx="40" cy="282" r="6"
              fill={normal ? "var(--pass)" : "var(--bg-elev-2)"}
              stroke={normal ? "var(--pass)" : "var(--text-faint)"} strokeWidth="1.2"
              style={normal ? { filter: "drop-shadow(0 0 4px var(--pass))" } : null} />
      <text x="54" y="286" className="hmi-tag">NORMAL · ONLINE</text>

      <circle cx="40" cy="302" r="6"
              fill={onBattery ? "var(--cyan)" : "var(--bg-elev-2)"}
              stroke={onBattery ? "var(--cyan)" : "var(--text-faint)"} strokeWidth="1.2" />
      <text x="54" y="306" className="hmi-tag">ON BATTERY</text>

      <circle cx="170" cy="282" r="6"
              fill={onBypass ? "var(--warn)" : "var(--bg-elev-2)"}
              stroke={onBypass ? "var(--warn)" : "var(--text-faint)"} strokeWidth="1.2" />
      <text x="184" y="286" className="hmi-tag">ON BYPASS</text>

      <circle cx="170" cy="302" r="6"
              fill="var(--bg-elev-2)" stroke="var(--text-faint)" strokeWidth="1.2" />
      <text x="184" y="306" className="hmi-tag">FAULT</text>

      {/* Load bar */}
      <text x="30" y="328" className="hmi-tag">LOAD</text>
      <rect x="70" y="320" width="180" height="10" rx="2"
            fill="var(--bg-base)" stroke="var(--text-tertiary)" strokeWidth="1" />
      <rect x="70" y="320" width={180 * (load / 100)} height="10" rx="2"
            fill={load > 80 ? "var(--warn)" : "var(--pass)"} />
      <text x="252" y="329" className="hmi-tag" textAnchor="end">{fmt(load, 0)}%</text>
    </Frame>
  );
}

// ---------------------------------------------------------------------------
// ATS — automatic transfer switch with two source contactors and a transfer
// position indicator. SOURCE 1 (utility) vs SOURCE 2 (gen).
// ---------------------------------------------------------------------------
function AtsHMI({ id, d }) {
  const onSource1 = (d?.active_source ?? 1) === 1;
  return (
    <Frame label={`${id?.toUpperCase()} · 1600A · ASCO 7000 series`}>
      <rect x="20" y="20" width="260" height="320" rx="6"
            fill="var(--bg-elev-1)" stroke="var(--text-tertiary)" strokeWidth="1.4" />

      {/* Source 1 — utility */}
      <rect x="40" y="50" width="100" height="60" rx="4"
            fill={onSource1 ? "var(--pass-dim)" : "var(--bg-elev-2)"}
            stroke={onSource1 ? "var(--pass)" : "var(--text-tertiary)"} strokeWidth="1.6" />
      <text x="90" y="68" textAnchor="middle" className="hmi-tag">SOURCE 1</text>
      <text x="90" y="84" textAnchor="middle" className="hmi-val-pass">UTILITY</text>
      <text x="90" y="100" textAnchor="middle" className="hmi-tag">480 V · 60.0 Hz</text>

      {/* Source 2 — generator */}
      <rect x="160" y="50" width="100" height="60" rx="4"
            fill={!onSource1 ? "var(--warn-dim)" : "var(--bg-elev-2)"}
            stroke={!onSource1 ? "var(--warn)" : "var(--text-tertiary)"} strokeWidth="1.6" />
      <text x="210" y="68" textAnchor="middle" className="hmi-tag">SOURCE 2</text>
      <text x="210" y="84" textAnchor="middle" className="hmi-val-warn">GENERATOR</text>
      <text x="210" y="100" textAnchor="middle" className="hmi-tag">standby</text>

      {/* Transfer mechanism */}
      <line x1="90" y1="110" x2="90" y2="160" stroke={onSource1 ? "var(--pass)" : "var(--text-faint)"}
            strokeWidth="2" strokeDasharray={onSource1 ? "0" : "4 4"} />
      <line x1="210" y1="110" x2="210" y2="160" stroke={!onSource1 ? "var(--warn)" : "var(--text-faint)"}
            strokeWidth="2" strokeDasharray={!onSource1 ? "0" : "4 4"} />

      <rect x="50" y="160" width="200" height="50" rx="4"
            fill="var(--bg-elev-2)" stroke="var(--text-tertiary)" strokeWidth="1.4" />
      <text x="150" y="178" textAnchor="middle" className="hmi-tag">TRANSFER MECHANISM</text>
      {/* Lever pointing left or right */}
      <circle cx="150" cy="195" r="6" fill="var(--accent)" stroke="var(--accent)" strokeWidth="1" />
      <line x1="150" y1="195" x2={onSource1 ? 95 : 205} y2="195"
            stroke="var(--accent)" strokeWidth="3" strokeLinecap="round" />

      {/* Output to load */}
      <line x1="150" y1="210" x2="150" y2="240" stroke="var(--pass)" strokeWidth="2" />
      <rect x="80" y="240" width="140" height="44" rx="4"
            fill="var(--bg-elev-2)" stroke="var(--pass)" strokeWidth="1.6" />
      <text x="150" y="258" textAnchor="middle" className="hmi-tag">LOAD · UPS / CRITICAL</text>
      <text x="150" y="276" textAnchor="middle" className="hmi-val-pass">ENERGIZED</text>

      {/* Mode switch */}
      <text x="40" y="310" className="hmi-tag">MODE</text>
      <rect x="80" y="300" width="60" height="20" rx="3"
            fill="var(--pass-dim)" stroke="var(--pass)" strokeWidth="1.2" />
      <text x="110" y="314" textAnchor="middle" className="hmi-tag">AUTO</text>

      <rect x="146" y="300" width="60" height="20" rx="3"
            fill="var(--bg-elev-2)" stroke="var(--text-faint)" strokeWidth="1" />
      <text x="176" y="314" textAnchor="middle" className="hmi-tag">TEST</text>

      <rect x="212" y="300" width="46" height="20" rx="3"
            fill="var(--bg-elev-2)" stroke="var(--text-faint)" strokeWidth="1" />
      <text x="235" y="314" textAnchor="middle" className="hmi-tag">MAN</text>
    </Frame>
  );
}

// ---------------------------------------------------------------------------
// Bus — three-phase busbar enclosure (A-B-C conductors with insulator stand-offs)
// ---------------------------------------------------------------------------
function BusHMI({ id, d }) {
  const v = d?.voltage_ll_v ?? 0;
  const a = d?.current_a ?? 0;
  const f = d?.frequency_hz ?? 0;
  const kw = d?.real_power_kw ?? 0;
  const pf = d?.power_factor ?? 0;

  return (
    <Frame label={`${id?.toUpperCase()} · 3-phase busbar`}>
      <rect x="20" y="20" width="260" height="80" rx="4"
            fill="var(--bg-elev-1)" stroke="var(--text-tertiary)" strokeWidth="1.4" />
      <text x="34" y="40" className="hmi-tag">BUSWAY ENCLOSURE</text>

      {/* Phase A / B / C conductors */}
      {[
        { y: 56, name: "A", color: "#e74c3c" },
        { y: 72, name: "B", color: "#f1c40f" },
        { y: 88, name: "C", color: "#3498db" },
      ].map((p) => (
        <g key={p.name}>
          <line x1="40" y1={p.y} x2="260" y2={p.y} stroke={p.color} strokeWidth="4" />
          <text x="30" y={p.y + 4} className="hmi-tag" style={{ fill: p.color }}>{p.name}</text>
          {[60, 100, 140, 180, 220].map((x) => (
            <rect key={x} x={x - 3} y={p.y - 3} width="6" height="6"
                  fill="var(--bg-elev-2)" stroke="var(--text-faint)" strokeWidth="0.6" />
          ))}
        </g>
      ))}

      {/* Live readings */}
      <rect x="20" y="120" width="260" height="220" rx="4"
            fill="var(--bg-elev-1)" stroke="var(--text-tertiary)" strokeWidth="1.4" />
      <text x="32" y="140" className="hmi-tag">LIVE METERING · CM2000</text>

      {[
        { l: "V (line-line)", v: `${fmt(v, 0)} V`,  y: 168 },
        { l: "I (avg)",       v: `${fmt(a, 0)} A`,  y: 192 },
        { l: "Frequency",     v: `${fmt(f, 3)} Hz`, y: 216 },
        { l: "Real power",    v: `${fmt(kw, 0)} kW`, y: 240 },
        { l: "Power factor",  v: fmt(pf, 3),         y: 264 },
        { l: "V THD",         v: `${fmt(d?.thd_voltage_pct, 2)} %`, y: 288 },
      ].map((row, i) => (
        <g key={i}>
          <text x="32" y={row.y} className="hmi-tag">{row.l}</text>
          <text x="268" y={row.y} textAnchor="end" className="hmi-counter">{row.v}</text>
          <line x1="32" y1={row.y + 4} x2="268" y2={row.y + 4} stroke="var(--border-subtle)" strokeWidth="0.5" />
        </g>
      ))}
    </Frame>
  );
}

// ---------------------------------------------------------------------------
// SEL-751 protective relay — front panel with target LEDs and LCD readout
// ---------------------------------------------------------------------------
function RelayHMI({ id, d }) {
  const tripped = !!d?.last_trip_cause;
  return (
    <Frame label={`${id?.toUpperCase()} · SEL-751 feeder relay`}>
      <rect x="30" y="30" width="240" height="300" rx="6"
            fill="var(--bg-elev-1)" stroke="var(--text-tertiary)" strokeWidth="1.6" />
      <rect x="40" y="42" width="220" height="22" rx="3"
            fill="var(--bg-elev-2)" stroke="var(--border-subtle)" strokeWidth="1" />
      <text x="150" y="58" textAnchor="middle" className="hmi-tag">SCHWEITZER ENGINEERING · SEL-751</text>

      {/* LCD */}
      <rect x="50" y="78" width="200" height="60" rx="3"
            fill={tripped ? "var(--fail-dim)" : "#0a1f1a"}
            stroke={tripped ? "var(--fail)" : "var(--pass)"} strokeWidth="1.4" />
      <text x="150" y="100" textAnchor="middle" className="hmi-counter"
            style={{ fill: tripped ? "var(--fail)" : "var(--pass)", fontSize: 14 }}>
        {tripped ? "TRIP — LOCKOUT" : "ARMED · NO ALARMS"}
      </text>
      <text x="150" y="124" textAnchor="middle" className="hmi-tag">
        {tripped ? d?.last_trip_cause : "monitoring 50/51/50N/51N/27/81"}
      </text>

      {/* Target LEDs */}
      <text x="50" y="160" className="hmi-tag">PROTECTIVE TARGETS</text>
      {[
        { l: "50",  desc: "INST OC" },
        { l: "51",  desc: "TIME OC" },
        { l: "50N", desc: "GND INST" },
        { l: "51N", desc: "GND TIME" },
        { l: "27",  desc: "UNDERVOLT" },
        { l: "81",  desc: "FREQ" },
      ].map((t, i) => {
        const col = i % 2, row = Math.floor(i / 2);
        const x = 50 + col * 110;
        const y = 178 + row * 36;
        const active = tripped && (d?.last_trip_cause || "").includes(t.l);
        return (
          <g key={t.l}>
            <circle cx={x + 8} cy={y + 8} r="6"
                    fill={active ? "var(--fail)" : "var(--bg-elev-2)"}
                    stroke={active ? "var(--fail)" : "var(--text-faint)"} strokeWidth="1.2" />
            <text x={x + 22} y={y + 6} className="hmi-counter" style={{ fontSize: 11 }}>{t.l}</text>
            <text x={x + 22} y={y + 18} className="hmi-tag">{t.desc}</text>
          </g>
        );
      })}

      {/* Pushbuttons */}
      <rect x="50" y="294" width="60" height="22" rx="3"
            fill="var(--bg-elev-2)" stroke="var(--text-tertiary)" strokeWidth="1" />
      <text x="80" y="308" textAnchor="middle" className="hmi-tag">TARGET RST</text>
      <rect x="120" y="294" width="60" height="22" rx="3"
            fill="var(--bg-elev-2)" stroke="var(--text-tertiary)" strokeWidth="1" />
      <text x="150" y="308" textAnchor="middle" className="hmi-tag">METER</text>
      <rect x="190" y="294" width="60" height="22" rx="3"
            fill="var(--bg-elev-2)" stroke="var(--text-tertiary)" strokeWidth="1" />
      <text x="220" y="308" textAnchor="middle" className="hmi-tag">EVENTS</text>
    </Frame>
  );
}

// ---------------------------------------------------------------------------
// Utility source — pothead / surge arrester / incoming feeder
// ---------------------------------------------------------------------------
function SourceHMI({ id, d }) {
  return (
    <Frame label={`${id?.toUpperCase()} · 13.8 kV utility feeder`}>
      <rect x="20" y="20" width="260" height="320" rx="6"
            fill="var(--bg-elev-1)" stroke="var(--text-tertiary)" strokeWidth="1.4" />

      {/* Tower / overhead line entry */}
      <line x1="40" y1="60" x2="100" y2="60" stroke="var(--text-tertiary)" strokeWidth="2" />
      <line x1="100" y1="60" x2="100" y2="100" stroke="var(--text-tertiary)" strokeWidth="2" />
      <text x="50" y="50" className="hmi-tag">115 kV o/h</text>

      {/* Step-down to 13.8 kV (substation) */}
      <rect x="80" y="100" width="120" height="40" rx="3"
            fill="var(--bg-elev-2)" stroke="var(--text-tertiary)" strokeWidth="1.4" />
      <text x="140" y="118" textAnchor="middle" className="hmi-tag">SUB · 115/13.8 kV</text>
      <text x="140" y="132" textAnchor="middle" className="hmi-counter" style={{ fontSize: 10 }}>30 MVA</text>

      {/* Surge arrester */}
      <line x1="140" y1="140" x2="140" y2="170" stroke="var(--text-tertiary)" strokeWidth="2" />
      <rect x="124" y="170" width="32" height="36" rx="2"
            fill="var(--bg-elev-2)" stroke="var(--accent)" strokeWidth="1.4" />
      {[178, 186, 194].map((y, i) => (
        <ellipse key={i} cx="140" cy={y} rx="13" ry="3"
                 fill="none" stroke="var(--accent)" strokeWidth="0.8" />
      ))}
      <text x="170" y="190" className="hmi-tag">MOV ARRESTER</text>

      {/* Pothead / cable termination */}
      <line x1="140" y1="206" x2="140" y2="240" stroke="var(--text-tertiary)" strokeWidth="2" />
      <polygon points="128,240 152,240 146,256 134,256"
               fill="var(--bg-elev-2)" stroke="var(--text-tertiary)" strokeWidth="1.2" />
      <text x="170" y="252" className="hmi-tag">POTHEAD</text>

      {/* Underground cable to switchgear */}
      <line x1="140" y1="256" x2="140" y2="290" stroke="var(--cyan)" strokeWidth="2.4" />
      <rect x="60" y="290" width="160" height="36" rx="3"
            fill="var(--pass-dim)" stroke="var(--pass)" strokeWidth="1.4" />
      <text x="140" y="308" textAnchor="middle" className="hmi-tag">TO MV SWITCHGEAR</text>
      <text x="140" y="322" textAnchor="middle" className="hmi-val-pass">ENERGIZED · 13.8 kV</text>
    </Frame>
  );
}
