// Attestation / AttestationViewer (Module 20, Contracts §6.4 "/attestation").
// GET /attestation/chain  +  GET /attestation/verify/:hash
// +  GET /attestation/certificate  +  GET /attestation/:hash
// +  GET /attestation/export/:test_id (download).
import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useApi } from "../hooks/useApi.js";
import {
  Card,
  StatusPill,
  DataTable,
  Loading,
  ErrorState,
  EmptyState,
  fmtTime,
  fmtNumber,
} from "../components/ui.jsx";

const PAGE = 50;

export default function Attestation() {
  const api = useApi();
  const [params, setParams] = useSearchParams();
  const [fromSeq, setFromSeq] = useState(0);
  const [chain, setChain] = useState(null);
  const [chainValid, setChainValid] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const [certificate, setCertificate] = useState(null);

  const [hashInput, setHashInput] = useState(params.get("hash") || "");
  const [verifyResult, setVerifyResult] = useState(null);
  const [record, setRecord] = useState(null);
  const [verifyBusy, setVerifyBusy] = useState(false);
  const [verifyError, setVerifyError] = useState(null);

  const [exportTestId, setExportTestId] = useState("");
  const [exportBusy, setExportBusy] = useState(false);

  const loadChain = async (start = fromSeq) => {
    setLoading(true);
    setError(null);
    try {
      const data = await api.get(`/attestation/chain?from_sequence=${start}&count=${PAGE}`);
      setChain(data.records || []);
      setChainValid(data.chain_valid);
    } catch (err) {
      setError(err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadChain(0);
    api.get("/attestation/certificate").then(setCertificate).catch(() => setCertificate(null));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Auto-verify a hash arriving via ?hash= (from punch list / test detail).
  useEffect(() => {
    const h = params.get("hash");
    if (h) {
      setHashInput(h);
      verifyHash(h);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const verifyHash = async (h) => {
    const hash = (h || hashInput).trim();
    if (!hash) return;
    setVerifyBusy(true);
    setVerifyError(null);
    setVerifyResult(null);
    setRecord(null);
    setParams(hash ? { hash } : {});
    try {
      const [v, r] = await Promise.allSettled([
        api.get(`/attestation/verify/${hash}`),
        api.get(`/attestation/${hash}`),
      ]);
      if (v.status === "fulfilled") setVerifyResult(v.value);
      if (r.status === "fulfilled") setRecord(r.value.record || r.value);
      if (v.status === "rejected" && r.status === "rejected") {
        setVerifyError(v.reason?.message || "Hash not found");
      }
    } finally {
      setVerifyBusy(false);
    }
  };

  const exportBundle = async () => {
    if (!exportTestId.trim()) return;
    setExportBusy(true);
    try {
      await api.download(`/attestation/export/${exportTestId.trim()}`, `attestation-${exportTestId.trim()}.json`);
    } catch (err) {
      setVerifyError(err.message);
    } finally {
      setExportBusy(false);
    }
  };

  return (
    <div className="page">
      <header className="page-head">
        <h1>Attestation</h1>
        <p className="page-sub">
          Tamper-evident chain backed by WORM storage. One chain per facility (§5.4).
        </p>
      </header>

      {/* --- Facility certificate --- */}
      <Card title="Facility Certificate">
        {!certificate ? (
          <EmptyState title="Certificate unavailable" detail="GET /attestation/certificate returned no data." />
        ) : (
          <div className="cert">
            <div className="cert-grid">
              <div><span>Facility</span><b>{certificate.facility}</b></div>
              <div><span>Chain length</span><b>{certificate.chain_length}</b></div>
              <div>
                <span>Chain valid</span>
                <StatusPill value={certificate.chain_valid ? "ok" : "error"} label={certificate.chain_valid ? "valid" : "BROKEN"} />
              </div>
              <div><span>Hash algorithm</span><b>{certificate.hash_algorithm || "SHA-256"}</b></div>
              <div><span>First record</span><b className="mono small">{shortHash(certificate.first_record)}</b></div>
              <div><span>Last record</span><b className="mono small">{shortHash(certificate.last_record)}</b></div>
            </div>
          </div>
        )}
      </Card>

      {/* --- Verify a record / walk to a hash --- */}
      <Card title="Verify a record">
        <div className="form-row">
          <input
            className="input grow mono"
            placeholder="SHA-256 hash"
            value={hashInput}
            onChange={(e) => setHashInput(e.target.value)}
          />
          <button className="btn btn-primary" onClick={() => verifyHash()} disabled={verifyBusy || !hashInput.trim()}>
            {verifyBusy ? "Verifying…" : "Verify"}
          </button>
        </div>
        {verifyError && <ErrorState error={verifyError} />}
        {verifyResult && (
          <div className="verify-result">
            <div className="kv">
              <span>Integrity</span>
              <StatusPill value={verifyResult.valid ? "ok" : "error"} label={verifyResult.valid ? "valid" : "TAMPERED"} />
            </div>
            <div className="kv"><span>Position</span><b>{verifyResult.chain_position} / {verifyResult.chain_length}</b></div>
            {Array.isArray(verifyResult.breaks) && verifyResult.breaks.length > 0 && (
              <div className="inline-error">Chain breaks at: {verifyResult.breaks.join(", ")}</div>
            )}
          </div>
        )}
        {record && (
          <div className="record-detail">
            <div className="kv"><span>Sequence</span><b>{record.sequence}</b></div>
            <div className="kv"><span>Device</span><b>{record.device_id}</b></div>
            <div className="kv"><span>Measurement</span><b>{record.measurement} = {fmtNumber(record.value)}</b></div>
            <div className="kv"><span>Timestamp</span><b>{record.timestamp_ns ? fmtTime(new Date(record.timestamp_ns / 1e6).toISOString()) : "—"}</b></div>
            {record.test_id && <div className="kv"><span>Test</span><b className="mono small">{record.test_id}</b></div>}
            <div className="kv col"><span>Previous hash</span><b className="mono small">{record.previous_hash}</b></div>
          </div>
        )}
      </Card>

      {/* --- Export bundle for a test --- */}
      <Card title="Export signed bundle">
        <div className="form-row">
          <input
            className="input grow"
            placeholder="test_id"
            value={exportTestId}
            onChange={(e) => setExportTestId(e.target.value)}
          />
          <button className="btn btn-ghost" onClick={exportBundle} disabled={exportBusy || !exportTestId.trim()}>
            {exportBusy ? "Exporting…" : "Download bundle"}
          </button>
        </div>
        <p className="muted small">Downloads all attested records for a test as a JSON bundle (§4.5).</p>
      </Card>

      {/* --- Chain walk --- */}
      <Card
        title="Chain"
        actions={
          chainValid != null && (
            <StatusPill value={chainValid ? "ok" : "error"} label={chainValid ? "chain valid" : "CHAIN BROKEN"} />
          )
        }
      >
        <div className="form-row tight">
          <label className="muted small">From sequence</label>
          <input
            className="input narrow"
            type="number"
            min="0"
            value={fromSeq}
            onChange={(e) => setFromSeq(Number(e.target.value))}
          />
          <button className="btn btn-ghost btn-sm" onClick={() => loadChain(Math.max(0, fromSeq - PAGE))}>
            ◀ Prev
          </button>
          <button className="btn btn-ghost btn-sm" onClick={() => loadChain(fromSeq)}>
            Load
          </button>
          <button className="btn btn-ghost btn-sm" onClick={() => { const n = fromSeq + PAGE; setFromSeq(n); loadChain(n); }}>
            Next ▶
          </button>
        </div>

        {loading && <Loading label="Walking chain…" />}
        {error && <ErrorState error={error} onRetry={() => loadChain(fromSeq)} />}
        {!loading && !error && (
          <DataTable
            columns={[
              { key: "sequence", header: "Seq", sortable: true, width: "70px" },
              { key: "device_id", header: "Device" },
              { key: "measurement", header: "Measurement" },
              { key: "value", header: "Value", render: (r) => fmtNumber(r.value) },
              {
                key: "hash",
                header: "Hash",
                render: (r) => (
                  <button className="link mono small" onClick={() => { setHashInput(r.hash); verifyHash(r.hash); }}>
                    {shortHash(r.hash)}
                  </button>
                ),
              },
              { key: "test_id", header: "Test", render: (r) => (r.test_id ? <span className="mono small">{shortHash(r.test_id)}</span> : "—") },
            ]}
            rows={chain}
            rowKey={(r) => r.hash || r.sequence}
            empty={<EmptyState title="No records" detail="The attestation chain is empty for this range." />}
          />
        )}
      </Card>
    </div>
  );
}

function shortHash(h) {
  if (!h) return "—";
  const s = String(h);
  return s.length > 16 ? `${s.slice(0, 10)}…${s.slice(-6)}` : s;
}
