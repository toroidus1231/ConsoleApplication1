// LocalStorage-backed build persistence. A "real" build means the user has
// committed a site + phase + equipment mix, and the resulting payload is
// written to disk-equivalent (localStorage) with a build id and timestamp.
//
// Dashboard / Control Room views are gated until a real build exists, so
// the user can't manipulate a non-existent fleet.

const KEY = "forge:build:v1";
const HISTORY_KEY = "forge:builds:v1";

export function saveBuild(build) {
  const record = {
    id: "build_" + Date.now().toString(36),
    createdAt: new Date().toISOString(),
    site: build.site.id,
    phase: build.phase.id,
    mix: build.mix,
    counts: build.counts,
  };
  localStorage.setItem(KEY, JSON.stringify(record));
  // Append to history (trimmed to 16).
  let h = [];
  try { h = JSON.parse(localStorage.getItem(HISTORY_KEY) || "[]"); } catch {}
  h.unshift(record);
  if (h.length > 16) h = h.slice(0, 16);
  localStorage.setItem(HISTORY_KEY, JSON.stringify(h));
  return record;
}

export function loadBuild() {
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return null;
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

export function clearBuild() {
  localStorage.removeItem(KEY);
}

export function buildHistory() {
  try { return JSON.parse(localStorage.getItem(HISTORY_KEY) || "[]"); }
  catch { return []; }
}
