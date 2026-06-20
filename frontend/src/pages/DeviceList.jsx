// DeviceList (Contracts §6.4 "/devices"). GET /devices?protocol=&site= + SSE.
// Live poll_result events mark which devices are actively reporting (§6.2).
import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useApi } from "../hooks/useApi.js";
import { useFacility } from "../context/FacilityContext.jsx";
import {
  Card,
  DataTable,
  StatusPill,
  Loading,
  ErrorState,
  EmptyState,
} from "../components/ui.jsx";

const PROTOCOLS = ["", "modbus_tcp", "bacnet_ip", "snmp", "nvml"];

export default function DeviceList() {
  const api = useApi();
  const { sse } = useFacility();
  const [protocol, setProtocol] = useState("");
  const [site, setSite] = useState("");
  const [search, setSearch] = useState("");
  const [devices, setDevices] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  // device_id -> last poll timestamp (ms), from live SSE.
  const [liveSeen, setLiveSeen] = useState({});

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      const params = new URLSearchParams();
      if (protocol) params.set("protocol", protocol);
      if (site) params.set("site", site);
      const qs = params.toString();
      const data = await api.get(`/devices${qs ? `?${qs}` : ""}`);
      setDevices(data.devices || []);
    } catch (err) {
      setError(err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [protocol, site]);

  useEffect(() => {
    return sse.subscribe("poll_result", (data) => {
      setLiveSeen((prev) => ({ ...prev, [String(data.device_id)]: Date.now() }));
    });
  }, [sse]);

  const sites = useMemo(() => {
    const set = new Set((devices || []).map((d) => d.site).filter(Boolean));
    return [...set].sort();
  }, [devices]);

  const filtered = useMemo(() => {
    if (!devices) return [];
    const q = search.trim().toLowerCase();
    if (!q) return devices;
    return devices.filter(
      (d) =>
        (d.name || "").toLowerCase().includes(q) ||
        (d.device_id || "").toLowerCase().includes(q) ||
        (d.primary_ip || "").toLowerCase().includes(q)
    );
  }, [devices, search]);

  return (
    <div className="page">
      <header className="page-head">
        <h1>Devices</h1>
        <p className="page-sub">{devices ? `${devices.length} device(s) from NetBox` : "Loading devices…"}</p>
      </header>

      <Card>
        <div className="form-row">
          <select
            className="input"
            value={protocol}
            onChange={(e) => setProtocol(e.target.value)}
          >
            {PROTOCOLS.map((p) => (
              <option key={p} value={p}>
                {p ? p : "All protocols"}
              </option>
            ))}
          </select>
          <select className="input" value={site} onChange={(e) => setSite(e.target.value)}>
            <option value="">All sites</option>
            {sites.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
          <input
            className="input grow"
            placeholder="Search name / id / IP"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
          <button className="btn btn-ghost" onClick={load}>
            Refresh
          </button>
        </div>
      </Card>

      <Card>
        {loading && <Loading label="Loading devices…" />}
        {error && <ErrorState error={error} onRetry={load} />}
        {!loading && !error && (
          <DataTable
            columns={[
              {
                key: "name",
                header: "Device",
                sortable: true,
                render: (d) => (
                  <Link to={`/devices/${d.device_id}`} className="link">
                    {d.name || d.device_id}
                  </Link>
                ),
              },
              { key: "device_type_slug", header: "Type", sortable: true },
              {
                key: "protocol",
                header: "Protocol",
                render: (d) => <StatusPill value={d.protocol} />,
              },
              { key: "primary_ip", header: "IP" },
              { key: "site", header: "Site", sortable: true },
              { key: "rack", header: "Rack", sortable: true },
              { key: "position", header: "U", width: "56px" },
              {
                key: "live",
                header: "Live",
                render: (d) =>
                  liveSeen[String(d.device_id)] ? (
                    <span className="live-dot on" title="Recently polled" />
                  ) : (
                    <span className="live-dot off" title="No recent poll" />
                  ),
              },
            ]}
            rows={filtered}
            rowKey={(d) => d.device_id}
            empty={
              <EmptyState
                title="No devices"
                detail="Run discovery or import from BIM to populate NetBox."
              />
            }
          />
        )}
      </Card>
    </div>
  );
}
