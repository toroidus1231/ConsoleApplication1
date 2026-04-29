import React, { useEffect, useState } from "react";
import { api } from "../hooks/useApi";

// Module 20: Attestation Viewer. Lets owner / lender / auditor verify chain
// integrity and download the certificate.
export default function Attestation() {
  const [verify, setVerify] = useState(null);
  const [cert, setCert] = useState(null);
  const [error, setError] = useState("");

  const refresh = async () => {
    setError("");
    try {
      const v = await api.get("/attestation/verify");
      setVerify(v);
      const c = await api.get("/attestation/certificate");
      setCert(c);
    } catch (e) { setError(String(e)); }
  };

  useEffect(() => { refresh(); }, []);

  return (
    <>
      <h1>Attestation</h1>
      <button onClick={refresh}>Re-verify chain</button>
      {error && <div className="severity-critical">{error}</div>}

      {verify && (
        <div className={verify.chain_valid ? "chain-valid" : "chain-broken"}>
          <strong>Chain {verify.chain_valid ? "valid" : "BROKEN"}</strong>
          <div>Length: {verify.length}</div>
          {verify.breaks?.length > 0 && (
            <div>Broken at sequences: {verify.breaks.join(", ")}</div>
          )}
        </div>
      )}

      {cert && (
        <div className="card">
          <h2>Certificate</h2>
          <table>
            <tbody>
              {Object.entries(cert).map(([k, v]) => (
                <tr key={k}><td>{k}</td><td><code>{String(v)}</code></td></tr>
              ))}
            </tbody>
          </table>
          <button onClick={() => download(cert)}>Download JSON</button>
        </div>
      )}
    </>
  );
}

function download(obj) {
  const blob = new Blob([JSON.stringify(obj, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `attestation-${obj.facility || "facility"}.json`;
  a.click();
  URL.revokeObjectURL(url);
}
