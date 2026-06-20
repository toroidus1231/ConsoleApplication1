// Checklist / PhysicalChecklist (Module 19, Contracts §6.4 "/checklist").
// GET /checklist/:id  +  POST /checklist/:id/:item_id (multipart with photo).
// Device is chosen by ID or by scanning a NETBOX:{device_id} QR string (§9.1).
// Works offline: submissions queue in IndexedDB and sync on reconnect (§6.5).
import { useEffect, useState } from "react";
import { useApi } from "../hooks/useApi.js";
import {
  submitChecklistItem,
  compressImage,
  parseDeviceQR,
  flushQueue,
  getQueued,
} from "../offline.js";
import {
  Card,
  StatusPill,
  Loading,
  ErrorState,
  EmptyState,
} from "../components/ui.jsx";

export default function Checklist() {
  const api = useApi();
  const [deviceId, setDeviceId] = useState("");
  const [activeDevice, setActiveDevice] = useState(null);
  const [device, setDevice] = useState(null);
  const [items, setItems] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [online, setOnline] = useState(navigator.onLine);
  const [queued, setQueued] = useState(0);
  const [qrText, setQrText] = useState("");

  // Track connectivity + flush the offline queue on reconnect (§6.5).
  useEffect(() => {
    const refreshQueue = () => getQueued().then((q) => setQueued(q.length)).catch(() => {});
    const goOnline = async () => {
      setOnline(true);
      await flushQueue();
      refreshQueue();
    };
    const goOffline = () => setOnline(false);
    window.addEventListener("online", goOnline);
    window.addEventListener("offline", goOffline);
    refreshQueue();
    return () => {
      window.removeEventListener("online", goOnline);
      window.removeEventListener("offline", goOffline);
    };
  }, []);

  const loadDevice = async (id) => {
    if (!id) return;
    setLoading(true);
    setError(null);
    setActiveDevice(id);
    try {
      // Checklist items for the device type.
      const cl = await api.get(`/checklist/${id}`);
      setItems(cl.items || []);
      // Best-effort device name for the header (non-fatal if it fails offline).
      try {
        const dev = await api.get(`/devices/${id}`);
        setDevice(dev.device || null);
      } catch {
        setDevice(null);
      }
    } catch (err) {
      setError(err);
      setItems(null);
    } finally {
      setLoading(false);
    }
  };

  const onScan = () => {
    const id = parseDeviceQR(qrText);
    if (id) {
      setDeviceId(id);
      loadDevice(id);
    } else {
      setError(new Error(`Not a NETBOX device QR string: "${qrText}"`));
    }
  };

  return (
    <div className="page">
      <header className="page-head">
        <h1>Physical Checklist</h1>
        <p className="page-sub">
          Per-device verification with photo evidence. Offline-capable for mechanical rooms.
        </p>
      </header>

      <div className={`offline-banner ${online ? "on" : "off"}`}>
        <span className={`live-dot ${online ? "on" : "off"}`} />
        {online ? "Online — submissions sent immediately." : "Offline — submissions queue locally and sync on reconnect."}
        {queued > 0 && <span className="queue-badge">{queued} queued</span>}
      </div>

      <Card title="Select device">
        <div className="form-row">
          <input
            className="input"
            placeholder="NetBox device ID"
            value={deviceId}
            onChange={(e) => setDeviceId(e.target.value)}
          />
          <button className="btn btn-primary" onClick={() => loadDevice(deviceId)} disabled={!deviceId}>
            Load checklist
          </button>
        </div>
        <div className="form-row">
          <input
            className="input grow"
            placeholder="Scan/paste QR payload e.g. NETBOX:dev-1423 (§9.1)"
            value={qrText}
            onChange={(e) => setQrText(e.target.value)}
          />
          <button className="btn btn-ghost" onClick={onScan} disabled={!qrText}>
            Use QR
          </button>
        </div>
      </Card>

      {loading && <Loading label="Loading checklist…" />}
      {error && <ErrorState error={error} onRetry={() => loadDevice(activeDevice)} />}

      {items && !loading && (
        <Card
          title={`Checklist — ${device?.name || activeDevice}`}
          actions={<span className="muted small">{items.filter((i) => i.completed).length}/{items.length} complete</span>}
        >
          {items.length === 0 ? (
            <EmptyState title="No checklist items" detail="This device type defines no checklist[] in its Config Context." />
          ) : (
            <div className="checklist">
              {items.map((item) => (
                <ChecklistItem
                  key={item.id}
                  deviceId={activeDevice}
                  item={item}
                  online={online}
                  onSubmitted={(updated) => {
                    setItems((prev) => prev.map((i) => (i.id === item.id ? { ...i, ...updated } : i)));
                    getQueued().then((q) => setQueued(q.length)).catch(() => {});
                  }}
                />
              ))}
            </div>
          )}
        </Card>
      )}
    </div>
  );
}

function ChecklistItem({ deviceId, item, online, onSubmitted }) {
  const [completed, setCompleted] = useState(Boolean(item.completed));
  const [notes, setNotes] = useState("");
  const [photo, setPhoto] = useState(null);
  const [photoName, setPhotoName] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState(null);
  const [err, setErr] = useState(null);

  const requiresPhoto = item.requires_photo || item.required_photo;
  const blockedOnPhoto = requiresPhoto && completed && !photo;

  const onPhoto = async (e) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setPhotoName(file.name);
    const compressed = await compressImage(file, 1024, 0.8);
    setPhoto(compressed);
  };

  const submit = async () => {
    setBusy(true);
    setErr(null);
    setMsg(null);
    try {
      const { mode, result } = await submitChecklistItem({
        device_id: deviceId,
        item_id: item.id,
        completed,
        notes,
        photoBlob: photo,
        client_ts: Date.now(),
      });
      if (mode === "online") {
        setMsg(`Saved${result?.attestation_hash ? ` · attested` : ""}`);
        onSubmitted(result?.item || { completed });
      } else {
        setMsg("Queued offline — will sync when online.");
        onSubmitted({ completed });
      }
    } catch (e2) {
      setErr(e2.message || "Submit failed");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className={`check-item ${completed ? "done" : ""}`}>
      <div className="check-main">
        <label className="check-toggle">
          <input
            type="checkbox"
            checked={completed}
            onChange={(e) => setCompleted(e.target.checked)}
          />
          <span className="check-desc">{item.description}</span>
        </label>
        <div className="check-meta">
          <StatusPill value={item.category} />
          {requiresPhoto && <span className="chip">photo required</span>}
          {item.acceptance_criteria && (
            <span className="muted small">{item.acceptance_criteria}</span>
          )}
        </div>
      </div>

      <div className="check-controls">
        <input
          className="input"
          placeholder="Notes"
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
        />
        <label className="btn btn-ghost btn-sm file-btn">
          {photoName ? "Change photo" : "Add photo"}
          <input type="file" accept="image/*" capture="environment" hidden onChange={onPhoto} />
        </label>
        {photoName && <span className="muted small ellipsis">{photoName}</span>}
        <button
          className="btn btn-primary btn-sm"
          onClick={submit}
          disabled={busy || blockedOnPhoto}
          title={blockedOnPhoto ? "Photo required to complete this item" : undefined}
        >
          {busy ? "Saving…" : online ? "Submit" : "Queue"}
        </button>
      </div>

      {blockedOnPhoto && <div className="inline-warn">Photo evidence required to mark complete.</div>}
      {msg && <div className="inline-ok">{msg}</div>}
      {err && <div className="inline-error">{err}</div>}
    </div>
  );
}
