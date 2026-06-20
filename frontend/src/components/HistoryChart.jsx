// HistoryChart — dependency-free inline SVG line chart for register history
// (points: [{ timestamp, value }]) returned by GET /devices/:id/history.
// Kept deliberately minimal so the bundle only needs react/react-dom/router.
import { useMemo } from "react";
import { EmptyState, fmtTime, fmtNumber } from "./ui.jsx";

const W = 640;
const H = 220;
const PAD = { top: 16, right: 16, bottom: 28, left: 48 };

export default function HistoryChart({ points, label }) {
  const model = useMemo(() => {
    const valid = (points || [])
      .map((p) => ({ t: new Date(p.timestamp).getTime(), v: Number(p.value) }))
      .filter((p) => !Number.isNaN(p.t) && !Number.isNaN(p.v))
      .sort((a, b) => a.t - b.t);
    if (valid.length === 0) return null;

    const tMin = valid[0].t;
    const tMax = valid[valid.length - 1].t;
    let vMin = Math.min(...valid.map((p) => p.v));
    let vMax = Math.max(...valid.map((p) => p.v));
    if (vMin === vMax) {
      vMin -= 1;
      vMax += 1;
    }
    const innerW = W - PAD.left - PAD.right;
    const innerH = H - PAD.top - PAD.bottom;
    const x = (t) => PAD.left + (tMax === tMin ? 0 : ((t - tMin) / (tMax - tMin)) * innerW);
    const y = (v) => PAD.top + innerH - ((v - vMin) / (vMax - vMin)) * innerH;

    const d = valid
      .map((p, i) => `${i === 0 ? "M" : "L"}${x(p.t).toFixed(1)},${y(p.v).toFixed(1)}`)
      .join(" ");

    return { valid, tMin, tMax, vMin, vMax, x, y, d, innerH };
  }, [points]);

  if (!model) {
    return <EmptyState title="No data points" detail="No history in the selected window." />;
  }

  const { valid, vMin, vMax, x, y, d, tMin, tMax } = model;
  const last = valid[valid.length - 1];

  return (
    <div className="chart">
      <div className="chart-head">
        <span className="muted small">{valid.length} points</span>
        <span className="chart-latest">
          {label}: <strong>{fmtNumber(last.v)}</strong>
        </span>
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} className="chart-svg" preserveAspectRatio="none">
        {/* y gridlines */}
        {[0, 0.5, 1].map((f) => {
          const v = vMin + (vMax - vMin) * f;
          return (
            <g key={f}>
              <line
                x1={PAD.left}
                x2={W - PAD.right}
                y1={y(v)}
                y2={y(v)}
                className="chart-grid"
              />
              <text x={4} y={y(v) + 4} className="chart-axis">
                {fmtNumber(v, 1)}
              </text>
            </g>
          );
        })}
        {/* x endpoints */}
        <text x={PAD.left} y={H - 8} className="chart-axis">
          {fmtTime(new Date(tMin).toISOString())}
        </text>
        <text x={W - PAD.right} y={H - 8} className="chart-axis" textAnchor="end">
          {fmtTime(new Date(tMax).toISOString())}
        </text>
        <path d={d} className="chart-line" />
        <circle cx={x(last.t)} cy={y(last.v)} r="3" className="chart-point" />
      </svg>
    </div>
  );
}
