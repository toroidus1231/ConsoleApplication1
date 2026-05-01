import React, { useEffect, useState } from "react";
import { Routes, Route, NavLink, Navigate, useLocation, Link } from "react-router-dom";
import {
  Activity, BarChart3, Boxes, FileBarChart, FileSearch, Network,
  ScrollText, ShieldCheck, Upload, Server, Wifi, Zap, Search,
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
import EquipmentPanel from "./pages/EquipmentPanel";
import { useSSE } from "./hooks/useSSE";
import { api } from "./hooks/useApi";

const NAV_GROUPS = [
  {
    label: "Operations",
    items: [
      { to: "/",           label: "Overview",     icon: Activity, end: true },
      { to: "/sld",        label: "Single-Line",  icon: Zap },
      { to: "/timeline",   label: "Test Timeline",icon: FileBarChart },
      { to: "/tests",      label: "Active Tests", icon: BarChart3 },
    ],
  },
  {
    label: "Inventory",
    items: [
      { to: "/discovery",  label: "Discovery",    icon: Wifi },
      { to: "/devices",    label: "Devices",      icon: Server },
      { to: "/racks",      label: "Rack View",    icon: Boxes },
      { to: "/power",      label: "Power DAG",    icon: Network },
    ],
  },
  {
    label: "Findings",
    items: [
      { to: "/punchlist",  label: "Punch List",   icon: ScrollText },
      { to: "/checklist",  label: "Checklist",    icon: FileSearch },
      { to: "/attestation",label: "Attestation",  icon: ShieldCheck },
    ],
  },
  {
    label: "Project",
    items: [
      { to: "/reports",    label: "Reports",      icon: FileBarChart },
      { to: "/import",     label: "Import",       icon: Upload },
    ],
  },
];

export default function App() {
  const [apiKey, setApiKey] = useState(localStorage.getItem("api_key") || "");
  if (!apiKey) {
    return <ApiKeyPrompt onSet={(k) => { localStorage.setItem("api_key", k); setApiKey(k); }} />;
  }
  return (
    <div className="app-shell">
      <Sidebar />
      <main className="app-main">
        <TopBar />
        <div className="app-content">
          <Routes>
            <Route path="/"            element={<Dashboard />} />
            <Route path="/sld"         element={<SingleLine />} />
            <Route path="/relays/:id"    element={<RelayConsole />} />
            <Route path="/equipment/:id" element={<EquipmentPanel />} />
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
        </div>
      </main>
      <StatusBar />
    </div>
  );
}

function Sidebar() {
  return (
    <aside className="sidebar">
      <div className="brand">
        <div className="brand-mark">CX</div>
        <div className="brand-text">
          <span className="name">Commissioning</span>
          <span className="env">DC1-Ashburn · prod</span>
        </div>
      </div>
      {NAV_GROUPS.map((group) => (
        <div className="nav-section" key={group.label}>
          <div className="nav-section-label">{group.label}</div>
          {group.items.map(({ to, label, icon: Icon, end }) => (
            <NavLink key={to} to={to} end={!!end}
                     className={({ isActive }) => "nav-link" + (isActive ? " active" : "")}>
              <Icon size={14} strokeWidth={1.8} /> {label}
            </NavLink>
          ))}
        </div>
      ))}
    </aside>
  );
}

function TopBar() {
  const loc = useLocation();
  const crumbs = breadcrumbs(loc.pathname);
  return (
    <div className="topbar">
      <div className="crumbs">
        {crumbs.map((c, i) => (
          <React.Fragment key={i}>
            {i > 0 && <span className="sep">/</span>}
            {c.to ? <Link to={c.to}>{c.label}</Link>
                   : <span className="here">{c.label}</span>}
          </React.Fragment>
        ))}
      </div>
      <div className="cmd-search">
        <Search size={11} />
        <span style={{ flex: 1 }}>Search devices, tests, attestations…</span>
        <kbd>⌘K</kbd>
      </div>
    </div>
  );
}

function breadcrumbs(path) {
  const parts = path.split("/").filter(Boolean);
  if (parts.length === 0) return [{ label: "Overview" }];
  const sectionLabels = {
    sld: "Single-Line", relays: "Relay", xfmr: "Transformer", gens: "Generator",
    ups: "UPS", hipot: "Cable Hipot", ats: "ATS", discovery: "Discovery",
    devices: "Devices", racks: "Rack View", power: "Power DAG", tests: "Tests",
    timeline: "Test Timeline", punchlist: "Punch List", checklist: "Checklist",
    attestation: "Attestation", reports: "Reports", import: "Import",
  };
  const out = [{ label: "Overview", to: "/" }];
  if (parts[0]) out.push({
    label: sectionLabels[parts[0]] || parts[0],
    to: parts.length > 1 ? "/" + parts[0] : null,
  });
  if (parts.length > 1) out.push({ label: parts[1] });
  return out;
}

function StatusBar() {
  const { connected } = useSSE();
  const [tel, setTel] = useState(null);
  useEffect(() => {
    let alive = true;
    const tick = async () => {
      try { const d = await api.get("/telemetry"); if (alive) setTel(d); } catch (_) {}
    };
    tick();
    const h = setInterval(tick, 2000);
    return () => { alive = false; clearInterval(h); };
  }, []);
  const mvA = tel?.buses?.["mv-bus-A"];
  const xfmrArc = tel?.xfmrs && Object.values(tel.xfmrs).some((x) => x.c2h2_ppm > 2);
  const soeCrit = tel?.soe?.filter((e) => e.severity === "critical").length || 0;
  return (
    <footer className="statusbar">
      <span className="seg">
        <span className={"dot " + (connected ? "ok" : "fail")} />
        <span className="lbl">EVENTS</span>
        <span className={"val " + (connected ? "ok" : "fail")}>
          {connected ? "live" : "down"}
        </span>
      </span>
      <span className="seg">
        <span className="lbl">FACILITY</span>
        <span className="val">DC1-Ashburn</span>
      </span>
      {mvA && (
        <>
          <span className="seg">
            <span className="lbl">MV-A</span>
            <span className="val">{Math.round(mvA.voltage_ll_v).toLocaleString()} V</span>
          </span>
          <span className="seg">
            <span className="lbl">f</span>
            <span className="val">{mvA.frequency_hz.toFixed(3)} Hz</span>
          </span>
          <span className="seg">
            <span className="lbl">PF</span>
            <span className="val">{mvA.power_factor.toFixed(3)}</span>
          </span>
        </>
      )}
      {xfmrArc && (
        <span className="seg">
          <span className="dot fail" />
          <span className="val fail">DGA: ACTIVE ARCING (XFMR-A1)</span>
        </span>
      )}
      <span className="seg" style={{ marginLeft: "auto" }}>
        <span className="lbl">SOE</span>
        <span className={"val " + (soeCrit > 0 ? "fail" : "ok")}>
          {soeCrit > 0 ? `${soeCrit} critical` : "clear"}
        </span>
      </span>
      <span className="seg">
        <span className="lbl">TIME</span>
        <span className="val">{new Date().toISOString().slice(11, 19)}Z</span>
      </span>
    </footer>
  );
}

function ApiKeyPrompt({ onSet }) {
  const [v, setV] = useState("");
  return (
    <main style={{ padding: 80, maxWidth: 460, margin: "0 auto" }}>
      <div className="brand" style={{ borderBottom: "none", padding: 0, marginBottom: 24 }}>
        <div className="brand-mark" style={{ width: 32, height: 32, fontSize: 14 }}>CX</div>
        <div className="brand-text">
          <span className="name" style={{ fontSize: 18 }}>Commissioning Platform</span>
          <span className="env">DC1-Ashburn · prod</span>
        </div>
      </div>
      <h1 style={{ fontSize: 14, fontWeight: 600, marginTop: 24, marginBottom: 6, textTransform: "uppercase",
                   letterSpacing: 0.06 + "em", color: "var(--text-tertiary)" }}>
        Authenticate
      </h1>
      <p style={{ color: "var(--text-tertiary)", fontSize: 12, marginTop: 0 }}>
        Paste the API key from <code>platform.yml</code>, or use <code>demo</code> for the dev server.
      </p>
      <input style={{ width: "100%", marginTop: 16 }} type="password" autoFocus
             value={v} onChange={(e) => setV(e.target.value)}
             onKeyDown={(e) => e.key === "Enter" && v && onSet(v)} />
      <div style={{ marginTop: 16 }}>
        <button className="primary" onClick={() => v && onSet(v)} disabled={!v}>
          Continue →
        </button>
      </div>
    </main>
  );
}
