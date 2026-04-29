import React, { useEffect, useState } from "react";
import { api } from "../hooks/useApi";

// Module 19: Physical Verification Checklist.
//
// Designed for a tablet walking through a mech room with patchy WiFi.
// Submissions queue in IndexedDB when offline and flush when connectivity
// returns. Photo upload uses multipart; image is downscaled to 1024px max
// before sending to keep sync bandwidth reasonable.

const DB_NAME = "checklist-queue";
const STORE = "submissions";

function openDB() {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, 1);
    req.onupgradeneeded = () => req.result.createObjectStore(STORE, { autoIncrement: true });
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

async function enqueue(payload) {
  const db = await openDB();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE, "readwrite");
    const req = tx.objectStore(STORE).add(payload);
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

async function drain() {
  const db = await openDB();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE, "readwrite");
    const store = tx.objectStore(STORE);
    const req = store.getAll();
    req.onsuccess = async () => {
      for (const item of req.result) {
        try {
          await api.postForm(`/checklist/${item.deviceId}/${item.itemId}`, item.formData);
        } catch (_) { return; /* try again later */ }
      }
      store.clear();
      resolve();
    };
    req.onerror = () => reject(req.error);
  });
}

async function compressImage(file) {
  if (!file) return null;
  const img = await new Promise((resolve, reject) => {
    const i = new Image();
    i.onload = () => resolve(i);
    i.onerror = reject;
    i.src = URL.createObjectURL(file);
  });
  const max = 1024;
  const ratio = Math.min(max / img.width, max / img.height, 1);
  const canvas = document.createElement("canvas");
  canvas.width = img.width * ratio;
  canvas.height = img.height * ratio;
  canvas.getContext("2d").drawImage(img, 0, 0, canvas.width, canvas.height);
  return new Promise((res) => canvas.toBlob(res, "image/jpeg", 0.8));
}

export default function Checklist() {
  const [deviceId, setDeviceId] = useState("");
  const [items, setItems] = useState([]);

  useEffect(() => {
    const flush = () => navigator.onLine && drain().catch(() => {});
    window.addEventListener("online", flush);
    flush();
    return () => window.removeEventListener("online", flush);
  }, []);

  const load = async () => {
    if (!deviceId) return;
    try {
      const r = await api.get(`/checklist/${encodeURIComponent(deviceId)}`);
      setItems(r.items || []);
    } catch (e) { setItems([]); }
  };

  const submit = async (item, photoFile, notes) => {
    const formData = new FormData();
    formData.append("completed", "true");
    formData.append("notes", notes || "");
    if (photoFile) {
      const blob = await compressImage(photoFile);
      formData.append("photo", blob, "photo.jpg");
    }
    if (navigator.onLine) {
      await api.postForm(`/checklist/${deviceId}/${item.id}`, formData);
    } else {
      await enqueue({ deviceId, itemId: item.id, formData });
    }
    setItems(items.map((i) => (i.id === item.id ? { ...i, completed: true } : i)));
  };

  return (
    <>
      <h1>Physical Verification Checklist</h1>
      <p>Status: {navigator.onLine ? "🟢 online" : "🔴 offline (queued)"}</p>
      <div className="card">
        <input
          placeholder="device_id (or scan QR NETBOX:dev-xxx)"
          value={deviceId}
          onChange={(e) => setDeviceId(e.target.value.replace(/^NETBOX:/, ""))}
        />
        <button onClick={load}>Load</button>
      </div>

      {items.map((i) => (
        <ChecklistItem key={i.id} item={i} onSubmit={submit} />
      ))}
    </>
  );
}

function ChecklistItem({ item, onSubmit }) {
  const [photo, setPhoto] = useState(null);
  const [notes, setNotes] = useState("");
  return (
    <div className="card">
      <div><strong>{item.description}</strong> ({item.category})</div>
      <div style={{ fontSize: "0.85em", color: "#9aa0a6" }}>
        Pass criterion: {item.acceptance_criteria}
      </div>
      {item.requires_photo && (
        <input type="file" accept="image/*" capture="environment"
               onChange={(e) => setPhoto(e.target.files?.[0] || null)} />
      )}
      <input placeholder="notes" value={notes} onChange={(e) => setNotes(e.target.value)} />
      <button disabled={item.completed} onClick={() => onSubmit(item, photo, notes)}>
        {item.completed ? "✓ Done" : "Submit"}
      </button>
    </div>
  );
}
