import React, { useState, useEffect } from "react";
import { Routes, Route, NavLink, Navigate } from "react-router-dom";
import Dashboard from "./pages/Dashboard";
import Devices from "./pages/Devices";
import Tests from "./pages/Tests";
import PunchList from "./pages/PunchList";
import Checklist from "./pages/Checklist";
import Attestation from "./pages/Attestation";
import Reports from "./pages/Reports";
import ImportPage from "./pages/ImportPage";

export default function App() {
  const [apiKey, setApiKey] = useState(localStorage.getItem("api_key") || "");

  if (!apiKey) {
    return <ApiKeyPrompt onSet={(k) => { localStorage.setItem("api_key", k); setApiKey(k); }} />;
  }

  return (
    <>
      <nav>
        <NavLink to="/" end>Overview</NavLink>
        <NavLink to="/devices">Devices</NavLink>
        <NavLink to="/tests">Tests</NavLink>
        <NavLink to="/punchlist">Punch List</NavLink>
        <NavLink to="/checklist">Checklist</NavLink>
        <NavLink to="/attestation">Attestation</NavLink>
        <NavLink to="/reports">Reports</NavLink>
        <NavLink to="/import">Import</NavLink>
      </nav>
      <main>
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/devices" element={<Devices />} />
          <Route path="/tests" element={<Tests />} />
          <Route path="/punchlist" element={<PunchList />} />
          <Route path="/checklist" element={<Checklist />} />
          <Route path="/attestation" element={<Attestation />} />
          <Route path="/reports" element={<Reports />} />
          <Route path="/import" element={<ImportPage />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
    </>
  );
}

function ApiKeyPrompt({ onSet }) {
  const [v, setV] = useState("");
  return (
    <main>
      <h1>API Key</h1>
      <p>Paste the api.key from platform.yml.</p>
      <input value={v} onChange={(e) => setV(e.target.value)} type="password" />
      <button onClick={() => v && onSet(v)}>Save</button>
    </main>
  );
}
