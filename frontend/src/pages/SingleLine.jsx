import React, { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../hooks/useApi";
import { POS, SLD_W, SLD_H } from "./sld_layout";
import {
  Source, Breaker, Bus, Xfmr, Relay, PdMon,
  ATSCard, UPSCard, GenCard, Legend,
} from "./sld_cards";
import { Wire } from "./sld_wires";

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
        <h1>Single-Line Diagram</h1>
        <span className="crumb">DC1-Ashburn · MV/LV main lineup · live · {t.timestamp}</span>
      </div>

      <SoeBanner soe={t.soe || []} />

      <div className="row" style={{ alignItems: "flex-start" }}>
        <SLDCanvas t={t} selected={selected} onSelect={setSelected} />
        <DetailPanel t={t} selected={selected} navigate={navigate} />
      </div>
    </>
  );
}

// ---------------------------------------------------------------------------
// SoE banner — shows the 3 most recent events
// ---------------------------------------------------------------------------

function SoeBanner({ soe }) {
  const recent = soe.slice(-3).reverse();
  if (recent.length === 0) return null;
  return (
    <div style={{ marginBottom: 12 }}>
      {recent.map((e, i) => (
        <div key={i} className={"soe-row " + e.severity}>
          <span className="ts">{e.timestamp.slice(11, 19)}</span>
          <span><span className={"badge badge-" + sevToBadge(e.severity)}>{e.severity}</span></span>
          <span className="dev">{e.device_id}</span>
          <span className="msg">{e.text}</span>
        </div>
      ))}
    </div>
  );
}
function sevToBadge(s) {
  return s === "critical" ? "critical"
       : s === "alarm"    ? "alarm"
       : s === "warn"     ? "warn"
       :                    "info";
}

// ---------------------------------------------------------------------------
// The SLD canvas — HTML cards on top, SVG wire layer behind
// ---------------------------------------------------------------------------

function SLDCanvas({ t, selected, onSelect }) {
  const breakers = t.breakers || {};
  const buses    = t.buses || {};
  const relays   = t.relays || {};
  const xfmrs    = t.xfmrs || {};
  const upses    = t.upses || {};
  const gens     = t.gens || {};

  const live = (id) => breakers[id]?.position === "closed";
  const isSel = (id) => selected && selected.id === id;

  return (
    <div className="card grow" style={{ padding: 0 }}>
      <div className="card-h">MV / LV LINEUP</div>
      <div className="sld-canvas" style={{ height: SLD_H, width: "100%" }}>
        <SLDWires t={t} />

        {/* Sources */}
        <Source id="util-A" label="UTILITY A" rating="13.8 kV"
                {...POS["util-A"]} onSelect={onSelect} selected={isSel("util-A")} />
        <Source id="util-B" label="UTILITY B" rating="13.8 kV"
                {...POS["util-B"]} onSelect={onSelect} selected={isSel("util-B")} />
        <Source id="gen-bus" label="GEN PARALLELING" rating="2 × 1500 kW"
                {...POS["gen-bus"]} onSelect={onSelect} selected={isSel("gen-bus")} />

        {/* MV main breakers + relays */}
        {["mv-main-A", "mv-main-B"].map((id) => (
          <Breaker key={id} id={id} name={id.toUpperCase()} rating={1200}
                   state={breakers[id]?.position}
                   {...POS[id]} onSelect={onSelect} selected={isSel(id)} />
        ))}
        {["sel-mv-main-A", "sel-mv-main-B"].map((id) => (
          <Relay key={id} id={id} tripped={!!relays[id]?.last_trip_cause}
                 {...POS[id]} onSelect={onSelect} selected={isSel(id)} />
        ))}

        {/* MV buses + tie + insulgards */}
        <Bus id="mv-bus-A" label="MV BUS A · 13.8kV"
             voltage={fmt(buses["mv-bus-A"]?.voltage_ll_v, 0)}
             current={fmt(buses["mv-bus-A"]?.current_a, 0)}
             freq={fmt(buses["mv-bus-A"]?.frequency_hz, 2)}
             energized {...POS["mv-bus-A"]}
             onSelect={onSelect} selected={isSel("mv-bus-A")} />
        <Bus id="mv-bus-B" label="MV BUS B · 13.8kV"
             voltage={fmt(buses["mv-bus-B"]?.voltage_ll_v, 0)}
             current={fmt(buses["mv-bus-B"]?.current_a, 0)}
             freq={fmt(buses["mv-bus-B"]?.frequency_hz, 2)}
             energized {...POS["mv-bus-B"]}
             onSelect={onSelect} selected={isSel("mv-bus-B")} />
        <Breaker id="mv-tie" name="MV-TIE" rating={1200}
                 state={breakers["mv-tie"]?.position}
                 {...POS["mv-tie"]} onSelect={onSelect} selected={isSel("mv-tie")} />
        <Relay id="sel-mv-tie" tripped={!!relays["sel-mv-tie"]?.last_trip_cause}
               {...POS["sel-mv-tie"]} onSelect={onSelect} selected={isSel("sel-mv-tie")} />
        <PdMon id="insulgard-mv-A" name="InsulGard PD · MV-A" {...POS["insulgard-mv-A"]} />
        <PdMon id="insulgard-mv-B" name="InsulGard PD · MV-B" {...POS["insulgard-mv-B"]} />

        {/* Transformers */}
        {["xfmr-A1", "xfmr-A2", "xfmr-B1", "xfmr-B2"].map((id) => (
          <Xfmr key={id} id={id} name={id.toUpperCase()}
                sizing="2500 kVA · 13.8/0.48 kV"
                winding={xfmrs[id]?.winding_temp_c}
                c2h2={xfmrs[id]?.c2h2_ppm}
                {...POS[id]} onSelect={onSelect} selected={isSel(id)} />
        ))}

        {/* MTZ incoming + LV buses */}
        {["mtz-inc-A1", "mtz-inc-A2", "mtz-inc-B1", "mtz-inc-B2"].map((id) => (
          <Breaker key={id} id={id} name={id.toUpperCase()} rating={4000}
                   state={breakers[id]?.position}
                   {...POS[id]} onSelect={onSelect} selected={isSel(id)} />
        ))}
        {["lv-bus-A1", "lv-bus-A2", "lv-bus-B1", "lv-bus-B2"].map((id) => (
          <Bus key={id} id={id} label={id.toUpperCase() + " · 480V"}
               voltage={fmt(buses[id]?.voltage_ll_v, 0)}
               current={fmt(buses[id]?.current_a, 0)}
               freq={fmt(buses[id]?.frequency_hz, 2)}
               energized {...POS[id]}
               onSelect={onSelect} selected={isSel(id)} />
        ))}

        {/* ATS / UPS */}
        <ATSCard id="ats-1" {...POS["ats-1"]} onSelect={onSelect} selected={isSel("ats-1")} />
        <ATSCard id="ats-2" {...POS["ats-2"]} onSelect={onSelect} selected={isSel("ats-2")} />
        <UPSCard id="ups-A" ups={upses["ups-A"]} {...POS["ups-A"]}
                 onSelect={onSelect} selected={isSel("ups-A")} />
        <UPSCard id="ups-B" ups={upses["ups-B"]} {...POS["ups-B"]}
                 onSelect={onSelect} selected={isSel("ups-B")} />

        {/* Generator panels */}
        <GenCard id="gen-1" gen={gens["gen-1"]} {...POS["gen-1"]}
                 onSelect={onSelect} selected={isSel("gen-1")} />
        <GenCard id="gen-2" gen={gens["gen-2"]} {...POS["gen-2"]}
                 onSelect={onSelect} selected={isSel("gen-2")} />

        <Legend {...POS["legend"]} />
      </div>
    </div>
  );
}

function fmt(n, d) {
  if (n == null || isNaN(n)) return "—";
  return n.toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d });
}

// ---------------------------------------------------------------------------
// SVG wire layer
// ---------------------------------------------------------------------------

function SLDWires({ t }) {
  const breakers = t.breakers || {};
  const live = (id) => breakers[id]?.position === "closed";

  return (
    <svg className="sld-wires" viewBox={`0 0 ${SLD_W} ${SLD_H}`} preserveAspectRatio="none">
      {/* Utility A → MV-MAIN-A */}
      <Wire from="util-A" fromSide="bottom" to="mv-main-A" toSide="left" live={live("mv-main-A")} route="vhv" />
      {/* Utility B → MV-MAIN-B */}
      <Wire from="util-B" fromSide="bottom" to="mv-main-B" toSide="left" live={live("mv-main-B")} route="vhv" />

      {/* MV-MAIN-A → MV BUS A (vertical drop) */}
      <Wire from="mv-main-A" fromSide="bottom" to="mv-bus-A" toSide="top" live={live("mv-main-A")} />
      <Wire from="mv-main-B" fromSide="bottom" to="mv-bus-B" toSide="top" live={live("mv-main-B")} />

      {/* MV-TIE between buses */}
      <Wire from="mv-bus-A" fromSide="right" to="mv-tie" toSide="top" live={live("mv-tie")} route="hvh" />
      <Wire from="mv-tie" fromSide="bottom" to="mv-bus-B" toSide="left" live={live("mv-tie")} route="hvh" />

      {/* Transformers fed off the MV buses */}
      {["xfmr-A1", "xfmr-A2"].map((id) => (
        <Wire key={id} from="mv-bus-A" fromSide="bottom" to={id} toSide="top" live={live("mv-main-A")} />
      ))}
      {["xfmr-B1", "xfmr-B2"].map((id) => (
        <Wire key={id} from="mv-bus-B" fromSide="bottom" to={id} toSide="top" live={live("mv-main-B")} />
      ))}

      {/* XFMRs → MTZ incoming */}
      {["A1", "A2", "B1", "B2"].map((s) => (
        <Wire key={s} from={`xfmr-${s}`} fromSide="bottom" to={`mtz-inc-${s}`} toSide="top" live />
      ))}

      {/* MTZ → LV bus → ATS chain */}
      {["A1", "A2", "B1", "B2"].map((s) => (
        <Wire key={s} from={`mtz-inc-${s}`} fromSide="bottom" to={`lv-bus-${s}`} toSide="top"
              live={live(`mtz-inc-${s}`)} />
      ))}
      <Wire from="lv-bus-A1" fromSide="right" to="ats-1" toSide="left" live route="hvh" />
      <Wire from="lv-bus-B1" fromSide="right" to="ats-2" toSide="left" live route="hvh" />
      <Wire from="ats-1" fromSide="bottom" to="ups-A" toSide="top" live />
      <Wire from="ats-2" fromSide="bottom" to="ups-B" toSide="top" live />

      {/* Gen paralleling bus → ATSes (alt source) */}
      <Wire from="gen-bus" fromSide="bottom" to="ats-1" toSide="top" live={false} route="vhv" />
      <Wire from="gen-bus" fromSide="bottom" to="ats-2" toSide="top" live={false} route="vhv" />
    </svg>
  );
}

// ---------------------------------------------------------------------------
// Detail panel — populated when an element is clicked
// ---------------------------------------------------------------------------

function DetailPanel({ t, selected, navigate }) {
  if (!selected) {
    return (
      <div className="card" style={{ width: 320, padding: 0 }}>
        <div className="card-h">SELECTION</div>
        <div className="card-body">
          <p style={{ color: "var(--text-tertiary)", margin: 0, fontSize: 12 }}>
            Click any element on the SLD to inspect its live values and open the
            equipment console.
          </p>
        </div>
      </div>
    );
  }

  const { type, id } = selected;
  const open = (route) => (
    <button className="primary" style={{ marginTop: 12, width: "100%" }}
            onClick={() => navigate(route)}>
      Open console →
    </button>
  );

  return (
    <div className="card" style={{ width: 320, padding: 0 }}>
      <div className="card-h">{(type + " · " + id).toUpperCase()}</div>
      <div className="card-body">
        {type === "bus"     && <BusBody bus={t.buses[id]} />}
        {type === "breaker" && <BreakerBody brk={t.breakers[id]} />}
        {type === "xfmr"    && <><XfmrBody x={t.xfmrs[id]} />{open(`/xfmr/${id}`)}</>}
        {type === "ups"     && <><UpsBody u={t.upses[id]} />{open(`/ups/${id}`)}</>}
        {type === "gen"     && <><GenBody g={t.gens[id]} />{open(`/gens/${id}`)}</>}
        {type === "ats"     && <>{open(`/ats/${id}`)}</>}
        {type === "relay"   && <>{open(`/relays/${id}`)}</>}
      </div>
    </div>
  );
}

const KV = ({ k, v, color }) => (
  <div className="kv" style={{ marginBottom: 4 }}>
    <div className="k">{k}</div>
    <div className="v" style={{ color: color || "var(--text-primary)" }}>{v}</div>
  </div>
);

function BusBody({ bus }) {
  if (!bus) return <p style={{ color: "var(--text-tertiary)" }}>—</p>;
  return (<>
    <KV k="V (line-line)" v={`${bus.voltage_ll_v.toFixed(0)} V`} />
    <KV k="I (avg)"      v={`${bus.current_a.toFixed(0)} A`} />
    <KV k="Real power"   v={`${bus.real_power_kw.toFixed(0)} kW`} />
    <KV k="Reactive"     v={`${bus.reactive_power_kvar.toFixed(0)} kVAR`} />
    <KV k="Power factor" v={bus.power_factor.toFixed(3)} />
    <KV k="Frequency"    v={`${bus.frequency_hz.toFixed(3)} Hz`} />
    <KV k="V THD"        v={`${bus.thd_voltage_pct.toFixed(2)} %`} />
  </>);
}
function BreakerBody({ brk }) {
  if (!brk) return null;
  const color = brk.position === "closed" ? "var(--pass)"
              : brk.position === "tripped" ? "var(--fail)"
              : "var(--text-tertiary)";
  return (<>
    <KV k="Position" v={brk.position} color={color} />
    <KV k="Rating"   v={`${brk.rating_amps} A`} />
    <KV k="Spring"   v={brk.spring_charged ? "charged" : "uncharged"} />
    <KV k="Contact wear" v={`${brk.contact_wear_pct} %`} />
    <KV k="Op count" v={brk.ops_count} />
  </>);
}
function XfmrBody({ x }) {
  if (!x) return null;
  const arc = x.c2h2_ppm > 2;
  return (<>
    <KV k="Winding temp"  v={`${x.winding_temp_c.toFixed(1)} °C`} />
    <KV k="Oil temp"      v={`${x.oil_temp_c.toFixed(1)} °C`} />
    <KV k="H₂ (DGA)"      v={`${x.h2_ppm.toFixed(0)} ppm`} />
    <KV k="CH₄ (DGA)"     v={`${x.ch4_ppm.toFixed(1)} ppm`} />
    <KV k="C₂H₂ (DGA)"   v={`${x.c2h2_ppm.toFixed(2)} ppm` + (arc ? " · ACTIVE ARCING" : "")}
        color={arc ? "var(--fail)" : null} />
    <KV k="Moisture"      v={`${x.moisture_ppm.toFixed(1)} ppm`} />
    <KV k="PD magnitude"  v={`${x.pd_magnitude_pc.toFixed(1)} pC`} />
  </>);
}
function UpsBody({ u }) {
  if (!u) return null;
  return (<>
    <KV k="State"    v={u.state} color="var(--pass)" />
    <KV k="Input V"  v={`${u.input_voltage_v.toFixed(1)} V`} />
    <KV k="Output V" v={`${u.output_voltage_v.toFixed(1)} V`} />
    <KV k="Battery"  v={`${u.battery_pct.toFixed(1)} %`} />
    <KV k="Runtime"  v={`${u.runtime_minutes.toFixed(1)} min`} />
    <KV k="Load"     v={`${u.load_pct.toFixed(1)} %`} />
  </>);
}
function GenBody({ g }) {
  if (!g) return null;
  return (<>
    <KV k="State"    v={g.state} color="var(--warn)" />
    <KV k="RPM"      v={g.rpm.toFixed(0)} />
    <KV k="V"        v={`${g.voltage_ll_v.toFixed(1)} V`} />
    <KV k="Freq"     v={`${g.frequency_hz.toFixed(3)} Hz`} />
    <KV k="Oil"      v={`${g.oil_pressure_psi.toFixed(2)} psi`} />
    <KV k="Coolant"  v={`${g.coolant_temp_c.toFixed(1)} °C`} />
    <KV k="Fuel"     v={`${g.fuel_level_pct.toFixed(1)} %`} />
    <KV k="Runtime"  v={`${g.runtime_hours.toFixed(0)} h`} />
  </>);
}
