import React, { useState } from "react";
import { Routes, Route, NavLink, Navigate } from "react-router-dom";
import {
  Activity, BarChart3, Boxes, FileBarChart, FileSearch, Network,
  ScrollText, ShieldCheck, Upload, Server, Wifi, Zap, Cpu,
} from "lucide-react";
import Dashboard from "./pages/Dashboard";
import Devices from "./pages/Devices";
import Tests from "./pages/Tests";
import TestLive from "./pages/TestLive";
import Timeline from "./pages/Timeline";
import PunchList from "./pages/PunchList";
import Checklist from "./pages/Checklist";
import Attestation from "./pages/Attestation";
import Reports from "./pages/Reports";
import ImportPage from "./pages/ImportPage";
import PowerGraph from "./pages/PowerGraph";
import Discovery from "./pages/Discovery";
import Racks from "./pages/Racks";
import SingleLine from "./pages/SingleLine";
import RelayConsole from "./pages/RelayConsole";
import TransformerConsole from "./pages/TransformerConsole";
import GeneratorConsole from "./pages/GeneratorConsole";
import UpsConsole from "./pages/UpsConsole";
import HipotConsole from "./pages/HipotConsole";
import AtsConsole from "./pages/AtsConsole";
import { useSSE } from "./hooks/useSSE";

const NAV = [
  { to: "/",            label: "Overview",   icon: Activity, end: true },
  { to: "/sld",         label: "SLD",        icon: Zap },
  { to: "/discovery",   label: "Discovery",  icon: Wifi },
  { to: "/devices",     label: "Devices",    icon: Server },
  { to: "/racks",       label: "Racks",      icon: Boxes },
  { to: "/power",       label: "Power DAG",  icon: Network },
  { to: "/tests",       label: "Tests",      icon: BarChart3 },
  { to: "/timeline",    label: "Timeline",   icon: FileBarChart },
  { to: "/punchlist",   label: "Punch List", icon: ScrollText },
  { to: "/checklist",   label: "Checklist",  icon: FileSearch },
  { to: "/attestation", label: "Attestation",icon: ShieldCheck },
  { to: "/import",      label: "Import",     icon: Upload },
];

export default function App() {
  const [apiKey, setApiKey] = useState(localStorage.getItem("api_key") || "");
  if (!apiKey) {
    return <ApiKeyPrompt onSet={(k) => { localStorage.setItem("api_key", k); setApiKey(k); }} />;
  }
  return (
    <div className="app-shell">
      <TopBar />
      <main className="app-main">
        <Routes>
          <Route path="/"            element={<Dashboard />} />
          <Route path="/sld"         element={<SingleLine />} />
          <Route path="/relays/:id"  element={<RelayConsole />} />
          <Route path="/xfmr/:id"    element={<TransformerConsole />} />
          <Route path="/gens/:id"    element={<GeneratorConsole />} />
          <Route path="/ups/:id"     element={<UpsConsole />} />
          <Route path="/hipot/:id"   element={<HipotConsole />} />
          <Route path="/ats/:id"     element={<AtsConsole />} />
          <Route path="/discovery"   element={<Discovery />} />
          <Route path="/devices"     element={<Devices />} />
          <Route path="/racks"       element={<Racks />} />
          <Route path="/power"       element={<PowerGraph />} />
          <Route path="/tests"       element={<Tests />} />
          <Route path="/tests/:testId" element={<TestLive />} />
          <Route path="/timeline"    element={<Timeline />} />
          <Route path="/punchlist"   element={<PunchList />} />
          <Route path="/checklist"   element={<Checklist />} />
          <Route path="/attestation" element={<Attestation />} />
          <Route path="/reports"     element={<Reports />} />
          <Route path="/import"      element={<ImportPage />} />
          <Route path="*"            element={<Navigate to="/" replace />} />
        </Routes>
      </main>
    </div>
  );
}

function TopBar() {
  const { connected } = useSSE();
  return (
    <nav className="topbar">
      <div className="brand">CX <span>Platform</span></div>
      {NAV.map(({ to, label, icon: Icon, end }) => (
        <NavLink key={to} to={to} end={!!end}>
          <Icon size={14} /> {label}
        </NavLink>
      ))}
      <div className="right">
        <span className="conn">
          <span className={"dot " + (connected ? "dot-up" : "dot-down")} />
          {connected ? "events live" : "events offline"}
        </span>
      </div>
    </nav>
  );
}

function ApiKeyPrompt({ onSet }) {
  const [v, setV] = useState("");
  return (
    <main style={{ padding: 80, maxWidth: 480, margin: "0 auto" }}>
      <h1 style={{ fontSize: 18 }}>Sign in</h1>
      <p style={{ color: "var(--text-faint)" }}>Paste the api.key from <code>platform.yml</code> or use <code>demo</code> for the dev server.</p>
      <input style={{ width: "100%" }} type="password" autoFocus
             value={v} onChange={(e) => setV(e.target.value)}
             onKeyDown={(e) => e.key === "Enter" && v && onSet(v)} />
      <div style={{ marginTop: 12 }}>
        <button onClick={() => v && onSet(v)} disabled={!v}>Continue</button>
      </div>
    </main>
  );
}
