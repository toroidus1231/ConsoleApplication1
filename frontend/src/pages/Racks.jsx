import React, { useEffect, useMemo, useState } from "react";
import { api } from "../hooks/useApi";

// Module 18 (Rack elevations): SVG-style rack frames showing each device
// at its U-position from NetBox, color-coded by latest test status.

const RACK_HEIGHT_U = 45;

export default function Racks() {
  const [racks, setRacks] = useState({});

  useEffect(() => {
    api.get("/racks").then((r) => setRacks(r.racks || {})).catch(() => {});
  }, []);

  const ordered = useMemo(() => Object.entries(racks).sort(([a], [b]) => a.localeCompare(b)), [racks]);

  return (
    <>
      <div className="page-header">
        <h1>Rack Elevations</h1>
        <span className="crumb">{ordered.length} racks · positions from NetBox</span>
      </div>

      <div className="rack-grid">
        {ordered.map(([rack, devices]) => (
          <Rack key={rack} name={rack} devices={devices} />
        ))}
      </div>
    </>
  );
}

function Rack({ name, devices }) {
  // Build a sparse 1..RACK_HEIGHT_U grid; each device occupies its U slot.
  const occupied = new Map();
  for (const d of devices) {
    if (!d.position || d.position < 1 || d.position > RACK_HEIGHT_U) continue;
    occupied.set(d.position, d);
  }
  const us = [];
  for (let u = RACK_HEIGHT_U; u >= 1; u--) {
    const dev = occupied.get(u);
    us.push(
      <div className="rack-u" key={u}>
        <div className="u-num">{u}</div>
        <div className={`u-dev ${dev ? `has ${dev.test_status || "pending"}` : ""}`}
             title={dev ? `${dev.name} (${dev.device_type_slug})` : ""}>
          {dev ? `${dev.name} · ${dev.device_type_slug}` : ""}
        </div>
      </div>,
    );
  }
  return (
    <div className="rack">
      <div className="rack-title">Rack {name}</div>
      <div className="rack-frame">{us}</div>
    </div>
  );
}
