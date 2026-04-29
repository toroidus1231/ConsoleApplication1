import React, { useState } from "react";
import { api } from "../hooks/useApi";

// Combines BIM import (Module 16) and PDF→Config Context generation
// (Module 17) into one operator-facing page.
export default function ImportPage() {
  const [bimStatus, setBimStatus] = useState("");
  const [cfgResult, setCfgResult] = useState(null);
  const [cfgError, setCfgError] = useState("");

  const importIfc = async (file) => {
    if (!file) return;
    setBimStatus("uploading…");
    const fd = new FormData();
    fd.append("file", file);
    try {
      const r = await api.postForm("/bim/import", fd);
      setBimStatus(`job ${r.job_id} ${r.status}`);
    } catch (e) { setBimStatus(String(e)); }
  };

  const generateCfg = async (pdf, slug) => {
    if (!pdf || !slug) return;
    setCfgError("");
    setCfgResult(null);
    const fd = new FormData();
    fd.append("pdf", pdf);
    fd.append("device_type_slug", slug);
    try {
      const r = await api.postForm("/config/generate", fd);
      // Poll for result.
      const out = await api.get(`/config/result/${r.job_id}`);
      setCfgResult(out);
    } catch (e) { setCfgError(String(e)); }
  };

  return (
    <>
      <h1>Import</h1>

      <div className="card">
        <h2>BIM/IFC Import</h2>
        <input type="file" accept=".ifc"
               onChange={(e) => importIfc(e.target.files?.[0])} />
        <div>{bimStatus}</div>
      </div>

      <div className="card">
        <h2>Config Context from PDF</h2>
        <ConfigForm onGenerate={generateCfg} />
        {cfgError && <div className="severity-critical">{cfgError}</div>}
        {cfgResult && (
          <>
            {cfgResult.validation_errors?.length > 0 && (
              <div className="severity-critical">
                Validation errors:
                <ul>{cfgResult.validation_errors.map((e, i) => <li key={i}>{e}</li>)}</ul>
              </div>
            )}
            {cfgResult.warnings?.length > 0 && (
              <div className="severity-minor">
                Warnings:
                <ul>{cfgResult.warnings.map((w, i) => <li key={i}>{w}</li>)}</ul>
              </div>
            )}
            <pre style={{ background: "#0e1116", padding: "1rem", overflow: "auto" }}>
              {JSON.stringify(cfgResult.config_context, null, 2)}
            </pre>
          </>
        )}
      </div>
    </>
  );
}

function ConfigForm({ onGenerate }) {
  const [slug, setSlug] = useState("");
  const [pdf, setPdf] = useState(null);
  return (
    <>
      <input placeholder="device_type_slug" value={slug}
             onChange={(e) => setSlug(e.target.value)} />
      <input type="file" accept=".pdf"
             onChange={(e) => setPdf(e.target.files?.[0] || null)} />
      <button onClick={() => onGenerate(pdf, slug)} disabled={!pdf || !slug}>
        Generate
      </button>
    </>
  );
}
