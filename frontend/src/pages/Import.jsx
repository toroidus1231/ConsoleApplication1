// Import / ImportPage (Contracts §6.4 "/import").
// BIM:    POST /bim/import (.ifc) -> GET /bim/status/:job -> GET /bim/preview/:job -> POST /bim/commit/:job
// Config: POST /config/generate (PDF + slug) -> GET /config/result/:job -> POST /config/commit/:job
import { useEffect, useRef, useState } from "react";
import { useApi } from "../hooks/useApi.js";
import {
  Card,
  StatusPill,
  DataTable,
  Loading,
  ErrorState,
  EmptyState,
} from "../components/ui.jsx";

export default function Import() {
  return (
    <div className="page">
      <header className="page-head">
        <h1>Import</h1>
        <p className="page-sub">
          Bring the design into the platform: BIM/IFC devices and manufacturer-PDF Config Contexts.
        </p>
      </header>
      <div className="grid-2">
        <BimImport />
        <ConfigGenerate />
      </div>
    </div>
  );
}

// ---- BIM / IFC import --------------------------------------------------------

function BimImport() {
  const api = useApi();
  const [file, setFile] = useState(null);
  const [jobId, setJobId] = useState(null);
  const [status, setStatus] = useState(null);
  const [preview, setPreview] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const [committed, setCommitted] = useState(null);
  const pollRef = useRef(null);

  const upload = async () => {
    if (!file) return;
    setBusy(true);
    setError(null);
    setPreview(null);
    setCommitted(null);
    try {
      const form = new FormData();
      form.append("file", file);
      const resp = await api.postForm("/bim/import", form);
      setJobId(resp.job_id);
      setStatus(resp.status || "parsing");
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };

  useEffect(() => {
    if (!jobId || (status && status !== "parsing")) return undefined;
    let active = true;
    const tick = async () => {
      try {
        const st = await api.get(`/bim/status/${jobId}`);
        if (!active) return;
        setStatus(st.status);
        if (st.status && st.status !== "parsing") {
          clearInterval(pollRef.current);
          const pv = await api.get(`/bim/preview/${jobId}`);
          if (active) setPreview(pv.devices || []);
        }
      } catch (err) {
        if (active) setError(err.message);
        clearInterval(pollRef.current);
      }
    };
    pollRef.current = setInterval(tick, 2000);
    tick();
    return () => {
      active = false;
      clearInterval(pollRef.current);
    };
  }, [jobId, status, api]);

  const commit = async () => {
    setBusy(true);
    setError(null);
    try {
      const resp = await api.post(`/bim/commit/${jobId}`, {});
      setCommitted(resp.devices_committed ?? 0);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card title="BIM / IFC Import">
      <div className="form-row">
        <input
          className="input grow"
          type="file"
          accept=".ifc"
          onChange={(e) => setFile(e.target.files?.[0] || null)}
        />
        <button className="btn btn-primary" onClick={upload} disabled={!file || busy}>
          Upload
        </button>
      </div>
      {error && <ErrorState error={error} />}
      {status && (
        <div className="kv">
          <span>Job {jobId}</span>
          <StatusPill value={status === "parsing" ? "running" : "ok"} label={status} />
        </div>
      )}
      {status === "parsing" && <Loading label="Parsing IFC…" />}

      {preview && (
        <>
          <DataTable
            columns={[
              { key: "name", header: "Device" },
              { key: "type", header: "Type" },
              { key: "location", header: "Location" },
              {
                key: "connections",
                header: "Connections",
                render: (d) => (Array.isArray(d.connections) ? d.connections.length : d.connections ?? 0),
              },
            ]}
            rows={preview}
            rowKey={(d, i) => `${d.name}-${i}`}
            empty={<EmptyState title="No devices parsed from the IFC file." />}
          />
          {preview.length > 0 && committed == null && (
            <div className="form-row">
              <button className="btn btn-primary" onClick={commit} disabled={busy}>
                {busy ? "Committing…" : `Commit ${preview.length} device(s) to NetBox`}
              </button>
            </div>
          )}
        </>
      )}
      {committed != null && (
        <div className="banner banner-ok">Committed {committed} device(s) to NetBox.</div>
      )}
    </Card>
  );
}

// ---- PDF -> Config Context ---------------------------------------------------

function ConfigGenerate() {
  const api = useApi();
  const [pdf, setPdf] = useState(null);
  const [slug, setSlug] = useState("");
  const [jobId, setJobId] = useState(null);
  const [status, setStatus] = useState(null);
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const [committed, setCommitted] = useState(false);
  const pollRef = useRef(null);

  const generate = async () => {
    if (!pdf || !slug.trim()) return;
    setBusy(true);
    setError(null);
    setResult(null);
    setCommitted(false);
    try {
      const form = new FormData();
      form.append("pdf", pdf);
      form.append("device_type_slug", slug.trim());
      const resp = await api.postForm("/config/generate", form);
      setJobId(resp.job_id);
      setStatus(resp.status || "extracting");
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };

  useEffect(() => {
    if (!jobId || (status && status !== "extracting")) return undefined;
    let active = true;
    const tick = async () => {
      try {
        const res = await api.get(`/config/result/${jobId}`);
        if (!active) return;
        // result endpoint returns the config once ready; treat presence as done.
        if (res.config_context || res.validation_errors || res.warnings) {
          setResult(res);
          setStatus("ready");
          clearInterval(pollRef.current);
        }
      } catch {
        // still extracting; keep polling
      }
    };
    pollRef.current = setInterval(tick, 2000);
    tick();
    return () => {
      active = false;
      clearInterval(pollRef.current);
    };
  }, [jobId, status, api]);

  const commit = async () => {
    setBusy(true);
    setError(null);
    try {
      await api.post(`/config/commit/${jobId}`, {});
      setCommitted(true);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };

  const hasErrors = result?.validation_errors?.length > 0;

  return (
    <Card title="PDF → Config Context">
      <div className="form-row">
        <input
          className="input grow"
          type="file"
          accept=".pdf"
          onChange={(e) => setPdf(e.target.files?.[0] || null)}
        />
      </div>
      <div className="form-row">
        <input
          className="input grow"
          placeholder="device_type_slug (e.g. cm2000)"
          value={slug}
          onChange={(e) => setSlug(e.target.value)}
        />
        <button className="btn btn-primary" onClick={generate} disabled={!pdf || !slug.trim() || busy}>
          Extract
        </button>
      </div>
      {error && <ErrorState error={error} />}
      {status === "extracting" && <Loading label="Extracting config from PDF…" />}

      {result && (
        <>
          {hasErrors && (
            <div className="banner banner-danger">
              {result.validation_errors.length} validation error(s): {result.validation_errors.slice(0, 3).join("; ")}
            </div>
          )}
          {result.warnings?.length > 0 && (
            <div className="banner banner-warn">
              {result.warnings.length} warning(s): {result.warnings.slice(0, 3).join("; ")}
            </div>
          )}
          <pre className="code-block">{JSON.stringify(result.config_context || {}, null, 2)}</pre>
          {committed ? (
            <div className="banner banner-ok">Config Context committed to NetBox.</div>
          ) : (
            <button className="btn btn-primary" onClick={commit} disabled={busy || hasErrors}>
              {busy ? "Committing…" : "Commit to NetBox"}
            </button>
          )}
        </>
      )}
    </Card>
  );
}
