import React, { useEffect, useState } from "react";
import { api } from "../hooks/useApi";

// Module 18 (continued): device list view.
export default function Devices() {
  const [devices, setDevices] = useState([]);
  const [filter, setFilter] = useState("");

  useEffect(() => {
    const path = filter ? `/devices?protocol=${encodeURIComponent(filter)}` : "/devices";
    api.get(path).then((d) => setDevices(d.devices || [])).catch(() => setDevices([]));
  }, [filter]);

  return (
    <>
      <h1>Devices</h1>
      <select value={filter} onChange={(e) => setFilter(e.target.value)}>
        <option value="">All protocols</option>
        <option value="modbus_tcp">Modbus TCP</option>
        <option value="bacnet_ip">BACnet/IP</option>
        <option value="snmp">SNMP</option>
        <option value="nvml">NVML</option>
      </select>

      <table>
        <thead>
          <tr><th>Device ID</th><th>Name</th><th>Protocol</th><th>Site</th><th>Rack</th><th>IP</th></tr>
        </thead>
        <tbody>
          {devices.map((d) => (
            <tr key={d.device_id}>
              <td>{d.device_id}</td>
              <td>{d.name}</td>
              <td>{d.protocol}</td>
              <td>{d.site}</td>
              <td>{d.rack}@{d.position}</td>
              <td>{d.primary_ip}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}
