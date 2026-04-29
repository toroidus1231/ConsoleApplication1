import React, { useEffect, useState } from "react";
import { api } from "../hooks/useApi";

const SEVERITIES = ["critical", "major", "minor", "info"];
const CATEGORIES = ["identity", "firmware", "network", "power", "cooling", "security", "safety", "test_failure", "physical"];

export default function PunchList() {
  const [items, setItems] = useState([]);
  const [filterSev, setFilterSev] = useState("");
  const [filterCat, setFilterCat] = useState("");
  const [filterStatus, setFilterStatus] = useState("open");
  const [evidence, setEvidence] = useState(null);

  const reload = () => {
    const params = new URLSearchParams();
    if (filterSev) params.set("severity", filterSev);
    if (filterCat) params.set("category", filterCat);
    if (filterStatus) params.set("status", filterStatus);
    const q = params.toString();
    api.get(`/punchlist${q ? "?" + q : ""}`).then((d) => setItems(d.items || [])).catch(() => setItems([]));
  };
  useEffect(reload, [filterSev, filterCat, filterStatus]);

  const resolve = async (id) => {
    await api.patch(`/punchlist/${id}`, { status: "resolved", resolved_by: "engineer" });
    setItems(items.filter((i) => i.id !== id));
  };

  const showEvidence = async (item) => {
    if (!item.evidence_hash) return setEvidence({ item, error: "No evidence_hash on this item." });
    try {
      const r = await api.get(`/attestation/${item.evidence_hash}`);
      setEvidence({ item, record: r.record });
    } catch (e) {
      setEvidence({ item, error: String(e) });
    }
  };

  return (
    <>
      <div className="page-header">
        <h1>Punch List</h1>
        <span className="crumb">{items.length} items</span>
      </div>

      <div className="card">
        <div className="row">
          <select value={filterSev} onChange={(e) => setFilterSev(e.target.value)}>
            <option value="">All severities</option>
            {SEVERITIES.map((s) => <option key={s} value={s}>{s}</option>)}
          </select>
          <select value={filterCat} onChange={(e) => setFilterCat(e.target.value)}>
            <option value="">All categories</option>
            {CATEGORIES.map((c) => <option key={c} value={c}>{c}</option>)}
          </select>
          <select value={filterStatus} onChange={(e) => setFilterStatus(e.target.value)}>
            <option value="open">Open</option>
            <option value="acknowledged">Acknowledged</option>
            <option value="resolved">Resolved</option>
            <option value="">All</option>
          </select>
        </div>
      </div>

      <div className="card" style={{ padding: 0 }}>
        <table>
          <thead>
            <tr>
              <th>Severity</th><th>Category</th><th>Device</th><th>Rack</th>
              <th>Expected</th><th>Actual</th><th>Remediation</th><th>Evidence</th><th></th>
            </tr>
          </thead>
          <tbody>
            {items.map((i) => (
              <tr key={i.id}>
                <td><span className={`badge badge-${i.severity}`}>{i.severity}</span></td>
                <td>{i.category}</td>
                <td>{i.device_name || i.device_id}</td>
                <td>{i.rack || "—"}</td>
                <td className="mono">{i.expected}</td>
                <td className="mono">{i.actual}</td>
                <td style={{ maxWidth: 320 }}>{i.remediation}</td>
                <td>
                  {i.evidence_hash ? (
                    <button className="ghost" onClick={() => showEvidence(i)}>Open</button>
                  ) : <span style={{ color: "var(--text-faint)" }}>—</span>}
                </td>
                <td>
                  {i.status === "open" && (
                    <button className="ghost" onClick={() => resolve(i.id)}>Resolve</button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {evidence && (
        <EvidenceModal evidence={evidence} onClose={() => setEvidence(null)} />
      )}
    </>
  );
}

function EvidenceModal({ evidence, onClose }) {
  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-h">
          <h2>Attestation Evidence</h2>
          <button className="close" onClick={onClose}>×</button>
        </div>
        <div className="modal-body">
          <div className="kv">
            <div className="k">Item</div>
            <div className="v">{evidence.item.device_name} · {evidence.item.category}</div>
            <div className="k">Severity</div>
            <div className="v">
              <span className={`badge badge-${evidence.item.severity}`}>{evidence.item.severity}</span>
            </div>
            <div className="k">Hash</div>
            <div className="v mono" style={{ wordBreak: "break-all" }}>{evidence.item.evidence_hash}</div>
          </div>
          <hr style={{ border: 0, borderTop: "1px solid var(--border)", margin: "14px 0" }} />
          {evidence.error && <div className="badge badge-major">{evidence.error}</div>}
          {evidence.record && (
            <>
              <div className="kv">
                <div className="k">Sequence</div>
                <div className="v">{evidence.record.sequence}</div>
                <div className="k">Device</div>
                <div className="v">{evidence.record.device_id}</div>
                <div className="k">Measurement</div>
                <div className="v">{evidence.record.measurement} = {evidence.record.value}</div>
                <div className="k">Protocol</div>
                <div className="v">{evidence.record.protocol} from <span className="mono">{evidence.record.source_ip}</span></div>
                <div className="k">Worker</div>
                <div className="v mono">{evidence.record.worker_id}</div>
                <div className="k">Previous hash</div>
                <div className="v mono" style={{ wordBreak: "break-all" }}>{evidence.record.previous_hash}</div>
                <div className="k">Raw bytes</div>
                <div className="v mono">{evidence.record.raw_bytes}</div>
              </div>
              <h3 style={{ fontSize: 12, marginTop: 16, color: "var(--text-faint)" }}>Full record</h3>
              <pre>{JSON.stringify(evidence.record, null, 2)}</pre>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
