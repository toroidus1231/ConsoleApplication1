// Reports (Contracts §6.4 "/reports").
// POST /reports/generate  +  GET /reports/:id (PDF download).
// Also surfaces reconciliation (§4.7) since it feeds punch items into reports.
import { useEffect, useRef, useState } from "react";
import { useApi } from "../hooks/useApi.js";
import {
  Card,
  StatusPill,
  Loading,
  EmptyState,
  ErrorState,
} from "../components/ui.jsx";

const SECTIONS = [
  { key: "discovery", label: "Discovery" },
  { key: "tests", label: "Active tests" },
  { key: "punchlist", label: "Punch list" },
  { key: "attestation", label: "Attestation" },
];

export default function Reports() {
  const api = useApi();

  // --- report generation ---
  const [selected, setSelected] = useState(SECTIONS.map((s) => s.key));
  const [reportId, setReportId] = useState(null);
  const [reportStatus, setReportStatus] = useState(null);
  const [genError, setGenError] = useState(null);
  const [downloading, setDownloading] = useState(false);
  const pollRef = useRef(null);

  // --- reconciliation ---
  const [reconJob, setReconJob] = useState(null);
  const [reconStatus, setReconStatus] = useState(null);
  const [reconError, setReconError] = useState(null);
  const reconRef = useRef(null);

  const toggle = (key) =>
    setSelected((prev) => (prev.includes(key) ? prev.filter((k) => k !== key) : [...prev, key]));

  const generate = async () => {
    setGenError(null);
    setReportStatus("generating");
    setReportId(null);
    try {
      const resp = await api.post("/reports/generate", { include_sections: selected });
      setReportId(resp.report_id);
      setReportStatus(resp.status || "generating");
    } catch (err) {
      setGenError(err.message);
      setReportStatus(null);
    }
  };

  // Poll report status until ready, then offer download.
  useEffect(() => {
    if (!reportId || reportStatus === "ready" || reportStatus === "complete") return undefined;
    let active = true;
    const tick = async () => {
      try {
        // Reuse the status endpoint shape if the backend exposes one; otherwise
        // a HEAD-like GET of /reports/:id would download. We poll generate state
        // via a lightweight status convention and stop after it leaves
        // "generating".
        const st = await api.get(`/reports/status/${reportId}`).catch(() => null);
        if (!active) return;
        if (st && st.status) {
          setReportStatus(st.status);
          if (st.status !== "generating") clearInterval(pollRef.current);
        } else {
          // No status endpoint — assume it becomes ready shortly.
          setReportStatus("ready");
          clearInterval(pollRef.current);
        }
      } catch {
        clearInterval(pollRef.current);
      }
    };
    pollRef.current = setInterval(tick, 2000);
    tick();
    return () => {
      active = false;
      clearInterval(pollRef.current);
    };
  }, [reportId, reportStatus, api]);

  const downloadReport = async () => {
    if (!reportId) return;
    setDownloading(true);
    try {
      await api.download(`/reports/${reportId}`, `commissioning-report-${reportId}.pdf`);
    } catch (err) {
      setGenError(err.message);
    } finally {
      setDownloading(false);
    }
  };

  const runReconciliation = async () => {
    setReconError(null);
    setReconStatus("running");
    try {
      const resp = await api.post("/reconciliation/run", {});
      setReconJob(resp.job_id);
      setReconStatus(resp.status || "running");
    } catch (err) {
      setReconError(err.message);
      setReconStatus(null);
    }
  };

  useEffect(() => {
    if (!reconJob || reconStatus !== "running") return undefined;
    let active = true;
    const tick = async () => {
      try {
        const st = await api.get(`/reconciliation/status/${reconJob}`);
        if (!active) return;
        setReconStatus(st.status);
        if (st.status !== "running") {
          setReconStatus(st);
          clearInterval(reconRef.current);
        }
      } catch (err) {
        if (active) setReconError(err.message);
        clearInterval(reconRef.current);
      }
    };
    reconRef.current = setInterval(tick, 2000);
    tick();
    return () => {
      active = false;
      clearInterval(reconRef.current);
    };
  }, [reconJob, reconStatus, api]);

  const reportReady =
    reportId && reportStatus && reportStatus !== "generating";

  return (
    <div className="page">
      <header className="page-head">
        <h1>Reports</h1>
        <p className="page-sub">Generate the commissioning report PDF and run design-vs-actual reconciliation.</p>
      </header>

      <Card title="Reconciliation">
        <p className="muted">
          Compare the NetBox design against discovered + tested actuals; mismatches become punch items (§5.2).
        </p>
        <div className="form-row">
          <button className="btn btn-primary" onClick={runReconciliation} disabled={reconStatus === "running"}>
            {reconStatus === "running" ? "Running…" : "Run reconciliation"}
          </button>
          {reconStatus === "running" && <Loading label="Reconciling…" />}
        </div>
        {reconError && <ErrorState error={reconError} />}
        {reconStatus && typeof reconStatus === "object" && (
          <div className="banner banner-ok">
            Reconciliation {reconStatus.status} — {reconStatus.punch_items_generated ?? 0} punch item(s) generated.
          </div>
        )}
      </Card>

      <Card title="Generate report">
        <div className="check-row">
          {SECTIONS.map((s) => (
            <label key={s.key} className="check-pill">
              <input
                type="checkbox"
                checked={selected.includes(s.key)}
                onChange={() => toggle(s.key)}
              />
              {s.label}
            </label>
          ))}
        </div>
        <div className="form-row">
          <button className="btn btn-primary" onClick={generate} disabled={selected.length === 0 || reportStatus === "generating"}>
            {reportStatus === "generating" ? "Generating…" : "Generate PDF"}
          </button>
          {reportStatus === "generating" && <Loading label="Building report…" />}
        </div>
        {genError && <ErrorState error={genError} />}

        {reportId && (
          <div className="report-result">
            <div className="kv">
              <span>Report</span>
              <b className="mono small">{reportId}</b>
              <StatusPill value={reportReady ? "ok" : "running"} label={reportReady ? "ready" : "generating"} />
            </div>
            <button className="btn btn-ghost" onClick={downloadReport} disabled={!reportReady || downloading}>
              {downloading ? "Downloading…" : "Download PDF"}
            </button>
          </div>
        )}
        {!reportId && !genError && reportStatus !== "generating" && (
          <EmptyState title="No report yet" detail="Pick sections and generate a PDF." />
        )}
      </Card>
    </div>
  );
}
