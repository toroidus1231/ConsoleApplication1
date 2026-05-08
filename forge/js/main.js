// Application entry. Wires the renderer, scene, sim, and UI into a single
// frame loop. Lazy-initializes the scene and sim only after the boot splash
// has handed off — keeping cold start fast.

import { Renderer } from "./renderer.js";
import { buildScene, PHASES, EQUIPMENT, introCamera } from "./scene.js";
import { Sim } from "./physics.js";
import { SITES, findSite, defaultSite } from "./sites.js";
import { UI } from "./ui.js";
import { saveBuild, loadBuild, clearBuild } from "./persistence.js";

const canvas = document.getElementById("gl");

// ---------- boot splash sequence ----------
const splash = document.getElementById("bootSplash");
const fill   = splash.querySelector(".bootFill");
const status = splash.querySelector(".bootStatus");

const bootSteps = [
  "initializing renderer",
  "compiling shaders",
  "warming IBL",
  "loading site palette",
  "ready",
];
let bootIdx = 0;
function bootStep() {
  status.textContent = bootSteps[bootIdx];
  fill.style.width = ((bootIdx + 1) / bootSteps.length) * 100 + "%";
  bootIdx++;
}

const renderer = new Renderer(canvas);
bootStep();   // renderer ready
bootStep();   // shaders compiled (compileProgram already ran above)

// ---------- state ----------
const state = {
  site: defaultSite(),
  phaseId: 1,
  mix: "balanced",
  build: null,                  // result of buildScene
  committedRecord: null,
  utilization: 0.65,
  bessMode: "auto",
  introStart: 0,
  introDuration: 5.0,            // seconds
  introActive: true,
  freeOrbit: false,
  orbitTheta: 0.6,
  orbitPhi:   0.6,
  orbitDist:  140,
};

const sim = new Sim();

function applySitePalette(site) {
  renderer.setEnvironment({
    skyTop: site.palette.skyTop,
    skyHorizon: site.palette.skyHorizon,
    sunColor: site.palette.sun,
    sunDir: [0.55, 0.62, 0.45],
    exposure: 1.05,
  });
}

function rebuildScene() {
  state.build = buildScene(state.phaseId, state.mix);
  renderer.setInstances(state.build.instances);
  refreshLiveMeta();
}

function refreshLiveMeta() {
  if (!state.build) return;
  const c = state.build.counts;
  const kW = c.racks * EQUIPMENT.rack.nameplateKW + c.aiRacks * EQUIPMENT.rack_hd.nameplateKW;
  document.getElementById("metaPower").textContent = (kW / 1000).toFixed(1) + " MW IT";
}

// Initial palette + scene.
applySitePalette(state.site);
bootStep();
rebuildScene();
bootStep();

// ---------- UI ----------
const ui = new UI({
  onSiteChange: (site) => {
    state.site = site;
    applySitePalette(site);
    if (sim.bound) sim.bind(site, state.build);
  },
  onPhasePreview: (phaseId, mix) => {
    state.phaseId = phaseId; state.mix = mix;
    rebuildScene();
    if (sim.bound) sim.bind(state.site, state.build);
    ui._renderBuildSummary?.();
  },
  onMixPreview: (phaseId, mix) => {
    state.phaseId = phaseId; state.mix = mix;
    rebuildScene();
    if (sim.bound) sim.bind(state.site, state.build);
    ui._renderBuildSummary?.();
  },
  onBuildCommit: (draft) => {
    state.site = findSite(draft.siteId);
    state.phaseId = draft.phaseId;
    state.mix = draft.mix;
    applySitePalette(state.site);
    rebuildScene();
    sim.bind(state.site, state.build);
    const phase = PHASES.find(p => p.id === draft.phaseId);
    const rec = saveBuild({
      site: state.site,
      phase,
      mix: draft.mix,
      counts: state.build.counts,
    });
    state.committedRecord = rec;
    ui.setCommitted(rec);
    ui.flashToast("Build commissioned · " + rec.id);
    renderer.triggerRipple();
    ui._renderBuildSummary?.();
  },
  onClearBuild: () => {
    clearBuild();
    state.committedRecord = null;
    sim.reset();
    ui.setCommitted(null);
    ui.flashToast("Build decommissioned");
  },
  onUtilization: (u) => { state.utilization = u; sim.utilization = u; },
  onBessMode: (m) => { state.bessMode = m; sim.bessMode = m; },
  onRipple: () => renderer.triggerRipple(),
  getLiveCounts: () => state.build?.counts,
});

// Restore previously committed build, if any.
const restored = loadBuild();
if (restored) {
  state.site = findSite(restored.site);
  state.phaseId = restored.phase;
  state.mix = restored.mix;
  applySitePalette(state.site);
  rebuildScene();
  sim.bind(state.site, state.build);
  state.committedRecord = restored;
  ui.draft.siteId = restored.site;
  ui.draft.phaseId = restored.phase;
  ui.draft.mix = restored.mix;
  ui.setCommitted(restored);
  // Skip the splash a bit faster if we have a build.
  bootStep();
}

// Hand off splash.
setTimeout(() => {
  bootStep();
  splash.classList.add("gone");
  state.introStart = performance.now() / 1000;
  // Trigger water-drop ripple at handoff.
  setTimeout(() => renderer.triggerRipple(), 600);
}, 700);

// ---------- input: orbit on RMB drag, scroll-zoom ----------
let dragging = false, lastX = 0, lastY = 0;
canvas.addEventListener("contextmenu", e => e.preventDefault());
canvas.addEventListener("mousedown", (e) => {
  if (e.button === 2 || e.button === 0) {
    dragging = true; lastX = e.clientX; lastY = e.clientY;
    state.freeOrbit = true;
    state.introActive = false;
  }
});
window.addEventListener("mouseup",   () => { dragging = false; });
window.addEventListener("mousemove", (e) => {
  if (!dragging) return;
  const dx = e.clientX - lastX, dy = e.clientY - lastY;
  lastX = e.clientX; lastY = e.clientY;
  state.orbitTheta -= dx * 0.005;
  state.orbitPhi   = Math.max(0.05, Math.min(Math.PI/2 - 0.05, state.orbitPhi - dy * 0.004));
});
canvas.addEventListener("wheel", (e) => {
  e.preventDefault();
  state.orbitDist *= Math.pow(1.0015, e.deltaY);
  state.orbitDist = Math.max(40, Math.min(360, state.orbitDist));
  state.freeOrbit = true;
  state.introActive = false;
}, { passive: false });

// ---------- frame loop ----------
let last = performance.now();
function frame() {
  const now = performance.now();
  const dt  = Math.min(0.05, (now - last) / 1000);
  last = now;

  // Camera.
  if (state.introActive) {
    const t = (now / 1000 - state.introStart) / state.introDuration;
    if (t >= 1) {
      state.introActive = false;
      state.freeOrbit = true;
      // Seed orbit params from end of intro.
      const c = introCamera(1, state.build?.footprint);
      const dx = c.eye[0] - c.target[0], dy = c.eye[1] - c.target[1], dz = c.eye[2] - c.target[2];
      state.orbitDist = Math.hypot(dx, dy, dz);
      state.orbitPhi  = Math.atan2(dy, Math.hypot(dx, dz));
      state.orbitTheta = Math.atan2(dx, dz);
    } else {
      const c = introCamera(t, state.build?.footprint);
      renderer.setCamera(c.eye, c.target, [0, 1, 0], 50 * Math.PI / 180);
    }
  }
  if (!state.introActive) {
    // Slow auto-orbit when user isn't dragging.
    if (!dragging) state.orbitTheta += dt * 0.04;
    const cx = 0, cy = 8, cz = 0;
    const r  = state.orbitDist;
    const eye = [
      cx + Math.sin(state.orbitTheta) * Math.cos(state.orbitPhi) * r,
      cy + Math.sin(state.orbitPhi) * r,
      cz + Math.cos(state.orbitTheta) * Math.cos(state.orbitPhi) * r,
    ];
    renderer.setCamera(eye, [cx, cy, cz], [0, 1, 0], 50 * Math.PI / 180);
  }

  renderer.render(now / 1000);

  // Sim step (only if a real build is bound).
  if (sim.bound) {
    sim.step(dt);
    updateHUD(sim.last);
    if (ui.view === "dashboard") ui.updateDashboard(sim.last, state.site);
  } else {
    updateHUDIdle();
  }

  requestAnimationFrame(frame);
}
requestAnimationFrame(frame);

function updateHUDIdle() {
  document.getElementById("hudIt").textContent    = "0.00 MW";
  document.getElementById("hudPue").textContent   = "—";
  document.getElementById("hudSoc").textContent   = "—";
  document.getElementById("hudTemp").textContent  = "—";
  document.getElementById("hudClock").textContent = "—";
}
function updateHUD(s) {
  document.getElementById("hudIt").textContent    = (s.itKW / 1000).toFixed(2) + " MW";
  document.getElementById("hudPue").textContent   = s.pue ? s.pue.toFixed(2) : "—";
  document.getElementById("hudSoc").textContent   = (s.bessFraction * 100).toFixed(0) + "%";
  document.getElementById("hudTemp").textContent  = s.coldAisleC.toFixed(1) + " °C";
  // Wall clock from sim time → 24h clock with day rollover.
  const totalSec = Math.floor(s.tSimSec) % 86400;
  const hh = String(Math.floor(totalSec / 3600)).padStart(2, "0");
  const mm = String(Math.floor((totalSec % 3600) / 60)).padStart(2, "0");
  document.getElementById("hudClock").textContent = `${hh}:${mm}`;
}

// Resize on window changes.
window.addEventListener("resize", () => renderer.resize());
