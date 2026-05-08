// Physics-grounded data center simulator.
//
// We model the smallest set of state that explains the headline numbers:
//   - IT load (sum of per-rack utilization × nameplate)
//   - Cooling load (closed-form CRAH model: Q_chiller = IT × (1 - free_cooling_fraction))
//   - PUE (= total / IT, where total includes UPS + fan/pump losses)
//   - BESS state-of-charge (charge from grid surplus, discharge during peak)
//   - Cold-aisle temperature (first-order: airflow vs IT load vs setpoint)
//   - Wall-clock time (advances faster-than-real-time so trends are visible)
//
// Units are SI internally (W, J, K). The UI converts to MW/°C for display.

import { EQUIPMENT } from "./scene.js";
import { clamp, lerp, smoothstep } from "./math.js";

// Loose calibration values — chosen to be defensible, not exact.
const UPS_LOSS_FRAC      = 0.025;   // 2.5% UPS losses
const TX_LOSS_FRAC       = 0.005;   // 0.5% transformer
const FAN_PUMP_FRAC      = 0.025;   // 2.5% fans/pumps overhead always-on
const FREE_COOL_KNEE_C   = 14;      // free cooling fully effective below 14°C OAT
const FREE_COOL_OFF_C    = 26;      // no free cooling above 26°C OAT
const CRAH_DELTAT        = 12;      // K rise across the rack
const COLD_AISLE_SETPT_C = 22;      // ASHRAE A1 cold-aisle target
const AIR_TC_SECONDS     = 90;      // thermal time constant of the room
const BESS_CHG_RATE_MW   = 0.6;     // per container (~0.16C average)
const BESS_DSCH_RATE_MW  = 0.9;     // per container
const BESS_ROUND_TRIP    = 0.92;
const SIM_TIME_SCALE     = 60;      // 1 wall second = 60 sim seconds

export class Sim {
  constructor() {
    this.reset();
  }

  reset() {
    this.t = 0;                       // sim seconds since start
    this.utilization = 0.65;          // 0..1, IT utilization
    this.coldAisleC = COLD_AISLE_SETPT_C;
    this.bessFraction = 0.5;          // SoC 0..1
    this.bessMode = "auto";           // "auto" | "charge" | "discharge"
    this.diurnal = true;
    this.bound = null;                // {site, build}
    this.history = [];                // recent samples for charts
    this.maxHistory = 240;
  }

  bind(site, build) {
    this.bound = { site, build };
    // Pick a reasonable starting cold aisle near setpoint.
    this.coldAisleC = COLD_AISLE_SETPT_C + (site.climate.tDbAvgC > 18 ? 1 : 0);
  }

  // Effective IT capacity in MW, summing per-rack nameplate × utilization.
  itCapacityKW() {
    if (!this.bound) return 0;
    const { counts } = this.bound.build;
    return counts.racks    * EQUIPMENT.rack.nameplateKW
         + counts.aiRacks  * EQUIPMENT.rack_hd.nameplateKW;
  }

  // Total BESS energy capacity in kWh.
  bessCapacityKWh() {
    if (!this.bound) return 0;
    return this.bound.build.counts.bess * EQUIPMENT.bess.nameplateKWh;
  }

  // Outdoor temperature: site mean ± diurnal swing, locked to wall clock.
  outdoorC(simSecondsAbs) {
    const s = this.bound?.site;
    if (!s) return 15;
    const hour = ((simSecondsAbs / 3600) % 24 + 24) % 24;
    const swing = (s.climate.tDbDesignC - s.climate.tDbAvgC) * 0.45;
    // Sin curve peaking at 15:00.
    const phase = (hour - 15) * (Math.PI / 12);
    const t = s.climate.tDbAvgC + Math.cos(phase) * swing;
    return this.diurnal ? t : s.climate.tDbAvgC;
  }

  freeCoolingFraction(oatC) {
    if (oatC <= FREE_COOL_KNEE_C) return 1.0;
    if (oatC >= FREE_COOL_OFF_C)  return 0.05;
    return 1.0 - (oatC - FREE_COOL_KNEE_C) / (FREE_COOL_OFF_C - FREE_COOL_KNEE_C);
  }

  // Step the sim by `dtWall` real seconds.
  step(dtWall) {
    if (!this.bound) return;
    const dt = dtWall * SIM_TIME_SCALE;   // sim seconds to advance
    this.t += dt;

    // IT power.
    const u = clamp(this.utilization, 0, 1);
    const itKW = this.itCapacityKW() * u;

    // Cooling — fraction handled mechanically vs free.
    const oatC = this.outdoorC(this.t);
    const freeFrac = this.freeCoolingFraction(oatC);
    const mechKW = itKW * (1 - freeFrac);
    // Mechanical cooling COP scales with OAT.
    const cop = lerp(6.5, 2.6, smoothstep(10, 38, oatC));
    const chillerKW = mechKW / cop;

    // Always-on overheads.
    const upsKW = itKW * UPS_LOSS_FRAC;
    const txKW  = itKW * TX_LOSS_FRAC;
    const fpKW  = itKW * FAN_PUMP_FRAC;

    const totalKW = itKW + chillerKW + upsKW + txKW + fpKW;
    const pue = itKW > 1 ? totalKW / itKW : 0;

    // BESS — auto mode: discharge on diurnal peak, charge in off-peak.
    const peakHour = ((this.t / 3600) % 24 + 24) % 24;
    const isPeak = peakHour > 13 && peakHour < 19;
    const cap = this.bessCapacityKWh();
    let bessKW = 0;
    if (cap > 0) {
      let mode = this.bessMode;
      if (mode === "auto") mode = isPeak ? "discharge" : "charge";
      const containers = this.bound.build.counts.bess;
      if (mode === "discharge" && this.bessFraction > 0.05) {
        bessKW = -BESS_DSCH_RATE_MW * 1000 * containers;
      } else if (mode === "charge" && this.bessFraction < 0.95) {
        bessKW =  BESS_CHG_RATE_MW * 1000 * containers;
      }
      const dE = (bessKW * dt / 3600);     // kWh delta this step
      const eff = bessKW > 0 ? BESS_ROUND_TRIP : 1;  // charge has loss; discharge already discounted
      this.bessFraction = clamp(this.bessFraction + (dE * eff) / cap, 0, 1);
    }

    // Cold aisle thermal: first-order toward (setpoint + over/under-cooling drift).
    const targetDrift = (1 - freeFrac) > 0.85 ? 0 : (oatC - FREE_COOL_KNEE_C) * 0.04;
    const target = COLD_AISLE_SETPT_C + clamp(targetDrift, -1.5, 3);
    const alpha = 1 - Math.exp(-dt / AIR_TC_SECONDS);
    this.coldAisleC = lerp(this.coldAisleC, target, alpha);

    // Grid power draw includes the BESS contribution (positive = grid imports).
    const gridKW = totalKW + bessKW;

    // Cost / carbon (per second, integrated downstream by caller).
    const grid = this.bound.site.grid;
    const carbonGperS = (gridKW * grid.carbonGCO2PerKWh) / 3600;

    // Snapshot.
    const sample = {
      tSimSec: this.t,
      itKW, chillerKW, upsKW, txKW, fpKW, totalKW,
      pue, oatC, freeFrac, cop,
      bessKW, bessFraction: this.bessFraction,
      coldAisleC: this.coldAisleC,
      gridKW, carbonGperS,
      utilizationPct: u * 100,
    };
    this.history.push(sample);
    if (this.history.length > this.maxHistory) this.history.shift();
    this.last = sample;
    return sample;
  }
}
