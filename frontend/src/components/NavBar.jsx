// NavBar — sidebar navigation (Contracts §6.4 route table) plus a live
// connection/health indicator fed by the SSE stream and the health snapshot.
import { NavLink } from "react-router-dom";
import { useFacility } from "../context/FacilityContext.jsx";

const LINKS = [
  { to: "/", label: "Overview", end: true, icon: "▦" },
  { to: "/discovery", label: "Discovery", icon: "◎" },
  { to: "/devices", label: "Devices", icon: "▤" },
  { to: "/tests", label: "Tests", icon: "⚡" },
  { to: "/punchlist", label: "Punch List", icon: "✓" },
  { to: "/checklist", label: "Checklist", icon: "☑" },
  { to: "/attestation", label: "Attestation", icon: "⛓" },
  { to: "/reports", label: "Reports", icon: "▢" },
  { to: "/import", label: "Import", icon: "⇪" },
];

// Roll the per-component health map ({modbus: {devices, errors}, ...}) and the
// infra checks (netbox/influxdb/minio) into a single ok/degraded indicator.
function overallStatus(health) {
  if (!health) return { tone: "muted", text: "connecting" };
  const infra = ["netbox", "influxdb", "minio"];
  const anyInfraDown = infra.some((k) => health[k] && health[k] !== "ok");
  const workers = health.workers || {};
  const anyWorkerErr = Object.values(workers).some(
    (w) => w && typeof w === "object" && (w.errors || 0) > 0
  );
  if (anyInfraDown) return { tone: "critical", text: "infra degraded" };
  if (anyWorkerErr) return { tone: "warn", text: "worker errors" };
  return { tone: "ok", text: "healthy" };
}

export default function NavBar() {
  const { facilityName, health, sse } = useFacility();
  const status = overallStatus(health);

  return (
    <nav className="sidebar">
      <div className="brand">
        <div className="brand-mark">CX</div>
        <div className="brand-text">
          <div className="brand-name">{facilityName}</div>
          <div className="brand-sub">Commissioning</div>
        </div>
      </div>

      <ul className="nav-links">
        {LINKS.map((l) => (
          <li key={l.to}>
            <NavLink
              to={l.to}
              end={l.end}
              className={({ isActive }) => `nav-link${isActive ? " active" : ""}`}
            >
              <span className="nav-icon" aria-hidden="true">
                {l.icon}
              </span>
              <span>{l.label}</span>
            </NavLink>
          </li>
        ))}
      </ul>

      <div className="sidebar-foot">
        <div className={`conn-dot ${sse.connected ? "on" : "off"}`} />
        <div className="conn-text">
          <div>SSE {sse.connected ? "live" : "offline"}</div>
          <div className={`conn-status tone-${status.tone}`}>{status.text}</div>
        </div>
      </div>
    </nav>
  );
}
