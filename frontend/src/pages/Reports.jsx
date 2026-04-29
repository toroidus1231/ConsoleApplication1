import React, { useState } from "react";

export default function Reports() {
  const [generating, setGenerating] = useState(false);
  const [error, setError] = useState("");

  const generate = async () => {
    setGenerating(true);
    setError("");
    try {
      const apiKey = localStorage.getItem("api_key") || "";
      const resp = await fetch("/api/v1/reports/generate", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-API-Key": apiKey },
        body: JSON.stringify({
          include_sections: ["discovery", "tests", "punchlist", "attestation"],
        }),
      });
      if (!resp.ok) throw new Error(`${resp.status}`);
      const { report_id } = await resp.json();
      // Stream the binary body straight to a download.
      const pdf = await fetch(`/api/v1/reports/${report_id}`, {
        headers: { "X-API-Key": apiKey },
      });
      const blob = await pdf.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = "commissioning-report.pdf";
      a.click();
      URL.revokeObjectURL(url);
    } catch (e) { setError(String(e)); }
    finally { setGenerating(false); }
  };

  return (
    <>
      <h1>Reports</h1>
      <button onClick={generate} disabled={generating}>
        {generating ? "Generating…" : "Generate PDF"}
      </button>
      {error && <div className="severity-critical">{error}</div>}
    </>
  );
}
