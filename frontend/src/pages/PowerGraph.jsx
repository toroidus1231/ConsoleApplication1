import React, { useEffect, useMemo, useState, useCallback } from "react";
import ReactFlow, {
  Background,
  Controls,
  MiniMap,
  Position,
  useNodesState,
  useEdgesState,
} from "reactflow";
import "reactflow/dist/style.css";
import { api } from "../hooks/useApi";

// Module 18: Power Dependency Graph view.
//
// Renders the NetBox power DAG (utility → ATS → UPS → PDU → server) as
// React Flow nodes. Nodes are colored by latest test_status and laid out
// in tiers using their "device_type" so the chain is readable.

const TIER_BY_TYPE = {
  utility: 0, "cat-3516": 0,
  "asco-7000": 1,
  "apc-symmetra": 2,
  "apc-rack-pdu": 3,
  cm2000: 4, "dgx-h100": 4,
};

const TIER_LABELS = ["Utility / Gen", "ATS", "UPS", "PDU", "Branch"];

function laidOut(graph) {
  const tiers = {};
  for (const n of graph.nodes || []) {
    const t = TIER_BY_TYPE[n.device_type] ?? 5;
    tiers[t] = tiers[t] || [];
    tiers[t].push(n);
  }
  const X_PER_TIER = 240;
  const Y_PER_NODE = 60;
  const nodes = [];
  for (const tier of Object.keys(tiers).sort()) {
    const arr = tiers[tier];
    arr.forEach((n, i) => {
      nodes.push({
        id: n.id,
        position: { x: tier * X_PER_TIER, y: i * Y_PER_NODE - (arr.length - 1) * Y_PER_NODE / 2 + 320 },
        data: { label: <NodeLabel node={n} /> },
        sourcePosition: Position.Right,
        targetPosition: Position.Left,
        className: `status-${n.test_status || "pending"}`,
        style: { width: 180 },
      });
    });
  }
  const edges = (graph.edges || []).map((e) => ({
    id: e.id,
    source: e.source,
    target: e.target,
    animated: false,
    style: { stroke: "var(--text-faint)" },
  }));
  return { nodes, edges };
}

function NodeLabel({ node }) {
  return (
    <div style={{ textAlign: "left" }}>
      <div style={{ fontWeight: 600, fontSize: 11.5 }}>{node.label}</div>
      <div style={{ fontSize: 10, color: "var(--text-faint)" }}>
        {node.device_type} · {node.rack}
      </div>
      <div style={{ marginTop: 4 }}>
        <span className={`badge badge-${node.test_status || "pending"}`}
              style={{ fontSize: 9, padding: "1px 6px" }}>
          {node.test_status || "pending"}
        </span>
      </div>
    </div>
  );
}

export default function PowerGraph() {
  const [graph, setGraph] = useState({ nodes: [], edges: [] });
  const [highlight, setHighlight] = useState(null);
  const { nodes: laidNodes, edges: laidEdges } = useMemo(() => laidOut(graph), [graph]);
  const [nodes, setNodes, onNodesChange] = useNodesState(laidNodes);
  const [edges, setEdges, onEdgesChange] = useEdgesState(laidEdges);

  useEffect(() => {
    api.get("/power-graph").then(setGraph).catch(() => {});
  }, []);

  useEffect(() => { setNodes(laidNodes); }, [laidNodes, setNodes]);
  useEffect(() => { setEdges(laidEdges); }, [laidEdges, setEdges]);

  const onNodeClick = useCallback((_, node) => {
    setHighlight(graph.nodes.find((n) => n.id === node.id));
  }, [graph]);

  const counts = useMemo(() => {
    const out = { passed: 0, failed: 0, aborted: 0, pending: 0, running: 0 };
    for (const n of graph.nodes || []) out[n.test_status || "pending"] = (out[n.test_status || "pending"] || 0) + 1;
    return out;
  }, [graph]);

  return (
    <>
      <div className="page-header">
        <h1>Power Dependency Graph</h1>
        <span className="crumb">{graph.nodes?.length || 0} devices · {graph.edges?.length || 0} feeds</span>
      </div>

      <div className="tile-row">
        <div className="tile"><div className="label">Passed</div><div className="value pass">{counts.passed}</div></div>
        <div className="tile"><div className="label">Failed</div><div className="value crit">{counts.failed + counts.aborted}</div></div>
        <div className="tile"><div className="label">Pending</div><div className="value info">{counts.pending}</div></div>
        <div className="tile"><div className="label">Running</div><div className="value maj">{counts.running}</div></div>
      </div>

      <div className="row">
        <div className="card grow" style={{ height: 640, padding: 0 }}>
          <ReactFlow
            nodes={nodes}
            edges={edges}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            onNodeClick={onNodeClick}
            fitView
            fitViewOptions={{ padding: 0.2 }}
          >
            <Background gap={24} color="#1a212c" />
            <Controls showInteractive={false} />
            <MiniMap nodeStrokeColor={() => "#58a6ff"} nodeColor={() => "#1a212c"}
                     style={{ background: "var(--bg-1)", border: "1px solid var(--border)" }} />
          </ReactFlow>
        </div>

        <div className="card" style={{ width: 320 }}>
          <div className="card-h">Selection</div>
          {highlight ? (
            <div className="kv">
              <div className="k">Device</div><div className="v">{highlight.label}</div>
              <div className="k">Type</div><div className="v">{highlight.device_type}</div>
              <div className="k">Rack</div><div className="v">{highlight.rack}@{highlight.position}</div>
              <div className="k">Status</div>
              <div className="v">
                <span className={`badge badge-${highlight.test_status || "pending"}`}>{highlight.test_status || "pending"}</span>
              </div>
              <div className="k">ID</div><div className="v mono">{highlight.id}</div>
            </div>
          ) : (
            <p style={{ color: "var(--text-faint)" }}>Click any node.</p>
          )}
        </div>
      </div>

      <div className="card">
        <div className="card-h">Legend</div>
        <div style={{ display: "flex", gap: 24, color: "var(--text-dim)", fontSize: 12 }}>
          {TIER_LABELS.map((label, i) => (
            <span key={i}><span style={{ color: "var(--text-faint)" }}>Tier {i}:</span> {label}</span>
          ))}
        </div>
      </div>
    </>
  );
}
