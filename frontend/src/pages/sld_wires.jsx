// Wire-routing helpers. Right-angle paths between two anchor points.

import React from "react";
import { anchor } from "./sld_layout";

// Right-angle path: go vertical then horizontal then vertical (or vice versa).
function pathVHV(from, to) {
  const midY = (from.y + to.y) / 2;
  return `M ${from.x} ${from.y} L ${from.x} ${midY} L ${to.x} ${midY} L ${to.x} ${to.y}`;
}
function pathHVH(from, to) {
  const midX = (from.x + to.x) / 2;
  return `M ${from.x} ${from.y} L ${midX} ${from.y} L ${midX} ${to.y} L ${to.x} ${to.y}`;
}

// A wire is { from, to, fromSide, toSide, live, route }.
//   route: "vhv" (default — vertical first) or "hvh"
export function Wire({ from, fromSide, to, toSide, live = true, fault = false, route = "vhv" }) {
  const a = anchor(from, fromSide);
  const b = anchor(to, toSide);
  const d = route === "hvh" ? pathHVH(a, b) : pathVHV(a, b);
  const cls = fault ? "wire-fault" : (live ? "wire-live" : "wire-dead");
  return <path d={d} className={cls} />;
}

export function StraightWire({ x1, y1, x2, y2, live = true }) {
  return (
    <path d={`M ${x1} ${y1} L ${x2} ${y2}`}
          className={live ? "wire-live" : "wire-dead"} />
  );
}
