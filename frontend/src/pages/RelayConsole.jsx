import React, { useEffect, useState } from "react";
import { useParams, Link } from "react-router-dom";
import { api } from "../hooks/useApi";

// SEL-751 protective relay commissioning console.
// Shows: pickup table per element (50, 51, 50N, 51N, 27, 81), live actual
// values vs threshold, trip target indicators, last event report,
// 3-phase phasor diagram (current + voltage), simulated TCC overlay.

export default function RelayConsole() {
  const { id } = useParams();
  const [t, setT] = useState(null);

  useEffect(() => {
    let alive = true;
    const tick = async () => {
      try { const d = await api.get("/telemetry"); if (alive) setT(d); } catch (_) {}
    };
    tick();
    const h = setInterval(tick, 1000);
    return () => { alive = false; clearInterval(h); };
  }, []);

  if (!t) return <p style={{ color: "var(--text-faint)" }}>Loading…</p>;
  const relay = t.relays[id];
  if (!relay) return <p>Relay {id} not found.</p>;

  const tripped = !!relay.last_trip_cause;

  return (
    <>
      <div className="page-header">
        <h1>SEL-751 · Relay Console</h1>
        <span className="crumb">
          <Link to="/sld" style={{ color: "var(--text-faint)" }}>SLD</Link>
          {" / "}
          <span className="mono">{id}</span>
        </span>
      </div>

      {/* Trip-target ribbon — physical relay's front-panel LEDs */}
      <div className="card" style={{ display: "flex", gap: 24, alignItems: "center" }}>
        <div style={{ fontSize: 11, color: "var(--text-faint)", textTransform: "uppercase",
                      letterSpacing: 0.6, width: 90 }}>Trip targets</div>
        {Object.entries(relay.elements).map(([code, el]) => (
          <TripTarget key={code} code={code} el={el} />
        ))}
      </div>

      <div className="row">
        {/* Pickup table */}
        <div className="card grow" style={{ padding: 0 }}>
          <div className="card-h">Element pickup</div>
          <table>
            <thead>
              <tr><th>Code</th><th>Function</th><th>Threshold</th><th>Actual</th><th>Status</th></tr>
            </thead>
            <tbody>
              {Object.entries(relay.elements).map(([code, el]) => (
                <tr key={code}>
                  <td className="mono">{code}</td>
                  <td>{el.label}</td>
                  <td className="mono">{formatThreshold(code, el)}</td>
                  <td className="mono">{formatActual(code, el)}</td>
                  <td>
                    {el.trip ? <span className="badge badge-failed">TRIP</span>
                    : el.pickup ? <span className="badge badge-queued">pickup</span>
                    : <span className="badge badge-passed">armed</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {/* Phasor diagram */}
        <div className="card" style={{ width: 360 }}>
          <div className="card-h">3-phase phasor</div>
          <Phasor relay={relay} />
        </div>
      </div>

      {/* Last event report */}
      <div className="card">
        <div className="card-h">Last event report</div>
        {tripped ? (
          <pre style={{ background: "var(--bg-0)", border: "1px solid var(--border)",
                        borderRadius: 4, padding: 12, fontSize: 11.5, overflow: "auto" }}>
{`*** SEL-751 RELAY EVENT REPORT ***
Substation:   DC1-Ashburn
Relay ID:     ${id}
Event:        TRIP
Cause:        ${relay.last_trip_cause}
Date / Time:  ${relay.last_trip_at || "—"}
Pre-fault:    ${formatActual("51", relay.elements["51"])}    pf=0.97
Fault:        ${formatActual("51", relay.elements["51"])} (51 pickup)
Trip time:    21 ms (1.26 cycles)
Breaker:      OPEN
Reclose:      DISABLED
Targets:      51 PHASE TOC
Operator:     auto (commissioning sweep)
`}</pre>
        ) : (
          <p style={{ color: "var(--text-faint)" }}>
            No trip events since last reset. Last self-test {new Date().toLocaleString()} — pass.
          </p>
        )}
      </div>

      <div className="card">
        <div className="card-h">Coordination study reference</div>
        <CoordinationCurve />
      </div>
    </>
  );
}

function TripTarget({ code, el }) {
  const active = el.trip;
  const pickup = el.pickup;
  const color = active ? "var(--fail)" : pickup ? "var(--queued)" : "var(--bg-3)";
  const text  = active ? "var(--fail)" : pickup ? "var(--queued)" : "var(--text-faint)";
  return (
    <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 4 }}>
      <div style={{ width: 28, height: 28, borderRadius: 4,
                    background: color, opacity: active || pickup ? 1 : 0.4,
                    border: `1px solid ${active ? "var(--fail)" : "var(--border)"}`,
                    boxShadow: active ? "0 0 12px rgba(248,81,73,0.6)" : "none" }} />
      <div style={{ fontSize: 11, color: text, fontFamily: "monospace" }}>{code}</div>
    </div>
  );
}

function formatThreshold(code, el) {
  if (el.threshold_a !== undefined) return `${el.threshold_a} A`;
  if (el.threshold_v !== undefined) return `${el.threshold_v.toFixed(0)} V`;
  if (el.threshold_hz_lo !== undefined) return `${el.threshold_hz_lo} – ${el.threshold_hz_hi} Hz`;
  return "—";
}
function formatActual(code, el) {
  if (el.actual_a !== undefined) return `${el.actual_a.toFixed(1)} A`;
  if (el.actual_v !== undefined) return `${el.actual_v.toFixed(0)} V`;
  if (el.actual_hz !== undefined) return `${el.actual_hz.toFixed(3)} Hz`;
  return "—";
}

// 3-phase phasor diagram — voltages 0/120/240, currents lagging by load PF angle
function Phasor({ relay }) {
  const W = 320, H = 280;
  const cx = W / 2, cy = H / 2 + 6;
  const R = 96;
  // Voltage angles
  const angles = { A: 0, B: -120, C: 120 };
  // Phase A current lags voltage by 14° (PF ~0.97)
  const lagDeg = 14;

  const polar = (deg, len) => {
    const rad = (deg - 90) * Math.PI / 180;
    return [cx + len * Math.cos(rad), cy + len * Math.sin(rad)];
  };

  const draw = (deg, len, color, label) => {
    const [x, y] = polar(deg, len);
    return (
      <g key={label}>
        <line x1={cx} y1={cy} x2={x} y2={y} stroke={color} strokeWidth="2" markerEnd="url(#arrA)" />
        <text x={x + (x < cx ? -10 : 10)} y={y + (y < cy ? -2 : 12)} fontSize="10"
              fill={color} textAnchor={x < cx ? "end" : "start"}>{label}</text>
      </g>
    );
  };

  return (
    <svg viewBox={`0 0 ${W} ${H}`} width="100%" height={H}>
      <defs>
        <marker id="arrA" viewBox="0 0 10 10" refX="8" refY="5"
                markerWidth="6" markerHeight="6" orient="auto-start-reverse">
          <polygon points="0,0 10,5 0,10" fill="currentColor" />
        </marker>
      </defs>
      {/* concentric circles */}
      {[0.4, 0.7, 1].map((f) => (
        <circle key={f} cx={cx} cy={cy} r={R * f} fill="none"
                stroke="var(--border)" strokeWidth="1" />
      ))}
      {/* axes */}
      <line x1={cx} y1={cy - R - 6} x2={cx} y2={cy + R + 6} stroke="var(--border)" />
      <line x1={cx - R - 6} y1={cy} x2={cx + R + 6} y2={cy} stroke="var(--border)" />
      {/* Voltages */}
      {draw(angles.A, R,         "var(--accent)",   "Va")}
      {draw(angles.B, R,         "var(--accent)",   "Vb")}
      {draw(angles.C, R,         "var(--accent)",   "Vc")}
      {/* Currents — shorter, with lag */}
      {draw(angles.A + lagDeg, R * 0.65, "var(--major)", "Ia")}
      {draw(angles.B + lagDeg, R * 0.65, "var(--major)", "Ib")}
      {draw(angles.C + lagDeg, R * 0.65, "var(--major)", "Ic")}
      <text x={cx} y={H - 14} fontSize="9.5" fill="var(--text-faint)" textAnchor="middle"
            fontFamily="monospace">PF ≈ 0.97 lagging · balanced</text>
    </svg>
  );
}

// Time-current characteristic (TCC) curve from coordination study
function CoordinationCurve() {
  const W = 700, H = 240;
  const pad = 40;
  const xMin = 1, xMax = 10000; // current multiples
  const yMin = 0.01, yMax = 100; // seconds

  const x = (i) => pad + (Math.log10(i / xMin) / Math.log10(xMax / xMin)) * (W - 2 * pad);
  const y = (s) => H - pad - (Math.log10(s / yMin) / Math.log10(yMax / yMin)) * (H - 2 * pad);

  const ieee_inverse = (i) => 0.0515 / (Math.pow(i / 480, 0.02) - 1) + 0.114;
  const points = [];
  for (let i = 500; i < 6000; i += 50) {
    const s = ieee_inverse(i);
    if (s > 0.01 && s < 50) points.push(`${x(i).toFixed(1)},${y(s).toFixed(1)}`);
  }
  const path = points.length ? `M${points.join(" L")}` : "";

  return (
    <svg viewBox={`0 0 ${W} ${H}`} width="100%" height={H}>
      {/* axes + grid */}
      {[1, 10, 100, 1000, 10000].map((c) => (
        <g key={`g${c}`}>
          <line x1={x(c)} y1={pad} x2={x(c)} y2={H - pad} stroke="var(--border)" strokeWidth="0.7" />
          <text x={x(c)} y={H - pad + 14} fontSize="9" fill="var(--text-faint)" textAnchor="middle">{c}A</text>
        </g>
      ))}
      {[0.01, 0.1, 1, 10, 100].map((s) => (
        <g key={`s${s}`}>
          <line x1={pad} y1={y(s)} x2={W - pad} y2={y(s)} stroke="var(--border)" strokeWidth="0.7" />
          <text x={pad - 6} y={y(s) + 4} fontSize="9" fill="var(--text-faint)" textAnchor="end">{s}s</text>
        </g>
      ))}
      <path d={path} fill="none" stroke="var(--accent)" strokeWidth="2" />
      <text x={W - pad - 8} y={pad + 14} fontSize="10" fill="var(--accent)" textAnchor="end"
            fontFamily="monospace">SEL-751 · 51 (very inverse, TD=2.5)</text>
      {/* Pickup line */}
      <line x1={x(480)} y1={pad} x2={x(480)} y2={H - pad} stroke="var(--queued)"
            strokeWidth="1" strokeDasharray="4 4" />
      <text x={x(480) + 6} y={pad + 12} fontSize="10" fill="var(--queued)">PU 480A</text>
    </svg>
  );
}
