import React, { useEffect, useState } from "react";
import { useParams, Link } from "react-router-dom";
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip,
         ResponsiveContainer, Legend, ReferenceLine, ReferenceArea,
         BarChart, Bar, Cell } from "recharts";
import { api } from "../hooks/useApi";

// ============================================================================
// Generic equipment panel — renders any device family from its Config Context
// `panel.layout` block. Adding a new family is a JSON drop with a layout —
// no React code, unless the family needs a primitive that doesn't exist yet.
// ============================================================================

export default function EquipmentPanel() {
  const { id } = useParams();
  const [rec, setRec] = useState(null);
  useEffect(() => {
    api.get(`/equipment/${id}`).then(setRec).catch(() => {});
  }, [id]);

  if (!rec) return <p style={{ color: "var(--text-faint)" }}>Loading…</p>;
  if (rec.no_prior_run) return <NoPriorRun id={id} category={rec.kind || rec.category} />;

  const layout = rec.panel_layout;
  if (!layout) {
    return <p style={{ color: "var(--text-faint)" }}>
      No panel layout configured for {rec.category} ({rec.device_type_slug}).
    </p>;
  }
  return (
    <>
      {layout.header && <PanelHeader spec={layout.header} data={rec} id={id} />}
      {layout.tiles && <TileRow tiles={layout.tiles} data={rec} />}
      {(layout.cards || []).map((card, i) => (
        <Card key={i} spec={card} data={rec} />
      ))}
    </>
  );
}

// ============================================================================
// Template / field resolution
// ============================================================================

function resolveField(data, path) {
  if (data == null || !path) return undefined;
  return path.split(".").reduce((o, k) => (o == null ? o : o[k]), data);
}

function applyFormat(value, fmt) {
  if (fmt == null || value == null) return value;
  // Python-style {:.2f}, {:.0f}, {:.3f} …
  const m = fmt.match(/\{:\.(\d+)f\}(.*)/);
  if (m && typeof value === "number") return value.toFixed(parseInt(m[1])) + m[2];
  if (fmt.includes("{}")) return fmt.replace("{}", String(value));
  return String(value);
}

function resolveTemplate(template, data) {
  if (!template) return "";
  return template.replace(/\{([^}]+)\}/g, (_, expr) => {
    const v = resolveField(data, expr);
    return v == null ? "" : String(v);
  });
}

function resolveValue(spec, data) {
  if (spec.value_template) return resolveTemplate(spec.value_template, data);
  if (spec.value_field) {
    const v = resolveField(data, spec.value_field);
    return spec.format ? applyFormat(v, spec.format) : v;
  }
  if (spec.value !== undefined) return spec.value;
  return "";
}

function statusLabel(spec, data) {
  if (!spec) return null;
  const v = resolveField(data, spec.field ?? spec.status_field);
  if (v == null) return null;
  const labels = spec.labels ?? spec.status_labels ?? {};
  return labels[String(v)] ?? (v ? "PASS" : "FAIL");
}

// ============================================================================
// Header
// ============================================================================

function PanelHeader({ spec, data, id }) {
  const title = resolveTemplate(spec.title_template, { ...data, device_id: id });
  const crumb = resolveTemplate(spec.crumb_template, { ...data, device_id: id });
  const passField = resolveField(data, spec.status_field);
  const label = statusLabel(spec, data);
  return (
    <div className="page-header">
      <h1>{title}</h1>
      {crumb && (
        <span className="crumb">
          <Link to="/sld" style={{ color: "var(--text-faint)" }}>SLD</Link>
          {" / "}{crumb}
        </span>
      )}
      {label && (
        <span className={`badge badge-${passField ? "passed" : "failed"}`}
              style={{ marginLeft: "auto" }}>{label}</span>
      )}
    </div>
  );
}

// ============================================================================
// Tile + TileRow
// ============================================================================

function Tile({ spec, data }) {
  let color = "var(--text)";
  if (spec.color_field && spec.color_labels) {
    const v = resolveField(data, spec.color_field);
    color = spec.color_labels[String(v)] || color;
  } else if (spec.color) {
    color = spec.color;
  }
  return (
    <div className="tile">
      <div className="label">{spec.label}</div>
      <div className="value" style={{ fontSize: 18, color, fontFamily: "monospace" }}>
        {resolveValue(spec, data)}
      </div>
    </div>
  );
}

function TileRow({ tiles, data }) {
  return (
    <div className="tile-row">
      {tiles.map((t, i) => <Tile key={i} spec={t} data={data} />)}
    </div>
  );
}

// ============================================================================
// Cards
// ============================================================================

function Card({ spec, data }) {
  const renderer = CARD_RENDERERS[spec.kind];
  if (!renderer) {
    return <div className="card padded">
      <p style={{ color: "var(--fail)" }}>
        unknown panel card kind: <code>{spec.kind}</code>
      </p>
    </div>;
  }
  return renderer(spec, data);
}

// --- kv_table ---
function KvTable(spec, data) {
  return (
    <div className="card">
      <div className="card-h">{spec.title}</div>
      <div className="kv">
        {spec.rows.map((row, i) => (
          <React.Fragment key={i}>
            <div className="k">{row.label}</div>
            <div className={`v${row.mono ? " mono" : ""}`}
                 style={row.wrap ? { wordBreak: "break-all" } : undefined}>
              {resolveValue(row, data)}
            </div>
          </React.Fragment>
        ))}
      </div>
    </div>
  );
}

// --- table ---
function CardTable(spec, data) {
  const rows = resolveField(data, spec.data_path) || [];
  return (
    <div className="card" style={{ padding: 0 }}>
      <div className="card-h">{spec.title}</div>
      <table>
        <thead>
          <tr>{spec.columns.map((c, i) => <th key={i}>{c.label}</th>)}</tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={i}>
              {spec.columns.map((col, j) => (
                <td key={j} className={col.mono ? "mono" : undefined}>
                  {renderCell(col, row)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function renderCell(col, row) {
  if (col.kind === "badge") {
    const v = resolveField(row, col.field);
    const label = col.labels?.[String(v)] ?? (v ? "pass" : "fail");
    return <span className={`badge badge-${v ? "passed" : "failed"}`}>{label}</span>;
  }
  const raw = resolveField(row, col.field);
  return col.format ? applyFormat(raw, col.format) : raw;
}

// --- signoff_table ---
function SignoffTable(spec, data) {
  const obj = resolveField(data, spec.data_path) || {};
  const rows = Object.entries(obj);
  return (
    <div className="card" style={{ padding: 0 }}>
      <div className="card-h">{spec.title}</div>
      <table>
        <thead><tr>
          <th>Step</th><th>Operator</th><th>Note</th><th>Status</th>
        </tr></thead>
        <tbody>
          {rows.map(([k, v]) => (
            <tr key={k}>
              <td>{v.label}</td>
              <td className="mono">{v.operator || "—"}</td>
              <td style={{ color: "var(--text-tertiary)", fontSize: 12 }}>
                {v.note || v.spec_reference || ""}
              </td>
              <td>
                <span className={`badge badge-${v.passed ? "passed" : "failed"}`}>
                  {v.passed ? "signed" : "open"}
                </span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// --- line_chart ---
function ChartCard(spec, data) {
  const rawSeries = resolveField(data, spec.data_path) || [];
  const series = spec.decimate ? rawSeries.filter((_, i) => i % spec.decimate === 0) : rawSeries;
  return (
    <div className="card">
      <div className="card-h">{spec.title}</div>
      <div style={{ height: spec.height || 280 }}>
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={series} margin={{ top: 5, right: 30, left: 5, bottom: 5 }}>
            <CartesianGrid stroke="#2a3441" strokeDasharray="3 3" />
            <XAxis dataKey={spec.x.field} type="number"
                   tickFormatter={spec.x.tick_suffix
                     ? (s) => `${s}${spec.x.tick_suffix}` : undefined}
                   tick={{ fill: "#8b949e", fontSize: 11 }} />
            {(spec.y_axes || []).map((ax) => {
              const max = ax.domain_max_field
                ? (resolveField(data, ax.domain_max_field) || 0) * (ax.domain_max_factor || 1)
                : ax.domain_max;
              return (
                <YAxis key={ax.id} yAxisId={ax.id}
                       orientation={ax.orientation || "left"}
                       domain={[ax.domain_min || 0, max]}
                       tick={{ fill: "#8b949e", fontSize: 11 }}
                       label={{ value: ax.label, angle: ax.orientation === "right" ? 90 : -90,
                                position: ax.orientation === "right" ? "insideRight" : "insideLeft",
                                fill: "#8b949e", fontSize: 10 }} />
              );
            })}
            <Tooltip contentStyle={{ background: "#11161e", border: "1px solid #2a3441" }} />
            {spec.legend !== false && (
              <Legend wrapperStyle={{ color: "#8b949e", fontSize: 11 }} />
            )}
            {(spec.reference_areas || []).map((ra, i) => {
              const x1 = ra.x1_value ?? resolveField(data, ra.x1_field);
              let x2 = ra.x2_value ?? resolveField(data, ra.x2_field);
              if (ra.x2_sum_fields) {
                x2 = ra.x2_sum_fields.reduce((s, f) => s + (resolveField(data, f) || 0), 0);
              }
              return (
                <ReferenceArea key={i} yAxisId={ra.y_axis_id}
                               x1={x1} x2={x2} fill={ra.fill}
                               fillOpacity={ra.opacity}
                               label={{ value: ra.label, position: "insideTop",
                                        fill: "var(--text-faint)", fontSize: 10 }} />
              );
            })}
            {(spec.reference_lines || []).map((rl, i) => {
              const y = rl.y_value ?? resolveField(data, rl.y_value_field);
              return (
                <ReferenceLine key={i} yAxisId={rl.y_axis_id} y={y}
                               stroke={rl.stroke} strokeDasharray={rl.stroke_dasharray}
                               label={{ value: rl.label, fill: rl.stroke, fontSize: 10 }} />
              );
            })}
            {(spec.lines || []).map((ln, i) => (
              <Line key={i} yAxisId={ln.y_axis_id}
                    type={ln.type || "monotone"}
                    dataKey={ln.field} stroke={ln.stroke}
                    dot={false} strokeWidth={ln.stroke_width || 2}
                    name={ln.name || ln.field} />
            ))}
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

// --- bar_chart ---
function CardBarChart(spec, data) {
  const series = resolveField(data, spec.data_path) || [];
  const accept = spec.acceptance_value ?? resolveField(data, spec.acceptance_field);
  return (
    <div className="card">
      <div className="card-h">{spec.title}</div>
      <div style={{ height: spec.height || 200 }}>
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={series}>
            <CartesianGrid stroke="#2a3441" strokeDasharray="3 3" />
            <XAxis dataKey={spec.x.field} tick={{ fill: "#8b949e", fontSize: 11 }} />
            <YAxis tick={{ fill: "#8b949e", fontSize: 11 }}
                   label={{ value: spec.y.label, angle: -90, position: "insideLeft",
                            fill: "#8b949e", fontSize: 10 }} />
            <Tooltip contentStyle={{ background: "#11161e", border: "1px solid #2a3441" }} />
            {accept !== undefined && (
              <ReferenceLine y={accept} stroke="var(--fail)" strokeDasharray="4 4"
                             label={{ value: `≤ ${accept}`, fill: "var(--fail)", fontSize: 10 }} />
            )}
            <Bar dataKey={spec.y.field}>
              {series.map((d, i) => {
                let color = spec.bar_default_color || "#58a6ff";
                if (spec.bar_color_logic) {
                  for (const r of spec.bar_color_logic) {
                    if (r.if_field !== undefined && resolveField(d, r.if_field) === r.if_value) {
                      color = r.color; break;
                    }
                  }
                }
                return <Cell key={i} fill={color} />;
              })}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

// --- duval_triangle ---
function DuvalCard(spec, data) {
  const g = resolveField(data, spec.data_path) || {};
  return (
    <div className="card">
      <div className="card-h">{spec.title || "Duval triangle"}</div>
      <div className="card-body">
        <Duval ch4={g.ch4 ?? 18} c2h4={g.c2h4 ?? 1.4} c2h2={g.c2h2 ?? 0.8} />
        <div style={{ fontFamily: "var(--font-mono)", fontSize: 11, marginTop: 8,
                      color: "var(--text-tertiary)" }}>
          CH₄ {Number(g.ch4 ?? 0).toFixed(1)} · C₂H₄ {Number(g.c2h4 ?? 0).toFixed(2)} ·
          C₂H₂ {Number(g.c2h2 ?? 0).toFixed(2)} ppm
        </div>
      </div>
    </div>
  );
}

function Duval({ ch4, c2h4, c2h2 }) {
  const total = ch4 + c2h4 + c2h2;
  if (total <= 0) return null;
  const a = ch4 / total, b = c2h4 / total, c = c2h2 / total;
  // Coords: triangle apex CH4 at top, C2H4 bottom-right, C2H2 bottom-left
  const W = 280, H = 240;
  const apex = [W / 2, 16];
  const right = [W - 16, H - 28];
  const left = [16, H - 28];
  const x = a * apex[0] + b * right[0] + c * left[0];
  const y = a * apex[1] + b * right[1] + c * left[1];
  return (
    <svg viewBox={`0 0 ${W} ${H}`} width={W} height={H}>
      <polygon points={`${apex[0]},${apex[1]} ${right[0]},${right[1]} ${left[0]},${left[1]}`}
               fill="none" stroke="var(--border-default)" />
      <text x={apex[0]} y={apex[1] - 4} textAnchor="middle"
            fontSize="11" fill="var(--text-tertiary)">CH₄</text>
      <text x={right[0]} y={right[1] + 14} textAnchor="end"
            fontSize="11" fill="var(--text-tertiary)">C₂H₄</text>
      <text x={left[0]} y={left[1] + 14} textAnchor="start"
            fontSize="11" fill="var(--text-tertiary)">C₂H₂</text>
      <circle cx={x} cy={y} r="5" fill="var(--fail)" stroke="var(--bg-base)" strokeWidth="2" />
    </svg>
  );
}

// --- ats_pipeline (3-phase commissioning sequence cards) ---
function AtsPipelineCard(spec, data) {
  const sequence = resolveField(data, spec.data_path) || [];
  const phases = clusterPhases(sequence);
  return (
    <div className="card">
      <div className="card-h">{spec.title || "Transfer timeline"}</div>
      <div className="ats-pipeline">
        {phases.map((p, i) => {
          const allPassed = p.steps.every((s) => s.passed);
          const range = p.startMs === p.endMs ? formatT(p.startMs)
            : `${formatT(p.startMs)} → ${formatT(p.endMs)}`;
          return (
            <React.Fragment key={i}>
              <div className={`ats-phase ${allPassed ? "ok" : "bad"}`}>
                <div className="ats-phase-head">
                  <span className="ats-phase-idx">{String(i + 1).padStart(2, "0")}</span>
                  <span className="ats-phase-name">{phaseTitle(p, i, phases.length)}</span>
                  <span className={`ats-phase-status ${allPassed ? "ok" : "bad"}`}>
                    {allPassed ? "PASS" : "FAIL"}
                  </span>
                </div>
                <div className="ats-phase-range">{range}</div>
                <ul className="ats-phase-steps">
                  {p.steps.map((s, j) => (
                    <li key={j} className={s.passed ? "ok" : "bad"}>
                      <span className="ats-step-t">{formatT(s.t_offset_ms)}</span>
                      <span className="ats-step-name">{s.step || s.name || ""}</span>
                    </li>
                  ))}
                </ul>
              </div>
              {i < phases.length - 1 && <div className="ats-phase-link" aria-hidden />}
            </React.Fragment>
          );
        })}
      </div>
    </div>
  );
}

function clusterPhases(sequence) {
  const phases = [];
  const boundaryMs = 60_000;
  for (const s of sequence) {
    const last = phases[phases.length - 1];
    if (!last || s.t_offset_ms - last.endMs >= boundaryMs) {
      phases.push({ steps: [s], startMs: s.t_offset_ms, endMs: s.t_offset_ms });
    } else {
      last.steps.push(s);
      last.endMs = s.t_offset_ms;
    }
  }
  return phases;
}

function phaseTitle(phase, index, totalPhases) {
  const names = phase.steps.map((s) => (s.step || "").toLowerCase());
  if (names.some((n) => n.includes("transfer to source"))) return "Transfer to alternate";
  if (names.some((n) => n.includes("return to utility")))  return "Return to utility";
  if (names.some((n) => n.includes("cooldown")))           return "Cooldown";
  if (index === 0) return "Pre-test";
  if (index === totalPhases - 1) return "Cooldown";
  return `Phase ${index + 1}`;
}

function formatT(ms) {
  if (ms === 0) return "T₀";
  if (ms < 1000) return `+${ms}ms`;
  if (ms < 60_000) return `+${(ms / 1000).toFixed(1)}s`;
  if (ms < 3_600_000) return `+${(ms / 60_000).toFixed(1)}min`;
  return `+${(ms / 3_600_000).toFixed(2)}h`;
}

// --- pi_curve ---
function PICurve(spec, data) {
  const pi = resolveField(data, spec.data_path) || { value: 0, curve: [], passed: false };
  return (
    <div className="card">
      <div className="card-h">
        Polarization Index · PI = {Number(pi.value || 0).toFixed(2)}
        <span className={`badge badge-${pi.passed ? "passed" : "failed"}`}
              style={{ marginLeft: 12 }}>
          {pi.passed ? "PASS" : "FAIL"}
        </span>
      </div>
      <div style={{ height: 220 }}>
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={pi.curve || []}>
            <CartesianGrid stroke="#2a3441" strokeDasharray="3 3" />
            <XAxis dataKey="minute" tick={{ fill: "#8b949e", fontSize: 11 }}
                   label={{ value: "minute", position: "insideBottom",
                            fill: "#8b949e", fontSize: 10 }} />
            <YAxis tick={{ fill: "#8b949e", fontSize: 11 }}
                   label={{ value: "MΩ", angle: -90, position: "insideLeft",
                            fill: "#8b949e", fontSize: 10 }} />
            <Tooltip contentStyle={{ background: "#11161e", border: "1px solid #2a3441" }} />
            <Line type="monotone" dataKey="resistance_mohm" stroke="#58a6ff"
                  strokeWidth={2} dot={false} />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

// --- gauge_row (for transformers + generators) ---
function GaugeRow(spec, data) {
  return (
    <div className="tile-row">
      {spec.gauges.map((g, i) => {
        const v = resolveField(data, g.value_field);
        const formatted = g.format ? applyFormat(v, g.format) : v;
        return (
          <div key={i} className="tile">
            <div className="label">{g.label}</div>
            <div className="value" style={{ fontSize: 18, fontFamily: "monospace" }}>
              {formatted ?? "—"}
            </div>
          </div>
        );
      })}
    </div>
  );
}

const CARD_RENDERERS = {
  kv_table: KvTable,
  table: CardTable,
  signoff_table: SignoffTable,
  line_chart: ChartCard,
  bar_chart: CardBarChart,
  duval: DuvalCard,
  ats_pipeline: AtsPipelineCard,
  pi_curve: PICurve,
  gauge_row: GaugeRow,
};

// ============================================================================
// No-prior-run state
// ============================================================================

function NoPriorRun({ id, category }) {
  return (
    <>
      <div className="page-header">
        <h1>{category} · {id}</h1>
        <span className="crumb">no prior commissioning on file</span>
      </div>
      <div className="card padded">
        <p style={{ color: "var(--text-secondary)" }}>
          No commissioning runs recorded for <code>{id}</code>.
        </p>
      </div>
    </>
  );
}
