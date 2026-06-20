// PowerDepGraph — visualizes power dependency chains (Contracts §5.1) with each
// powered device color-coded by its latest active-test status. The orchestrator
// serializes tests that share a power ancestor, so this view helps an engineer
// see which chains are blocked.
//
// The §4 API does not expose a dedicated power-graph endpoint, so this component
// derives chains from each device's Config Context. A device's upstream source
// is read from config_context.power.source / power_source / upstream (whichever
// the BIM import populated). Devices grouped under the same source form a chain.
// If no power topology is modeled, it falls back to grouping by rack.
import { useMemo } from "react";
import { StatusPill, EmptyState } from "./ui.jsx";

function upstreamOf(device) {
  const ctx = device.config_context || {};
  const power = ctx.power || {};
  return (
    power.source ||
    power.source_device ||
    ctx.power_source ||
    ctx.upstream ||
    null
  );
}

const STATUS_COLOR = {
  passed: "#2ea043",
  failed: "#f85149",
  aborted: "#f85149",
  restore_failure: "#f85149",
  precondition_failed: "#d29922",
  manual_pending: "#d29922",
  running: "#d29922",
  queued: "#388bfd",
};

export default function PowerDepGraph({ devices = [], statusByDevice = {} }) {
  // Group devices into chains keyed by their upstream source (or rack fallback).
  const chains = useMemo(() => {
    const groups = new Map();
    devices.forEach((d) => {
      const key = upstreamOf(d) || d.rack || "Unassigned";
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(d);
    });
    return [...groups.entries()].map(([source, members]) => ({
      source,
      members: members.sort((a, b) => (a.position || 0) - (b.position || 0)),
    }));
  }, [devices]);

  if (devices.length === 0) {
    return (
      <EmptyState
        title="No power topology"
        detail="Power chains appear once devices are discovered or imported from BIM."
      />
    );
  }

  return (
    <div className="power-graph">
      {chains.map((chain) => (
        <div className="power-chain" key={chain.source}>
          <div className="power-source">
            <span className="power-source-icon">⌁</span>
            {chain.source}
          </div>
          <div className="power-children">
            {chain.members.map((d) => {
              const status = statusByDevice[d.device_id];
              const color = STATUS_COLOR[status] || "#30363d";
              return (
                <div
                  className="power-node"
                  key={d.device_id}
                  style={{ borderLeftColor: color }}
                  title={status ? `Latest test: ${status}` : "No test run"}
                >
                  <div className="power-node-name">{d.name || d.device_id}</div>
                  <div className="power-node-meta">
                    {d.device_type_slug || d.protocol || ""}
                  </div>
                  {status && <StatusPill value={status} />}
                </div>
              );
            })}
          </div>
        </div>
      ))}
    </div>
  );
}
