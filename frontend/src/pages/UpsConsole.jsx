import React, { useEffect, useState } from "react";
import { useParams, Link } from "react-router-dom";
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip,
         ResponsiveContainer, ReferenceLine } from "recharts";
import { api } from "../hooks/useApi";

// UPS commissioning console (APC Symmetra MW 750 kW class).
//   - Live state, input/output V, battery %, load %, runtime estimate
//   - Battery transfer waveform — 60s output voltage trace showing the
//     dip-and-recover (spec §4.1 transfer test)
//   - Individual cell voltage bar chart with weak-cell flagging
//   - Transfer history table

export default function UpsConsole() {
  const { id } = useParams();
  const [rec, setRec] = useState(null);
  const [tel, setTel] = useState(null);

  useEffect(() => {
    api.get(`/equipment/ups/${id}`).then(setRec).catch(() => {});
    let alive = true;
    const tick = async () => {
      try { const d = await api.get("/telemetry"); if (alive) setTel(d); } catch (_) {}
    };
    tick();
    const h = setInterval(tick, 1000);
    return () => { alive = false; clearInterval(h); };
  }, [id]);

  if (!rec) return <p style={{ color: "var(--text-faint)" }}>Loading…</p>;
  const live = tel?.upses?.[id] || {};
  const minV = Math.min(...rec.transfer_waveform_60s.map(p => p.output_voltage));

  return (
    <>
      <div className="page-header">
        <h1>UPS · {id.toUpperCase()}</h1>
        <span className="crumb">
          <Link to="/sld" style={{ color: "var(--text-faint)" }}>SLD</Link>
          {" / "}{rec.manufacturer} {rec.model} · {rec.rated_kw} kW
        </span>
        <span className={`badge badge-${live.state === "online" ? "passed" : "queued"}`}
              style={{ marginLeft: "auto" }}>{(live.state || "—").toUpperCase()}</span>
      </div>

      <div className="tile-row">
        <Tile label="Input V"   value={live.input_voltage_v?.toFixed(1)}  unit="V" />
        <Tile label="Output V"  value={live.output_voltage_v?.toFixed(1)} unit="V" />
        <Tile label="Battery"   value={live.battery_pct?.toFixed(1)}      unit="%" color={live.battery_pct < 80 ? "var(--major)" : "var(--pass)"} />
        <Tile label="Runtime"   value={live.runtime_minutes?.toFixed(1)}  unit="min" />
        <Tile label="Load"      value={live.load_pct?.toFixed(1)}         unit="%" color={live.load_pct > 85 ? "var(--major)" : "var(--pass)"} />
        <Tile label="Weak cells" value={rec.weak_cells.length}            unit="" color={rec.weak_cells.length > 0 ? "var(--major)" : "var(--pass)"} />
      </div>

      <div className="card">
        <div className="card-h">
          Battery Transfer Waveform · last commissioning test
          <span style={{ marginLeft: 12, color: "var(--text-faint)", fontSize: 11 }}>
            min output {minV.toFixed(1)} V · acceptance ≥ 228 V (spec §4.1)
          </span>
        </div>
        <div style={{ height: 280 }}>
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={rec.transfer_waveform_60s.filter((_, i) => i % 2 === 0)}
                       margin={{ top: 5, right: 30, left: 5, bottom: 5 }}>
              <CartesianGrid stroke="#2a3441" strokeDasharray="3 3" />
              <XAxis dataKey="t_ms" type="number"
                     tickFormatter={(ms) => `${ms / 1000}s`}
                     tick={{ fill: "#8b949e", fontSize: 11 }} />
              <YAxis domain={[450, 490]} tick={{ fill: "#8b949e", fontSize: 11 }}
                     label={{ value: "V_out", angle: -90, position: "insideLeft", fill: "#8b949e", fontSize: 10 }} />
              <Tooltip contentStyle={{ background: "#11161e", border: "1px solid #2a3441" }} />
              <ReferenceLine y={460} stroke="var(--major)" strokeDasharray="4 4"
                             label={{ value: "dip floor 460V", fill: "var(--major)", fontSize: 10 }} />
              <ReferenceLine y={228} stroke="var(--fail)" strokeDasharray="4 4"
                             label={{ value: "abort threshold 228V", fill: "var(--fail)", fontSize: 10 }} />
              <Line type="monotone" dataKey="output_voltage" stroke="#58a6ff"
                    dot={false} strokeWidth={2} name="Output V" />
            </LineChart>
          </ResponsiveContainer>
        </div>
      </div>

      <div className="row">
        {/* Cell health */}
        <div className="card grow">
          <div className="card-h">
            Battery cells · {rec.cells_count} cells · {rec.weak_cells.length} weak
          </div>
          <CellChart cells={rec.all_cells} />
        </div>

        {/* Transfer history */}
        <div className="card" style={{ width: 420, padding: 0 }}>
          <div className="card-h">Transfer history</div>
          <table>
            <thead><tr><th>Days ago</th><th>Min V</th><th>Switchover</th><th>Status</th></tr></thead>
            <tbody>
              {rec.transfer_history.map((h, i) => (
                <tr key={i}>
                  <td className="mono">{h.date_offset_days}</td>
                  <td className="mono">{h.min_voltage_v.toFixed(1)} V</td>
                  <td className="mono">{h.switchover_ms.toFixed(1)} ms</td>
                  <td><span className={`badge badge-${h.passed ? "passed" : "failed"}`}>
                    {h.passed ? "passed" : "failed"}</span></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </>
  );
}

function Tile({ label, value, unit, color }) {
  return (
    <div className="tile">
      <div className="label">{label}</div>
      <div className="value" style={{ fontSize: 24, color: color || "var(--text)" }}>
        {value !== undefined ? value : "—"}
        <span style={{ fontSize: 12, color: "var(--text-faint)" }}> {unit}</span>
      </div>
    </div>
  );
}

function CellChart({ cells }) {
  const W = 1100, H = 220;
  const padTop = 20, padBottom = 30, padLeft = 36;
  const N = cells.length;
  const colW = (W - padLeft - 8) / N;
  const ymin = 3.30, ymax = 3.80;
  const yScale = (v) => padTop + (1 - (v - ymin) / (ymax - ymin)) * (H - padTop - padBottom);
  return (
    <svg viewBox={`0 0 ${W} ${H}`} width="100%" height={H}>
      {/* y axis */}
      {[3.40, 3.55, 3.65, 3.75].map((v) => (
        <g key={v}>
          <line x1={padLeft} x2={W - 8} y1={yScale(v)} y2={yScale(v)}
                stroke="var(--border)" strokeWidth="0.7" />
          <text x={padLeft - 6} y={yScale(v) + 4} fontSize="9" fill="var(--text-faint)" textAnchor="end">
            {v.toFixed(2)}
          </text>
        </g>
      ))}
      <line x1={padLeft} x2={W - 8} y1={yScale(3.55)} y2={yScale(3.55)}
            stroke="var(--major)" strokeDasharray="4 4" />
      <text x={W - 8} y={yScale(3.55) - 4} fontSize="9" fill="var(--major)" textAnchor="end">
        weak threshold 3.55V
      </text>
      {cells.map((c, i) => {
        const x = padLeft + i * colW;
        const y = yScale(c.voltage);
        const h = Math.max(1, H - padBottom - y);
        return <rect key={c.id} x={x} y={y} width={Math.max(1, colW - 0.5)} height={h}
                     fill={c.ok ? "var(--accent)" : "var(--fail)"}
                     fillOpacity={c.ok ? 0.85 : 1} />;
      })}
      <text x={padLeft} y={H - 8} fontSize="10" fill="var(--text-faint)">
        cell #
      </text>
    </svg>
  );
}
