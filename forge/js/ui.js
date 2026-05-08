// All sidebar panels and the topbar nav wiring. Pure DOM — no framework.

import { SITES, findSite } from "./sites.js";
import { PHASES, EQUIPMENT } from "./scene.js";

const fmtMW   = (kW) => (kW / 1000).toFixed(2) + " MW";
const fmtKW   = (kW) => kW.toFixed(0) + " kW";
const fmtPct  = (x) => (x * 100).toFixed(1) + "%";
const fmtTemp = (c)  => c.toFixed(1) + " °C";

const tag = (n, cls, html) => {
  const e = document.createElement(n);
  if (cls) e.className = cls;
  if (html != null) e.innerHTML = html;
  return e;
};

export class UI {
  constructor(opts) {
    this.opts = opts;             // { onSiteChange, onBuildCommit, onPhasePreview, onMixPreview, onClearBuild, onBessMode, onUtilization }
    this.sidebar = document.getElementById("sidebar");
    this.topbar  = document.getElementById("topbar");
    this.toast   = document.getElementById("toast");

    this.view = "site";
    this.draft = {
      siteId: SITES[0].id,
      phaseId: 1,
      mix: "balanced",
    };
    this.committed = null;        // { siteId, phaseId, mix, counts, createdAt }

    this._wireNav();
    this.render();
  }

  _wireNav() {
    this.topbar.querySelectorAll("nav button").forEach((b) => {
      b.addEventListener("click", () => {
        if (b.classList.contains("gated")) {
          this.flashToast("Commit a build first to unlock " + b.textContent);
          return;
        }
        this.view = b.dataset.view;
        this.topbar.querySelectorAll("nav button").forEach(x => x.classList.toggle("active", x === b));
        document.body.dataset.view = this.view;
        this.render();
      });
    });
  }

  setCommitted(record) {
    this.committed = record;
    this._syncGating();
    this._syncTopMeta();
  }

  _syncGating() {
    const gated = !this.committed;
    this.topbar.querySelectorAll("nav button[data-gated='1']").forEach((b) => {
      b.classList.toggle("gated", gated);
    });
  }

  _syncTopMeta() {
    if (this.committed) {
      const site = findSite(this.committed.site);
      const phase = PHASES.find(p => p.id === this.committed.phase);
      document.getElementById("metaSite").textContent  = site.city;
      document.getElementById("metaPhase").textContent = phase ? phase.label : "phase ?";
    } else {
      document.getElementById("metaSite").textContent  = "— select a site —";
      document.getElementById("metaPhase").textContent = "phase 0";
    }
  }

  flashToast(msg) {
    this.toast.textContent = msg;
    this.toast.classList.add("show");
    clearTimeout(this._toastTimer);
    this._toastTimer = setTimeout(() => this.toast.classList.remove("show"), 2200);
  }

  // ---------------------------------------------------------------- render

  render() {
    this.sidebar.innerHTML = "";
    if (this.view === "site")      this._renderSite();
    if (this.view === "build")     this._renderBuild();
    if (this.view === "dashboard") this._renderDashboard();
    if (this.view === "control")   this._renderControl();
  }

  _renderSite() {
    const p = tag("div", "panel");
    p.appendChild(tag("h3", null, "Site selection <span class='badge'>step 1</span>"));
    p.appendChild(tag("p", "gatedNote", `
      Pick a candidate site. Each location ships with its own grid mix,
      retail energy price, ambient climate, and water cost. The visual
      palette adapts (sky, sun) to give an arch-viz feel.
    `));

    const us = SITES.filter(s => s.country === "US");
    const ca = SITES.filter(s => s.country === "CA");

    p.appendChild(tag("label", "field", "United States"));
    p.appendChild(this._siteSelect(us));

    p.appendChild(tag("label", "field", "Canada"));
    p.appendChild(this._siteSelect(ca));

    const detail = tag("div");
    detail.id = "siteDetail";
    p.appendChild(detail);

    this.sidebar.appendChild(p);
    this._renderSiteDetail();
  }

  _siteSelect(list) {
    const sel = document.createElement("select");
    sel.appendChild(new Option("— select —", ""));
    for (const s of list) {
      const opt = new Option(`${s.city} · ${s.region}`, s.id);
      sel.appendChild(opt);
    }
    if (list.find(s => s.id === this.draft.siteId)) sel.value = this.draft.siteId;
    sel.addEventListener("change", () => {
      if (!sel.value) return;
      this.draft.siteId = sel.value;
      this.opts.onSiteChange?.(findSite(sel.value));
      this._renderSiteDetail();
    });
    return sel;
  }

  _renderSiteDetail() {
    const host = this.sidebar.querySelector("#siteDetail");
    if (!host) return;
    host.innerHTML = "";
    const s = findSite(this.draft.siteId);
    const rows = [
      ["Region",       s.region],
      ["Grid",         `${s.grid.kind}  ·  ${s.grid.carbonGCO2PerKWh} g CO₂/kWh`],
      ["Retail power", `$${s.grid.retailUSDPerKWh.toFixed(3)} /kWh`],
      ["Ambient mean", `${s.climate.tDbAvgC.toFixed(0)} °C`],
      ["Design dry-bulb", `${s.climate.tDbDesignC.toFixed(0)} °C`],
      ["Water",        `$${s.waterUSDPerKgal.toFixed(1)} /kgal`],
    ];
    for (const [k, v] of rows) {
      const r = tag("div", "row");
      r.appendChild(tag("span", "k", k));
      r.appendChild(tag("span", "v", v));
      host.appendChild(r);
    }

    const next = tag("button", "btn primary");
    next.textContent = "Continue → Build";
    next.addEventListener("click", () => {
      this.view = "build";
      this.topbar.querySelectorAll("nav button").forEach(x => x.classList.toggle("active", x.dataset.view === "build"));
      document.body.dataset.view = "build";
      this.render();
    });
    const row = tag("div", "btnRow");
    row.appendChild(next);
    host.appendChild(row);
  }

  _renderBuild() {
    const p = tag("div", "panel");
    p.appendChild(tag("h3", null, "Build configuration <span class='badge'>step 2</span>"));
    p.appendChild(tag("p", "gatedNote", `
      Choose the phase and equipment mix. Counts are <b>nameplate-accurate</b>:
      the racks, BESS containers, chillers, transformers and generators you
      see on the floor are exactly what the simulator wires up.
    `));

    p.appendChild(tag("label", "field", "Phase"));
    const phaseSel = document.createElement("select");
    PHASES.forEach((ph) => phaseSel.appendChild(new Option(ph.label, ph.id)));
    phaseSel.value = String(this.draft.phaseId);
    phaseSel.addEventListener("change", () => {
      this.draft.phaseId = parseInt(phaseSel.value);
      this.opts.onPhasePreview?.(this.draft.phaseId, this.draft.mix);
      this._renderBuildSummary();
    });
    p.appendChild(phaseSel);

    p.appendChild(tag("label", "field", "Equipment mix"));
    const grid = tag("div", "equipPick");
    const opts = [
      { v: "balanced",  name: "Balanced", spec: "12 kW racks + AI mix" },
      { v: "ai_dense",  name: "AI dense", spec: "high 45 kW share" },
      { v: "hpc",       name: "HPC",      spec: "moderate AI density" },
    ];
    for (const o of opts) {
      const b = document.createElement("button");
      b.dataset.v = o.v;
      b.classList.toggle("selected", o.v === this.draft.mix);
      b.innerHTML = `<span class="name">${o.name}</span><span class="spec">${o.spec}</span>`;
      b.addEventListener("click", () => {
        this.draft.mix = o.v;
        grid.querySelectorAll("button").forEach(x => x.classList.toggle("selected", x.dataset.v === o.v));
        this.opts.onMixPreview?.(this.draft.phaseId, this.draft.mix);
        this._renderBuildSummary();
      });
      grid.appendChild(b);
    }
    p.appendChild(grid);

    p.appendChild(tag("label", "field", "Build summary"));
    const sum = tag("div"); sum.id = "buildSum";
    p.appendChild(sum);

    const row = tag("div", "btnRow");
    const commit = tag("button", "btn primary");
    commit.textContent = "Commission build";
    commit.addEventListener("click", () => this.opts.onBuildCommit?.(this.draft));

    const reset = tag("button", "btn danger");
    reset.textContent = "Decommission";
    reset.disabled = !this.committed;
    reset.classList.toggle("disabled", !this.committed);
    reset.addEventListener("click", () => this.opts.onClearBuild?.());

    row.appendChild(commit);
    row.appendChild(reset);
    p.appendChild(row);

    this.sidebar.appendChild(p);
    this._renderBuildSummary();
  }

  // Reads counts back from latest live scene snapshot via opts.getLiveCounts().
  _renderBuildSummary() {
    const host = this.sidebar.querySelector("#buildSum");
    if (!host) return;
    host.innerHTML = "";
    const counts = this.opts.getLiveCounts?.();
    if (!counts) return;
    const totalRacks = counts.racks + counts.aiRacks;
    const itMW = (counts.racks * EQUIPMENT.rack.nameplateKW
               +  counts.aiRacks * EQUIPMENT.rack_hd.nameplateKW) / 1000;
    const bessMWh = (counts.bess * EQUIPMENT.bess.nameplateKWh) / 1000;
    const rows = [
      ["Halls",         counts.halls],
      ["Server racks",  `${totalRacks} (${counts.racks} std + ${counts.aiRacks} AI)`],
      ["IT nameplate",  itMW.toFixed(1) + " MW"],
      ["BESS",          `${counts.bess} container · ${bessMWh.toFixed(1)} MWh`],
      ["Chillers",      counts.chillers],
      ["Transformers",  counts.transformers],
      ["Generators",    counts.generators],
    ];
    for (const [k, v] of rows) {
      const r = tag("div", "row");
      r.appendChild(tag("span", "k", k));
      r.appendChild(tag("span", "v", v));
      host.appendChild(r);
    }
    if (this.committed) {
      const r = tag("div", "row");
      r.appendChild(tag("span", "k", "Build ID"));
      r.appendChild(tag("span", "v good", this.committed.id));
      host.appendChild(r);
    }
  }

  _renderDashboard() {
    if (!this.committed) return this._renderGated("Dashboard");
    const p = tag("div", "panel");
    p.appendChild(tag("h3", null, "Live dashboard <span class='badge'>real-time</span>"));
    const host = tag("div"); host.id = "dashRows";
    p.appendChild(host);
    this.sidebar.appendChild(p);

    const p2 = tag("div", "panel");
    p2.appendChild(tag("h3", null, "Operating envelope"));
    const env = tag("div"); env.id = "dashEnv";
    p2.appendChild(env);
    this.sidebar.appendChild(p2);

    this._dashHosts = { rows: host, env };
  }

  updateDashboard(sample, site) {
    if (!this._dashHosts || this.view !== "dashboard") return;
    const { rows, env } = this._dashHosts;
    rows.innerHTML = "";
    env.innerHTML = "";

    const s = sample;
    const dataRows = [
      ["IT load",        fmtMW(s.itKW)],
      ["Chiller load",   fmtMW(s.chillerKW)],
      ["UPS losses",     fmtKW(s.upsKW)],
      ["Fans / pumps",   fmtKW(s.fpKW)],
      ["Site total",     fmtMW(s.totalKW)],
      ["PUE",            s.pue ? s.pue.toFixed(3) : "—"],
      ["Grid draw",      fmtMW(s.gridKW)],
      ["Carbon",         (s.carbonGperS * 3600 / 1000).toFixed(1) + " kg/h CO₂"],
    ];
    for (const [k, v] of dataRows) {
      const r = tag("div", "row");
      r.appendChild(tag("span", "k", k));
      r.appendChild(tag("span", "v", v));
      rows.appendChild(r);
    }

    const envRows = [
      ["Outdoor air",    fmtTemp(s.oatC)],
      ["Cold aisle",     fmtTemp(s.coldAisleC)],
      ["Free cooling",   fmtPct(s.freeFrac)],
      ["Chiller COP",    s.cop.toFixed(2)],
      ["BESS SoC",       fmtPct(s.bessFraction)],
      ["BESS power",     (s.bessKW > 0 ? "+" : "") + fmtKW(s.bessKW) + (s.bessKW > 0 ? "  charging" : s.bessKW < 0 ? "  discharging" : "  idle")],
      ["Utilization",    s.utilizationPct.toFixed(0) + "%"],
    ];
    for (const [k, v] of envRows) {
      const r = tag("div", "row");
      r.appendChild(tag("span", "k", k));
      r.appendChild(tag("span", "v", v));
      env.appendChild(r);
    }

    // PUE delta bar.
    const max = 2.0, min = 1.05;
    const pct = Math.max(0, Math.min(1, (s.pue - min) / (max - min)));
    const bar = tag("div", "deltaBar");
    bar.appendChild(tag("div"));
    env.appendChild(bar);
    bar.firstChild.style.width = (pct * 100).toFixed(1) + "%";
  }

  _renderControl() {
    if (!this.committed) return this._renderGated("Control Room");

    const p = tag("div", "panel");
    p.appendChild(tag("h3", null, "Control Room <span class='badge'>operator</span>"));

    p.appendChild(tag("label", "field", "IT utilization"));
    const sl = document.createElement("input");
    sl.type = "range"; sl.min = "0"; sl.max = "100"; sl.value = "65";
    const lab = tag("div", "row"); lab.innerHTML = `<span class="k">target</span><span class="v" id="utilLabel">65%</span>`;
    sl.addEventListener("input", () => {
      document.getElementById("utilLabel").textContent = sl.value + "%";
      this.opts.onUtilization?.(parseFloat(sl.value) / 100);
    });
    p.appendChild(sl);
    p.appendChild(lab);

    p.appendChild(tag("label", "field", "BESS dispatch"));
    const grid = tag("div", "equipPick");
    [
      ["auto",      "Auto",      "diurnal economic"],
      ["charge",    "Charge",    "+ from grid"],
      ["discharge", "Discharge", "− to load"],
    ].forEach(([v, name, spec]) => {
      const b = document.createElement("button");
      b.dataset.v = v;
      if (v === "auto") b.classList.add("selected");
      b.innerHTML = `<span class="name">${name}</span><span class="spec">${spec}</span>`;
      b.addEventListener("click", () => {
        grid.querySelectorAll("button").forEach(x => x.classList.toggle("selected", x.dataset.v === v));
        this.opts.onBessMode?.(v);
      });
      grid.appendChild(b);
    });
    p.appendChild(grid);

    const row = tag("div", "btnRow");
    const ripple = tag("button", "btn");
    ripple.textContent = "Run depth scan";
    ripple.addEventListener("click", () => this.opts.onRipple?.());
    row.appendChild(ripple);
    p.appendChild(row);

    this.sidebar.appendChild(p);
  }

  _renderGated(label) {
    const p = tag("div", "panel");
    p.appendChild(tag("h3", null, label + " <span class='badge'>locked</span>"));
    p.appendChild(tag("div", "gatedNote", `
      <b>${label}</b> is gated until you commission a real build. The Forge
      will not let you operate a fleet that doesn't exist on the floor.<br/><br/>
      Go to <b>Site</b> → <b>Build</b> and commit a configuration to unlock.
    `));
    this.sidebar.appendChild(p);
  }
}
