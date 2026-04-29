// Layout coordinates for the single-line diagram. Pure data — keeps the
// component logic in SingleLine.jsx focused on rendering.
//
// Canvas grid: 1700 × 1100 px. Origin top-left; (x, y) is the top-left
// corner of each card. Width is fixed via CSS unless overridden with `w`.

export const SLD_W = 1700;
export const SLD_H = 1100;

// ---------------------------------------------------------------------------
//
//  Column plan (left section: utility / MV / LV chain)
//    col-1: x=  40   ─ XFMR-A1 / MTZ-A1 / LV-A1
//    col-2: x= 220   ─ XFMR-A2 / MTZ-A2 / LV-A2
//    col-3: x= 400   ─ XFMR-B1 / MTZ-B1 / LV-B1
//    col-4: x= 580   ─ XFMR-B2 / MTZ-B2 / LV-B2
//
//  Right section (ATS / UPS):  x= 820 .. 1080
//  Right section (Gen panels): x=1180 .. 1460
// ---------------------------------------------------------------------------

export const POS = {
  // Tier 1 — Sources (y=30)
  "util-A":         { x:  40, y:  30 },
  "util-B":         { x: 320, y:  30 },

  // Tier 2 — MV main breakers (y=130)
  "mv-main-A":      { x:  60, y: 130 },
  "mv-main-B":      { x: 340, y: 130 },
  "sel-mv-main-A":  { x: 240, y: 134 },
  "sel-mv-main-B":  { x: 520, y: 134 },

  // Tier 3 — MV bus bars (y=220)
  "mv-bus-A":       { x:  40, y: 220, w: 320 },
  "mv-bus-B":       { x: 380, y: 220, w: 320 },

  // MV tie + relay
  "mv-tie":         { x: 305, y: 270 },
  "sel-mv-tie":     { x: 320, y: 320 },

  // Decorative PD monitors flanking the buses
  "insulgard-mv-A": { x:  40, y: 270 },
  "insulgard-mv-B": { x: 570, y: 270 },

  // Tier 4 — Transformers (y=400)
  "xfmr-A1":        { x:  40, y: 400 },
  "xfmr-A2":        { x: 220, y: 400 },
  "xfmr-B1":        { x: 400, y: 400 },
  "xfmr-B2":        { x: 580, y: 400 },

  // Tier 5 — MTZ incoming breakers (y=560)
  "mtz-inc-A1":     { x:  40, y: 560 },
  "mtz-inc-A2":     { x: 220, y: 560 },
  "mtz-inc-B1":     { x: 400, y: 560 },
  "mtz-inc-B2":     { x: 580, y: 560 },

  // Tier 6 — LV bus bars (y=650)
  "lv-bus-A1":      { x:  40, y: 650, w: 160 },
  "lv-bus-A2":      { x: 220, y: 650, w: 160 },
  "lv-bus-B1":      { x: 400, y: 650, w: 160 },
  "lv-bus-B2":      { x: 580, y: 650, w: 160 },

  // Right section — ATS / UPS chain
  "ats-1":          { x: 820, y: 660 },
  "ats-2":          { x: 1000, y: 660 },
  "ups-A":          { x: 820, y: 800 },
  "ups-B":          { x: 1000, y: 800 },

  // Far right — generator paralleling bus + gen panels
  "gen-bus":        { x: 1240, y:  30 },
  "gen-1":          { x: 1180, y: 140 },
  "gen-2":          { x: 1180, y: 360 },

  // Legend (top right corner of the canvas, above the gen panels)
  "legend":         { x: 1480, y:  30 },
};

// Anchor points: where wires attach to a card. {x, y} relative to the
// canvas, not the card. Defaults to card center if missing.
export function anchor(id, side, devices = POS) {
  const p = devices[id];
  if (!p) return { x: 0, y: 0 };
  const w = p.w || cardWidth(id);
  const h = cardHeight(id);
  switch (side) {
    case "top":    return { x: p.x + w / 2, y: p.y };
    case "bottom": return { x: p.x + w / 2, y: p.y + h };
    case "left":   return { x: p.x,         y: p.y + h / 2 };
    case "right":  return { x: p.x + w,     y: p.y + h / 2 };
    default:       return { x: p.x + w / 2, y: p.y + h / 2 };
  }
}

function cardWidth(id) {
  if (id.startsWith("xfmr-")) return 130;
  if (id.startsWith("mtz-")) return 160;
  if (id.startsWith("mv-main-")) return 160;
  if (id === "mv-tie") return 130;
  if (id.startsWith("ats-")) return 140;
  if (id.startsWith("ups-")) return 140;
  if (id.startsWith("gen-") && id !== "gen-bus") return 280;
  if (id === "gen-bus") return 200;
  if (id.startsWith("util-")) return 200;
  if (id.startsWith("sel-")) return 88;
  if (id.startsWith("insulgard")) return 130;
  return 130;
}

function cardHeight(id) {
  if (id.startsWith("xfmr-")) return 110;
  if (id.startsWith("mv-bus") || id.startsWith("lv-bus")) return 30;
  if (id.startsWith("ats-")) return 76;
  if (id.startsWith("ups-")) return 110;
  if (id.startsWith("gen-") && id !== "gen-bus") return 200;
  if (id === "gen-bus") return 56;
  if (id.startsWith("util-")) return 56;
  if (id.startsWith("sel-")) return 38;
  if (id.startsWith("insulgard")) return 22;
  return 42;
}
