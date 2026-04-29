// HTML card components for the single-line diagram. Typography uses
// the design system — no SVG <text>.

import React from "react";

export function Source({ id, label, rating, x, y, onSelect, selected }) {
  return (
    <div className={"sld-card sld-source" + (selected ? " selected" : "")}
         style={{ left: x, top: y }}
         onClick={() => onSelect && onSelect({ type: "source", id })}>
      <div className="src-mark" />
      <div className="src-info">
        <div className="src-label">SOURCE</div>
        <div className="src-name">{label}</div>
        <div className="src-rating">{rating}</div>
      </div>
    </div>
  );
}

export function Breaker({ id, name, rating, state, x, y, onSelect, selected }) {
  const stateLower = (state || "racked_out").toLowerCase();
  const glyph = stateLower === "closed"  ? "✕"
              : stateLower === "tripped" ? "!"
              : stateLower === "open"    ? "○"
              :                            "◌";
  return (
    <div className={"sld-card sld-breaker" + (selected ? " selected" : "")}
         style={{ left: x, top: y }}
         onClick={() => onSelect && onSelect({ type: "breaker", id })}>
      <div className={"brk-glyph " + stateLower}>{glyph}</div>
      <div className="brk-info">
        <div className="sld-name">{name}</div>
        <div className="sld-meta">{rating}A</div>
      </div>
    </div>
  );
}

export function Bus({ id, label, voltage, current, freq, x, y, w, energized = true, onSelect, selected }) {
  return (
    <div className={"sld-bus" + (selected ? " selected" : "")}
         style={{
           left: x, top: y, width: w,
           borderLeftColor: energized ? "var(--pass)" : "var(--text-faint)",
         }}
         onClick={() => onSelect && onSelect({ type: "bus", id })}>
      <div className="bus-label">{label}</div>
      <div className="bus-stats">
        <div className="bus-stat"><span className="v">{voltage}</span><span className="u">V</span></div>
        <div className="bus-stat"><span className="v">{current}</span><span className="u">A</span></div>
        <div className="bus-stat"><span className="v">{freq}</span><span className="u">Hz</span></div>
      </div>
    </div>
  );
}

export function Xfmr({ id, name, sizing, winding, c2h2, x, y, onSelect, selected }) {
  const arc = c2h2 != null && c2h2 > 2;
  return (
    <div className={"sld-card sld-xfmr" + (arc ? " arc" : "") + (selected ? " selected" : "")}
         style={{ left: x, top: y }}
         onClick={() => onSelect && onSelect({ type: "xfmr", id })}>
      <div className="sld-tag">{id.toUpperCase()}</div>
      <div className="xfmr-symbol"><div className="c1" /><div className="c2" /></div>
      <div className="sld-name">{name}</div>
      <div className="sld-meta">{sizing}</div>
      <div className="meta-row">
        <span className="ok">W:{winding != null ? winding.toFixed(0) : "—"}°C</span>
        <span className={arc ? "alarm" : "ok"}>
          C₂H₂:{c2h2 != null ? c2h2.toFixed(2) : "—"}ppm
        </span>
      </div>
    </div>
  );
}

export function Relay({ id, tripped, x, y, onSelect, selected }) {
  return (
    <div className={"sld-card sld-relay" + (tripped ? " tripped" : "") + (selected ? " selected" : "")}
         style={{ left: x, top: y }}
         onClick={() => onSelect && onSelect({ type: "relay", id })}>
      <div className="sld-tag">SEL-751</div>
      <div className={"relay-status " + (tripped ? "tripped" : "armed")}>
        {tripped ? "TRIP" : "ARMED"}
      </div>
    </div>
  );
}

export function PdMon({ id, name, x, y }) {
  return (
    <div className="sld-chip" style={{ left: x, top: y }}>
      ◇ {name}
    </div>
  );
}

export function ATSCard({ id, x, y, onSelect, selected }) {
  return (
    <div className={"sld-card sld-equip" + (selected ? " selected" : "")}
         style={{ left: x, top: y }}
         onClick={() => onSelect && onSelect({ type: "ats", id })}>
      <div className="sld-tag">ATS</div>
      <div className="sld-name">{id.toUpperCase()}</div>
      <div className="equip-row">
        <span>SOURCE</span><span className="v">1</span>
      </div>
    </div>
  );
}

export function UPSCard({ id, ups, x, y, onSelect, selected }) {
  return (
    <div className={"sld-card sld-equip" + (selected ? " selected" : "")}
         style={{ left: x, top: y }}
         onClick={() => onSelect && onSelect({ type: "ups", id })}>
      <div className="sld-tag">UPS</div>
      <div className="sld-name">{id.toUpperCase()}</div>
      {ups && (
        <>
          <div className="equip-row">
            <span>BAT</span><span className="v">{ups.battery_pct.toFixed(0)}%</span>
          </div>
          <div className="equip-row">
            <span>LOAD</span><span className="v">{ups.load_pct.toFixed(0)}%</span>
          </div>
          <div className="equip-row">
            <span>STATE</span>
            <span className="v" style={{ color: "var(--pass)" }}>{ups.state}</span>
          </div>
        </>
      )}
    </div>
  );
}

export function GenCard({ id, gen, x, y, onSelect, selected }) {
  const stateColor = gen?.state === "running" ? "var(--pass)" : "var(--warn)";
  const stateText = (gen?.state || "standby").toUpperCase();
  return (
    <div className={"sld-card sld-gen" + (selected ? " selected" : "")}
         style={{ left: x, top: y }}
         onClick={() => onSelect && onSelect({ type: "gen", id })}>
      <div className="gen-head">
        <span className="t">GEN · {id.toUpperCase()}</span>
        <span className="badge" style={{
          background: gen?.state === "running" ? "var(--pass-dim)" : "var(--warn-dim)",
          color: stateColor,
          border: `1px solid ${stateColor}`,
        }}>{stateText}</span>
      </div>
      <div className="gen-name">Caterpillar 3516B · 1500 kW</div>
      <div className="gen-sub">diesel · 13.8 kV / 480 V via paralleling bus</div>
      <div className="gen-grid">
        <span className="lbl">RPM</span><span className="val">{gen ? gen.rpm.toFixed(0) : "—"}</span>
        <span className="lbl">V_LL</span><span className="val">{gen ? gen.voltage_ll_v.toFixed(0) : "—"} V</span>
        <span className="lbl">FREQ</span><span className="val">{gen ? gen.frequency_hz.toFixed(2) : "—"} Hz</span>
        <span className="lbl">OIL</span><span className="val">{gen ? gen.oil_pressure_psi.toFixed(1) : "—"} psi</span>
        <span className="lbl">COOL</span><span className="val">{gen ? gen.coolant_temp_c.toFixed(0) : "—"} °C</span>
        <span className="lbl">FUEL</span><span className="val">{gen ? gen.fuel_level_pct.toFixed(1) : "—"} %</span>
        <span className="lbl">RUNTIME</span><span className="val">{gen ? gen.runtime_hours.toFixed(0) : "—"} h</span>
      </div>
      <div className="gen-foot">
        last load-bank · <span className="ok">PASS</span> · 04:18 UTC
      </div>
    </div>
  );
}

export function Legend({ x, y }) {
  return (
    <div className="sld-legend" style={{ left: x, top: y }}>
      <div className="t">LEGEND</div>
      <div className="lg-row">
        <span className="swatch" style={{ background: "var(--pass-dim)", border: "1.5px solid var(--pass)" }} />
        <span>closed · energized</span>
      </div>
      <div className="lg-row">
        <span className="swatch" style={{ background: "var(--bg-elev-2)", border: "1.5px solid var(--text-tertiary)" }} />
        <span>open</span>
      </div>
      <div className="lg-row">
        <span className="swatch" style={{ background: "var(--fail-dim)", border: "1.5px solid var(--fail)" }} />
        <span>tripped</span>
      </div>
      <div className="lg-row">
        <span className="swatch-line" style={{ background: "var(--pass)" }} />
        <span>live conductor</span>
      </div>
      <div className="lg-row">
        <span className="swatch-line" style={{
          background: "repeating-linear-gradient(90deg, var(--text-faint) 0 4px, transparent 4px 8px)"
        }} />
        <span>de-energized</span>
      </div>
      <div style={{ marginTop: 6, fontSize: 10, color: "var(--text-faint)" }}>
        click any element →
      </div>
    </div>
  );
}
