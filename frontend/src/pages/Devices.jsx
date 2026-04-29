import React, { useEffect, useMemo, useState } from "react";
import { api } from "../hooks/useApi";

export default function Devices() {
  const [devices, setDevices] = useState([]);
  const [protocol, setProtocol] = useState("");
  const [search, setSearch] = useState("");

  useEffect(() => {
    const path = protocol ? `/devices?protocol=${encodeURIComponent(protocol)}` : "/devices";
    api.get(path).then((d) => setDevices(d.devices || [])).catch(() => setDevices([]));
  }, [protocol]);

  const visible = useMemo(() => {
    const q = search.toLowerCase();
    return devices.filter((d) =>
      !q ||
      d.name.toLowerCase().includes(q) ||
      d.device_id.toLowerCase().includes(q) ||
      d.rack.toLowerCase().includes(q) ||
      d.device_type_slug.toLowerCase().includes(q),
    );
  }, [devices, search]);

  return (
    <>
      <div className="page-header">
        <h1>Devices</h1>
        <span className="crumb">{visible.length} of {devices.length}</span>
      </div>

      <div className="card">
        <div className="row">
          <select value={protocol} onChange={(e) => setProtocol(e.target.value)}>
            <option value="">All protocols</option>
            <option value="modbus_tcp">Modbus TCP</option>
            <option value="bacnet_ip">BACnet/IP</option>
            <option value="snmp">SNMP</option>
            <option value="redfish">Redfish</option>
            <option value="nvml">NVML</option>
          </select>
          <input style={{ flex: 1 }} placeholder="search by name, type, rack…"
                 value={search} onChange={(e) => setSearch(e.target.value)} />
        </div>
      </div>

      <div className="card" style={{ padding: 0 }}>
        <table>
          <thead>
            <tr>
              <th>Status</th><th>Device</th><th>Type</th><th>Protocol</th>
              <th>Site</th><th>Rack</th><th>IP</th><th>Powered by</th>
            </tr>
          </thead>
          <tbody>
            {visible.map((d) => (
              <tr key={d.device_id}>
                <td><span className={`badge badge-${d.test_status || "pending"}`}>{d.test_status || "pending"}</span></td>
                <td>
                  <div style={{ fontWeight: 500 }}>{d.name}</div>
                  <div className="mono" style={{ color: "var(--text-faint)", fontSize: 10.5 }}>{d.device_id}</div>
                </td>
                <td>{d.device_type_slug}</td>
                <td>{d.protocol}</td>
                <td>{d.site}</td>
                <td>{d.rack}@{d.position}</td>
                <td className="mono">{d.primary_ip}</td>
                <td className="mono" style={{ color: "var(--text-faint)" }}>{d.power_source_id || "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}
