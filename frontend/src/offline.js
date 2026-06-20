// offline.js — IndexedDB-backed queue and image compression for the offline
// physical checklist (Contracts §6.5, Module 19).
//
//   * Checklist submissions made while offline are stored in IndexedDB.
//   * compressImage() downscales photos to 1024px max dimension at JPEG q80
//     before queueing/upload to minimize sync bandwidth.
//   * flushQueue() drains the queue to the API when connectivity returns;
//     conflict resolution keeps the earliest client timestamp.
import { getApiKey, API_BASE } from "./hooks/useApi.js";

const DB_NAME = "cx-offline";
const DB_VERSION = 1;
const STORE = "checklist_queue";

function openDB() {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, DB_VERSION);
    req.onupgradeneeded = () => {
      const db = req.result;
      if (!db.objectStoreNames.contains(STORE)) {
        const store = db.createObjectStore(STORE, { keyPath: "key" });
        store.createIndex("by_time", "client_ts");
      }
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

function tx(db, mode) {
  return db.transaction(STORE, mode).objectStore(STORE);
}

// Queue a submission. key = `${device_id}:${item_id}` so a later submission of
// the same item overwrites an earlier queued one (last edit wins locally; the
// flush step still resolves cross-client conflicts by earliest timestamp).
export async function queueSubmission(entry) {
  const db = await openDB();
  return new Promise((resolve, reject) => {
    const record = {
      key: `${entry.device_id}:${entry.item_id}`,
      client_ts: entry.client_ts || Date.now(),
      ...entry,
    };
    const req = tx(db, "readwrite").put(record);
    req.onsuccess = () => resolve(record);
    req.onerror = () => reject(req.error);
  });
}

export async function getQueued() {
  const db = await openDB();
  return new Promise((resolve, reject) => {
    const req = tx(db, "readonly").getAll();
    req.onsuccess = () => resolve(req.result || []);
    req.onerror = () => reject(req.error);
  });
}

export async function removeQueued(key) {
  const db = await openDB();
  return new Promise((resolve, reject) => {
    const req = tx(db, "readwrite").delete(key);
    req.onsuccess = () => resolve();
    req.onerror = () => reject(req.error);
  });
}

// POST one queued entry to the checklist API as multipart/form-data.
async function sendEntry(entry) {
  const form = new FormData();
  form.append("completed", String(entry.completed));
  form.append("notes", entry.notes || "");
  // Earliest client timestamp travels with the submission so the backend can
  // apply the §6.5 conflict rule (keep earlier timestamp).
  form.append("client_timestamp", new Date(entry.client_ts).toISOString());
  if (entry.photoBlob) {
    form.append("photo", entry.photoBlob, `${entry.item_id}.jpg`);
  }
  const resp = await fetch(`${API_BASE}/checklist/${entry.device_id}/${entry.item_id}`, {
    method: "POST",
    headers: { "X-API-Key": getApiKey() },
    body: form,
  });
  if (!resp.ok) throw new Error(`${resp.status}`);
  return resp.json();
}

// Drain the queue. Returns { synced, failed }. Safe to call repeatedly.
export async function flushQueue() {
  if (!navigator.onLine) return { synced: 0, failed: 0 };
  const entries = await getQueued();
  // Oldest first so earliest-timestamp wins if duplicates exist server-side.
  entries.sort((a, b) => a.client_ts - b.client_ts);
  let synced = 0;
  let failed = 0;
  for (const entry of entries) {
    try {
      await sendEntry(entry);
      await removeQueued(entry.key);
      synced += 1;
    } catch {
      failed += 1; // leave in queue, retry on next flush
    }
  }
  return { synced, failed };
}

// Submit immediately if online, otherwise queue. Returns
// { mode: "online"|"queued", result? }.
export async function submitChecklistItem(entry) {
  if (navigator.onLine) {
    try {
      const result = await sendEntry(entry);
      return { mode: "online", result };
    } catch {
      // Network blip mid-request — fall back to the offline queue.
      await queueSubmission(entry);
      return { mode: "queued" };
    }
  }
  await queueSubmission(entry);
  return { mode: "queued" };
}

// Downscale an image File/Blob to maxDim and JPEG quality (§6.5: 1024px, q80).
export async function compressImage(file, maxDim = 1024, quality = 0.8) {
  if (!file) return null;
  const bitmap = await createImageBitmap(file).catch(() => null);
  if (!bitmap) return file; // unsupported; upload original
  let { width, height } = bitmap;
  if (width > maxDim || height > maxDim) {
    const scale = maxDim / Math.max(width, height);
    width = Math.round(width * scale);
    height = Math.round(height * scale);
  }
  const canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = height;
  const ctx = canvas.getContext("2d");
  ctx.drawImage(bitmap, 0, 0, width, height);
  return new Promise((resolve) => {
    canvas.toBlob((blob) => resolve(blob || file), "image/jpeg", quality);
  });
}

// Parse a scanned QR payload of the form NETBOX:{device_id} (§9.1).
export function parseDeviceQR(text) {
  if (!text) return null;
  const trimmed = text.trim();
  const PREFIX = "NETBOX:";
  if (trimmed.toUpperCase().startsWith(PREFIX)) {
    return trimmed.slice(PREFIX.length);
  }
  return null;
}
