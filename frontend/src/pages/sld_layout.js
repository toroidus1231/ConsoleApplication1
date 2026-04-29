// Layout coordinates for the single-line diagram. Pure data — keeps the
// component logic in SingleLine.jsx focused on rendering.

// Canvas grid: 1700 × 1100 px. Devices addressed by id; wires by [from, to].

export const SLD_W = 1700;
export const SLD_H = 1100;

// (x, y) is the TOP-LEFT of each card.
export const POS = {
  // Sources
  "util-A":         { x:  60, y:  30 },
  "util-B":         { x: 280, y:  30 },
  "gen-bus":        { x: 980, y:  30 },

  // MV main breakers
  "mv-main-A":      { x:  60, y: 120 },
  "mv-main-B":      { x: 280, y: 120 },

  // SEL relays alongside MV mains
  "sel-mv-main-A":  { x: 220, y: 124 },
  "sel-mv-main-B":  { x: 440, y: 124 },

  // MV buses (horizontal bars — width is computed at render time)
  "mv-bus-A":       { x:  60, y: 200, w: 200 },
  "mv-bus-B":       { x: 280, y: 200, w: 200 },

  // MV tie between buses
  "mv-tie":         { x: 192, y: 240 },
  "sel-mv-tie":     { x: 200, y: 280 },

  // PD monitors (decorative chips)
  "insulgard-mv-A": { x:  60, y: 240 },
  "insulgard-mv-B": { x: 460, y: 240 },

  // Transformers
  "xfmr-A1":        { x:  20, y: 340 },
  "xfmr-A2":        { x: 170, y: 340 },
  "xfmr-B1":        { x: 320, y: 340 },
  "xfmr-B2":        { x: 470, y: 340 },

  // MTZ incoming breakers
  "mtz-inc-A1":     { x:  20, y: 480 },
  "mtz-inc-A2":     { x: 170, y: 480 },
  "mtz-inc-B1":     { x: 320, y: 480 },
  "mtz-inc-B2":     { x: 470, y: 480 },

  // LV buses
  "lv-bus-A1":      { x:  20, y: 560, w: 130 },
  "lv-bus-A2":      { x: 170, y: 560, w: 130 },
  "lv-bus-B1":      { x: 320, y: 560, w: 130 },
  "lv-bus-B2":      { x: 470, y: 560, w: 130 },

  // ATS / UPS chain
  "ats-1":          { x: 660, y: 600 },
  "ats-2":          { x: 800, y: 600 },
  "ups-A":          { x: 660, y: 720 },
  "ups-B":          { x: 800, y: 720 },

  // Generator panels
  "gen-1":          { x: 980, y: 110 },
  "gen-2":          { x: 980, y: 320 },

  // Legend
  "legend":         { x: 1280, y:  30 },
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
  if (id.startsWith("mtz-")) return 130;
  if (id.startsWith("mv-main-")) return 130;
  if (id === "mv-tie") return 130;
  if (id.startsWith("ats-")) return 124;
  if (id.startsWith("ups-")) return 124;
  if (id.startsWith("gen-") && id !== "gen-bus") return 240;
  if (id === "gen-bus" || id.startsWith("util-")) return 160;
  if (id.startsWith("sel-")) return 88;
  if (id.startsWith("insulgard")) return 130;
  return 130;
}

function cardHeight(id) {
  if (id.startsWith("xfmr-")) return 110;
  if (id.startsWith("mv-bus") || id.startsWith("lv-bus")) return 32;
  if (id.startsWith("ats-")) return 70;
  if (id.startsWith("ups-")) return 90;
  if (id.startsWith("gen-") && id !== "gen-bus") return 180;
  if (id.startsWith("sel-")) return 38;
  if (id.startsWith("insulgard")) return 22;
  return 42;
}
