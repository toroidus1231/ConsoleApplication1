import React, { useEffect, useState } from "react";
import { api } from "../hooks/useApi";

// Module 18 (continued): punch-list view with severity / category filters.
const SEVERITIES = ["critical", "major", "minor", "info"];
const CATEGORIES = ["identity", "firmware", "network", "power", "cooling", "security", "safety", "test_failure", "physical"];

export default function PunchList() {
  const [items, setItems] = useState([]);
  const [filterSev, setFilterSev] = useState("");
  const [filterCat, setFilterCat] = useState("");
  const [filterStatus, setFilterStatus] = useState("open");

  useEffect(() => {
    const params = new URLSearchParams();
    if (filterSev) params.set("severity", filterSev);
    if (filterCat) params.set("category", filterCat);
    if (filterStatus) params.set("status", filterStatus);
    const q = params.toString();
    api.get(`/punchlist${q ? "?" + q : ""}`).then((d) => setItems(d.items || [])).catch(() => setItems([]));
  }, [filterSev, filterCat, filterStatus]);

  const resolve = async (id) => {
    await api.patch(`/punchlist/${id}`, { status: "resolved", resolved_by: "engineer" });
    setItems(items.filter((i) => i.id !== id));
  };

  return (
    <>
      <h1>Punch List</h1>
      <div>
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

      <table>
        <thead>
          <tr>
            <th>Severity</th><th>Category</th><th>Device</th><th>Expected</th><th>Actual</th><th>Remediation</th><th></th>
          </tr>
        </thead>
        <tbody>
          {items.map((i) => (
            <tr key={i.id}>
              <td className={`severity-${i.severity}`}>{i.severity}</td>
              <td>{i.category}</td>
              <td>{i.device_name || i.device_id}</td>
              <td>{i.expected}</td>
              <td>{i.actual}</td>
              <td>{i.remediation}</td>
              <td>{i.status === "open" && <button onClick={() => resolve(i.id)}>Resolve</button>}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}
