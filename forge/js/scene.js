// Scene builder: emits per-instance data for the cube primitive to compose
// a ground-floor data center: multi-volume buildings, real DC equipment,
// phase-accurate counts. What you see is what gets built.

import { FLOATS_PER_INSTANCE } from "./renderer.js";

// Equipment catalog. Each entry has nameplate physical data so the
// physics sim and the visual count match exactly.
export const EQUIPMENT = {
  rack: {
    name: "42U server rack",
    spec: "12 kW · ASHRAE A1",
    w: 0.6, h: 2.0, d: 1.2,
    color: [0.10, 0.11, 0.13], roughness: 0.55, metallic: 0.4,
    nameplateKW: 12,
  },
  rack_hd: {
    name: "AI training rack",
    spec: "45 kW · liquid-assist",
    w: 0.6, h: 2.0, d: 1.2,
    color: [0.07, 0.08, 0.10], roughness: 0.45, metallic: 0.5,
    nameplateKW: 45,
  },
  bess: {
    name: "BESS container",
    spec: "3.7 MWh · LFP",
    w: 12, h: 2.9, d: 2.4,
    color: [0.78, 0.80, 0.82], roughness: 0.55, metallic: 0.15,
    nameplateKWh: 3700,
  },
  chiller: {
    name: "Air-cooled chiller",
    spec: "1.4 MW · variable speed",
    w: 6, h: 3, d: 2,
    color: [0.55, 0.58, 0.60], roughness: 0.45, metallic: 0.6,
    nameplateKW: 1400,
  },
  transformer: {
    name: "Pad-mount transformer",
    spec: "2500 kVA · 34.5 kV/415 V",
    w: 3, h: 2.5, d: 2,
    color: [0.32, 0.36, 0.40], roughness: 0.6, metallic: 0.55,
    nameplateKVA: 2500,
  },
  generator: {
    name: "Diesel generator",
    spec: "2.5 MW · standby",
    w: 5, h: 2.5, d: 2,
    color: [0.85, 0.78, 0.30], roughness: 0.55, metallic: 0.25,
    nameplateKW: 2500,
  },
};

// Each phase declares how many of each item exist. The visual scene reads
// these counts directly — no separate "art" pipeline.
export const PHASES = [
  { id: 1, label: "Phase 1 · pilot",      halls: 1, racks: 120,  hdRacks: 0,   bess: 2, chillers: 2, transformers: 2, generators: 2 },
  { id: 2, label: "Phase 2 · build-out",  halls: 2, racks: 280,  hdRacks: 40,  bess: 4, chillers: 4, transformers: 3, generators: 3 },
  { id: 3, label: "Phase 3 · full site",  halls: 3, racks: 480,  hdRacks: 120, bess: 8, chillers: 6, transformers: 4, generators: 4 },
];

const HALL = { w: 38, h: 7.5, d: 28 };  // single hall envelope
const HALL_GAP = 4;
const ROW_PITCH_X = 1.4;   // rack pitch within a row
const ROW_PITCH_Z = 3.2;   // aisle to aisle
const RACKS_PER_ROW = 18;
const ROWS_PER_HALL = 6;

// ---- per-instance packing ----
function pushInstance(arr, idx, m, alb, mat) {
  const o = idx * FLOATS_PER_INSTANCE;
  for (let i = 0; i < 16; i++) arr[o + i] = m[i];
  arr[o + 16] = alb[0]; arr[o + 17] = alb[1]; arr[o + 18] = alb[2]; arr[o + 19] = alb[3];
  arr[o + 20] = mat[0]; arr[o + 21] = mat[1]; arr[o + 22] = mat[2]; arr[o + 23] = mat[3];
}

function compose(m, tx, ty, tz, ry, sx, sy, sz) {
  const c = Math.cos(ry), s = Math.sin(ry);
  m[0] = c*sx;  m[1] = 0;    m[2] = -s*sx; m[3] = 0;
  m[4] = 0;     m[5] = sy;   m[6] = 0;     m[7] = 0;
  m[8] = s*sz;  m[9] = 0;    m[10] = c*sz; m[11] = 0;
  m[12] = tx;   m[13] = ty;  m[14] = tz;   m[15] = 1;
}

// Materials pack: albedo (rgb) + roughness (a), then metallic, emissive, ao, tint.
const MAT = (rgb, rough, metal = 0, emiss = 0, ao = 1) =>
  [[rgb[0], rgb[1], rgb[2], rough], [metal, emiss, ao, 0]];

// One-shot scene generator. Returns {instances, counts, footprint, hallCenters}.
export function buildScene(phaseId, equipMix /* "balanced"|"ai_dense"|"hpc" */) {
  const phase = PHASES.find(p => p.id === phaseId) || PHASES[0];

  // Adjust mix without changing total rack count.
  let racks = phase.racks, hd = phase.hdRacks;
  if (equipMix === "ai_dense") {
    hd = Math.round((racks + hd) * 0.55);
    racks = (racks + phase.hdRacks) - hd;
  } else if (equipMix === "hpc") {
    hd = Math.round((racks + hd) * 0.30);
    racks = (racks + phase.hdRacks) - hd;
  }

  // Estimate worst-case instance count.
  const estimate =
    1 +                                  // ground slab
    1 +                                  // perimeter ring
    phase.halls * 6 +                    // hall walls + roof
    1 +                                  // admin block
    1 +                                  // utility wing
    racks + hd +
    phase.bess +
    phase.chillers +
    phase.transformers +
    phase.generators +
    200;                                  // landscape decor budget
  const data = new Float32Array(estimate * FLOATS_PER_INSTANCE);
  let i = 0;
  const m = new Float32Array(16);
  const push = (m_, alb, mat) => pushInstance(data, i++, m_, alb, mat);

  // ---- Site ground (concrete pad) ----
  const padW = 160, padD = 110;
  compose(m, 0, -0.05, 0, 0, padW, 0.1, padD);
  push(m, [0.16, 0.17, 0.18, 0.92], [0.0, 0, 0.85, 0]);

  // Asphalt access road.
  compose(m, 0, -0.04, padD/2 + 4, 0, padW * 0.7, 0.08, 8);
  push(m, [0.07, 0.07, 0.075, 0.95], [0.0, 0, 0.85, 0]);

  // Perimeter security wall (low ring) — eight panels.
  const wallH = 2.4, wallW = 0.4;
  const ringW = padW + 6, ringD = padD + 6;
  // four sides as long thin boxes
  compose(m,  0, wallH/2,  ringD/2, 0, ringW, wallH, wallW);
  push(m, [0.42, 0.43, 0.45, 0.7], [0.05, 0, 0.95, 0]);
  compose(m,  0, wallH/2, -ringD/2, 0, ringW, wallH, wallW);
  push(m, [0.42, 0.43, 0.45, 0.7], [0.05, 0, 0.95, 0]);
  compose(m,  ringW/2, wallH/2, 0, 0, wallW, wallH, ringD);
  push(m, [0.42, 0.43, 0.45, 0.7], [0.05, 0, 0.95, 0]);
  compose(m, -ringW/2, wallH/2, 0, 0, wallW, wallH, ringD);
  push(m, [0.42, 0.43, 0.45, 0.7], [0.05, 0, 0.95, 0]);

  // ---- Halls (multi-volume) ----
  // Halls run along +X from the admin block. Each hall is its own volume,
  // gapped from the next by HALL_GAP.
  const hallCenters = [];
  const hallStartX = -((phase.halls * HALL.w + (phase.halls - 1) * HALL_GAP) / 2) + HALL.w / 2;
  for (let h = 0; h < phase.halls; h++) {
    const cx = hallStartX + h * (HALL.w + HALL_GAP);
    hallCenters.push({ x: cx, z: 0 });

    // Walls: 4 sides + roof slab. Use semi-transparent feel with light albedo.
    const wallT = 0.6;
    const facade = [0.78, 0.80, 0.82, 0.55];
    const facadeMat = [0.4, 0, 0.95, 0];

    // Front wall (with continuous strip windows look from emissive band).
    compose(m, cx, HALL.h/2, +HALL.d/2, 0, HALL.w, HALL.h, wallT);
    push(m, facade, facadeMat);
    compose(m, cx, HALL.h/2, -HALL.d/2, 0, HALL.w, HALL.h, wallT);
    push(m, facade, facadeMat);
    compose(m, cx + HALL.w/2, HALL.h/2, 0, 0, wallT, HALL.h, HALL.d);
    push(m, facade, facadeMat);
    compose(m, cx - HALL.w/2, HALL.h/2, 0, 0, wallT, HALL.h, HALL.d);
    push(m, facade, facadeMat);

    // Glass strip running around the building (emissive).
    compose(m, cx, HALL.h - 0.6, +HALL.d/2 + 0.05, 0, HALL.w * 0.96, 0.6, 0.05);
    push(m, [0.55, 0.78, 1.0, 0.2], [0.7, 0.4, 1, 0]);
    compose(m, cx, HALL.h - 0.6, -HALL.d/2 - 0.05, 0, HALL.w * 0.96, 0.6, 0.05);
    push(m, [0.55, 0.78, 1.0, 0.2], [0.7, 0.4, 1, 0]);

    // Roof slab.
    compose(m, cx, HALL.h + 0.25, 0, 0, HALL.w + 0.6, 0.5, HALL.d + 0.6);
    push(m, [0.36, 0.38, 0.40, 0.85], [0.1, 0, 0.95, 0]);

    // Roof rooftop unit (a few small boxes — RTU/CRAC stacks).
    for (let k = 0; k < 3; k++) {
      const ox = cx + (k - 1) * 6;
      compose(m, ox, HALL.h + 1.4, 0, 0, 3, 1.8, 4.2);
      push(m, [0.55, 0.57, 0.60, 0.5], [0.5, 0, 1, 0]);
    }
  }

  // ---- Admin block (separate volume, glassy) ----
  const adminW = 18, adminH = 6, adminD = 12;
  const adminX = hallStartX - HALL.w/2 - HALL_GAP - adminW/2;
  compose(m, adminX, adminH/2, -6, 0, adminW, adminH, adminD);
  push(m, [0.18, 0.24, 0.32, 0.35], [0.6, 0, 1, 0]);
  // Admin glass band.
  for (let f = 0; f < 2; f++) {
    compose(m, adminX, 1.3 + f * 2.4, -6 + adminD/2 + 0.05, 0, adminW * 0.95, 1.0, 0.08);
    push(m, [0.55, 0.78, 1.0, 0.15], [0.8, 0.6, 1, 0]);
  }

  // ---- Utility wing (north of halls) ----
  const utilW = 24, utilH = 5, utilD = 8;
  compose(m, 0, utilH/2, -HALL.d/2 - utilD/2 - 2, 0, utilW, utilH, utilD);
  push(m, [0.48, 0.50, 0.52, 0.7], [0.2, 0, 0.95, 0]);

  // ---- Switchyard transformers (north strip) ----
  const txStart = -((phase.transformers - 1) * 5) / 2;
  for (let t = 0; t < phase.transformers; t++) {
    const e = EQUIPMENT.transformer;
    const x = txStart + t * 5;
    const z = -HALL.d/2 - utilD - 5;
    compose(m, x, e.h/2, z, 0, e.w, e.h, e.d);
    push(m, [...e.color, e.roughness], [e.metallic, 0, 0.95, 0]);
    // ceramic bushing array (small cylinders → boxes).
    for (let k = 0; k < 3; k++) {
      compose(m, x - 0.8 + k * 0.8, e.h + 0.4, z, 0, 0.18, 0.8, 0.18);
      push(m, [0.65, 0.55, 0.42, 0.35], [0.1, 0, 1, 0]);
    }
  }

  // ---- BESS containers (south strip) ----
  const bessStart = -((phase.bess - 1) * 14) / 2;
  for (let b = 0; b < phase.bess; b++) {
    const e = EQUIPMENT.bess;
    const x = bessStart + b * 14;
    const z = HALL.d/2 + 6;
    compose(m, x, e.h/2, z, 0, e.w, e.h, e.d);
    push(m, [...e.color, e.roughness], [e.metallic, 0, 1, 0]);
    // Thin air-intake band.
    compose(m, x, e.h/2 + 0.1, z + e.d/2 + 0.02, 0, e.w * 0.9, 0.4, 0.06);
    push(m, [0.20, 0.22, 0.24, 0.5], [0.2, 0, 0.9, 0]);
    // BESS small status LED.
    compose(m, x - e.w/2 + 0.5, e.h - 0.4, z + e.d/2 + 0.05, 0, 0.18, 0.18, 0.05);
    push(m, [0.5, 1.0, 0.7, 0.2], [0, 1.4, 1, 0]);
  }

  // ---- Chillers (south, beyond BESS) ----
  const chStart = -((phase.chillers - 1) * 7) / 2;
  for (let c = 0; c < phase.chillers; c++) {
    const e = EQUIPMENT.chiller;
    const x = chStart + c * 7;
    const z = HALL.d/2 + 14;
    compose(m, x, e.h/2, z, 0, e.w, e.h, e.d);
    push(m, [...e.color, e.roughness], [e.metallic, 0, 0.95, 0]);
    // Top fans (4 squares per chiller).
    for (let f = 0; f < 4; f++) {
      compose(m, x - e.w/2 + 0.9 + f * 1.4, e.h + 0.05, z, 0, 1.1, 0.1, 1.1);
      push(m, [0.18, 0.20, 0.22, 0.4], [0.2, 0, 1, 0]);
    }
  }

  // ---- Generators (east edge) ----
  const genStart = -((phase.generators - 1) * 6) / 2;
  for (let g = 0; g < phase.generators; g++) {
    const e = EQUIPMENT.generator;
    const x = ringW/2 - 8;
    const z = genStart + g * 6;
    compose(m, x, e.h/2, z, 0, e.w, e.h, e.d);
    push(m, [...e.color, e.roughness], [e.metallic, 0, 1, 0]);
    // Stack.
    compose(m, x + 1.2, e.h + 0.6, z, 0, 0.4, 1.2, 0.4);
    push(m, [0.18, 0.18, 0.20, 0.5], [0.4, 0, 1, 0]);
  }

  // ---- Server racks inside each hall (rows + aisles) ----
  // Rows are arranged along +X, aisles along Z. We alternate hot/cold aisles
  // visually by rotating every other row 180° (back-to-back).
  const rackCounts = { rack: 0, rack_hd: 0 };
  const totalRacksTarget = racks + hd;
  let placed = 0;
  for (let h = 0; h < phase.halls; h++) {
    const cx = hallCenters[h].x;
    const startX = cx - ((RACKS_PER_ROW - 1) * ROW_PITCH_X) / 2;
    const startZ = -((ROWS_PER_HALL - 1) * ROW_PITCH_Z) / 2;
    for (let r = 0; r < ROWS_PER_HALL; r++) {
      for (let c = 0; c < RACKS_PER_ROW; c++) {
        if (placed >= totalRacksTarget) break;
        // Decide AI-dense vs standard. Alternate rows for AI mix when present.
        const useHd = rackCounts.rack_hd < hd && (r % 2 === 0);
        const ek = useHd ? "rack_hd" : "rack";
        const e = EQUIPMENT[ek];
        if (useHd) rackCounts.rack_hd++; else rackCounts.rack++;

        const x = startX + c * ROW_PITCH_X;
        const z = startZ + r * ROW_PITCH_Z;
        const ry = (r % 2 === 0) ? 0 : Math.PI;
        compose(m, x, e.h/2, z, ry, e.w, e.h, e.d);
        push(m, [...e.color, e.roughness], [e.metallic, 0, 0.85, 0]);

        // Front bezel (a thin emissive vertical strip — server activity LEDs).
        // Slightly offset along +Z (or -Z) depending on rotation.
        const dirZ = ry === 0 ? 1 : -1;
        const ledColor = useHd ? [0.4, 0.85, 1.4] : [1.2, 0.55, 0.25];
        compose(m, x, e.h/2, z + dirZ * (e.d/2 + 0.005), ry, 0.10, 1.4, 0.02);
        push(m, [...ledColor, 0.2], [0, 1.4, 1, 0]);
        placed++;
      }
    }
  }

  // ---- Light decor: trees ----
  const numTrees = 30;
  for (let t = 0; t < numTrees; t++) {
    const angle = (t / numTrees) * Math.PI * 2;
    const r = ringW/2 - 2 + Math.sin(t * 1.7) * 0.4;
    const tx = Math.cos(angle) * r * 0.9;
    const tz = Math.sin(angle) * (ringD/2 - 2) * 0.9;
    if (Math.abs(tz) < HALL.d/2 + 1 && Math.abs(tx) < HALL.w * phase.halls) continue;
    // trunk
    compose(m, tx, 0.9, tz, 0, 0.18, 1.8, 0.18);
    push(m, [0.20, 0.14, 0.08, 0.95], [0.0, 0, 0.9, 0]);
    // canopy
    compose(m, tx, 2.6, tz, t * 0.3, 1.4, 1.4, 1.4);
    push(m, [0.18, 0.30, 0.18, 0.85], [0.0, 0, 0.9, 0]);
  }

  const counts = {
    racks: rackCounts.rack,
    aiRacks: rackCounts.rack_hd,
    bess: phase.bess,
    chillers: phase.chillers,
    transformers: phase.transformers,
    generators: phase.generators,
    halls: phase.halls,
  };
  // Trim to actual instance count.
  const trimmed = data.subarray(0, i * FLOATS_PER_INSTANCE);
  return {
    instances: trimmed,
    counts,
    footprint: { w: padW, d: padD },
    hallCenters,
    phase,
  };
}

// Camera-fly intro path, parameterized by t ∈ [0..1].
export function introCamera(t, footprint) {
  // Eases in from a high-altitude approach to a corner orbit.
  const ease = (x) => x*x*(3 - 2*x);
  const u = ease(Math.min(1, t));

  const startEye  = [180, 120,  220];
  const endEye    = [ 95,  35,  120];
  const startTgt  = [  0,  20,    0];
  const endTgt    = [  0,   8,    0];

  const eye = [
    startEye[0] + (endEye[0] - startEye[0]) * u,
    startEye[1] + (endEye[1] - startEye[1]) * u,
    startEye[2] + (endEye[2] - startEye[2]) * u,
  ];
  const tgt = [
    startTgt[0] + (endTgt[0] - startTgt[0]) * u,
    startTgt[1] + (endTgt[1] - startTgt[1]) * u,
    startTgt[2] + (endTgt[2] - startTgt[2]) * u,
  ];
  return { eye, target: tgt };
}
