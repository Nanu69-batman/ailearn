// Edge routing for the developer-view diagram.
//
// An edge leaves its source box from a side and enters its target box from a
// side -- never from the centre -- and must not pass behind any other box.
// A smooth curve is used when it is clear; otherwise the best clear
// right-angle route (fewest bends, then shortest) is chosen. Re-run on every
// layout change, so a dragged box re-routes the lines around it.

import { NODE_H, NODE_W, VIEW_H, VIEW_W } from "./flows";

export interface Pt {
  x: number;
  y: number;
}

interface Rect {
  l: number;
  r: number;
  t: number;
  b: number;
}

/** Clearance a line keeps from a box it does not belong to. */
const CLEAR = 10;
/** Straight run out of (and into) a box before the first bend. */
const STUB = 16;
const RADIUS = 10;
const BEND_COST = 60;

function rectOf(c: Pt, pad: number): Rect {
  return {
    l: c.x - NODE_W / 2 - pad,
    r: c.x + NODE_W / 2 + pad,
    t: c.y - NODE_H / 2 - pad,
    b: c.y + NODE_H / 2 + pad,
  };
}

/** Liang-Barsky: does segment p-q cross the interior of rect? */
function segHits(p: Pt, q: Pt, r: Rect): boolean {
  let t0 = 0;
  let t1 = 1;
  const dx = q.x - p.x;
  const dy = q.y - p.y;
  const checks: Array<[number, number]> = [
    [-dx, p.x - r.l],
    [dx, r.r - p.x],
    [-dy, p.y - r.t],
    [dy, r.b - p.y],
  ];
  for (const [pp, qq] of checks) {
    if (pp === 0) {
      if (qq <= 0) return false;
    } else {
      const t = qq / pp;
      if (pp < 0) {
        if (t > t1) return false;
        if (t > t0) t0 = t;
      } else {
        if (t < t0) return false;
        if (t < t1) t1 = t;
      }
    }
  }
  return t1 - t0 > 1e-6;
}

type Side = "l" | "r" | "t" | "b";
const DIR: Record<Side, Pt> = { l: { x: -1, y: 0 }, r: { x: 1, y: 0 }, t: { x: 0, y: -1 }, b: { x: 0, y: 1 } };

function port(c: Pt, s: Side): Pt {
  if (s === "l") return { x: c.x - NODE_W / 2, y: c.y };
  if (s === "r") return { x: c.x + NODE_W / 2, y: c.y };
  if (s === "t") return { x: c.x, y: c.y - NODE_H / 2 };
  return { x: c.x, y: c.y + NODE_H / 2 };
}

function simplify(pts: Pt[]): Pt[] {
  const out: Pt[] = [];
  for (const p of pts) {
    const last = out[out.length - 1];
    if (last && Math.abs(last.x - p.x) < 0.01 && Math.abs(last.y - p.y) < 0.01) continue;
    out.push(p);
    while (out.length >= 3) {
      const [a, b, c] = out.slice(-3);
      const collinear = (a.x === b.x && b.x === c.x) || (a.y === b.y && b.y === c.y);
      if (!collinear) break;
      out.splice(out.length - 2, 1);
    }
  }
  return out;
}

function length(pts: Pt[]): number {
  let s = 0;
  for (let i = 1; i < pts.length; i++) s += Math.abs(pts[i].x - pts[i - 1].x) + Math.abs(pts[i].y - pts[i - 1].y);
  return s;
}

function inView(p: Pt): boolean {
  return p.x >= 4 && p.x <= VIEW_W - 4 && p.y >= 4 && p.y <= VIEW_H - 4;
}

/** Polyline with rounded corners, as an SVG path. */
function roundedPath(pts: Pt[]): string {
  if (pts.length < 2) return "";
  let d = `M ${pts[0].x} ${pts[0].y}`;
  for (let i = 1; i < pts.length - 1; i++) {
    const p = pts[i - 1];
    const c = pts[i];
    const n = pts[i + 1];
    const lin = Math.hypot(c.x - p.x, c.y - p.y);
    const lout = Math.hypot(n.x - c.x, n.y - c.y);
    const r = Math.min(RADIUS, lin / 2, lout / 2);
    const a = { x: c.x - ((c.x - p.x) / lin) * r, y: c.y - ((c.y - p.y) / lin) * r };
    const b = { x: c.x + ((n.x - c.x) / lout) * r, y: c.y + ((n.y - c.y) / lout) * r };
    d += ` L ${a.x} ${a.y} Q ${c.x} ${c.y} ${b.x} ${b.y}`;
  }
  const last = pts[pts.length - 1];
  return `${d} L ${last.x} ${last.y}`;
}

const CURVE_PAIRS: Array<[Side, Side]> = [
  ["r", "l"],
  ["b", "t"],
  ["t", "b"],
  ["r", "t"],
  ["r", "b"],
  ["b", "l"],
  ["t", "l"],
];

const PAIRS: Array<[Side, Side]> = [
  ["r", "l"],
  ["b", "t"],
  ["t", "b"],
  ["r", "t"],
  ["r", "b"],
  ["b", "l"],
  ["t", "l"],
  ["b", "b"],
  ["t", "t"],
  ["l", "r"],
  ["l", "l"],
  ["r", "r"],
];

const XS = Array.from({ length: Math.floor(VIEW_W / 16) }, (_, i) => 8 + i * 16);
const YS = Array.from({ length: Math.floor(VIEW_H / 12) }, (_, i) => 6 + i * 12);

/**
 * The path for an edge from box `a` to box `b`, avoiding `others` (every
 * other box centre). Always returns a path; a clear one whenever one exists.
 */
export interface Box {
  l: number;
  r: number;
  t: number;
  b: number;
}

export function routeEdge(a: Pt, b: Pt, others: Pt[], keepOut: Box[] = []): string {
  const blockers: Rect[] = [...others.map((o) => rectOf(o, CLEAR)), ...keepOut];
  const own = [rectOf(a, 2), rectOf(b, 2)];

  // 1. The shortest clear smooth curve, leaving and entering along a side.
  let bestCurve: string | null = null;
  let bestLen = Infinity;
  for (const [sa, sb] of CURVE_PAIRS) {
    const p = port(a, sa);
    const q = port(b, sb);
    // Only sides that face each other: a curve must not double back.
    const toward = (q.x - p.x) * DIR[sa].x + (q.y - p.y) * DIR[sa].y;
    const from = (p.x - q.x) * DIR[sb].x + (p.y - q.y) * DIR[sb].y;
    if (toward < 20 || from < 20) continue;
    const k = Math.max(30, Math.hypot(q.x - p.x, q.y - p.y) * 0.42);
    const c1 = { x: p.x + DIR[sa].x * k, y: p.y + DIR[sa].y * k };
    const c2 = { x: q.x + DIR[sb].x * k, y: q.y + DIR[sb].y * k };
    let prev = p;
    let len = 0;
    let clear = true;
    for (let i = 1; i <= 40 && clear; i++) {
      const t = i / 40;
      const u = 1 - t;
      const pt = {
        x: u * u * u * p.x + 3 * u * u * t * c1.x + 3 * u * t * t * c2.x + t * t * t * q.x,
        y: u * u * u * p.y + 3 * u * u * t * c1.y + 3 * u * t * t * c2.y + t * t * t * q.y,
      };
      if (blockers.some((r) => segHits(prev, pt, r))) clear = false;
      else if (i > 3 && i < 37 && own.some((r) => segHits(prev, pt, r))) clear = false;
      len += Math.hypot(pt.x - prev.x, pt.y - prev.y);
      prev = pt;
    }
    // Side-to-side reads best left to right; give it a small edge.
    if (sa === "r" && sb === "l") len -= 30;
    if (clear && len < bestLen) {
      bestLen = len;
      bestCurve = `M ${p.x} ${p.y} C ${c1.x} ${c1.y}, ${c2.x} ${c2.y}, ${q.x} ${q.y}`;
    }
  }
  if (bestCurve) return bestCurve;

  // 2. The best clear right-angle route over every port pair.
  let best: Pt[] | null = null;
  let bestScore = Infinity;
  for (const [sa, sb] of PAIRS) {
    const p = port(a, sa);
    const q = port(b, sb);
    const p1 = { x: p.x + DIR[sa].x * STUB, y: p.y + DIR[sa].y * STUB };
    const q1 = { x: q.x + DIR[sb].x * STUB, y: q.y + DIR[sb].y * STUB };
    const middles: Pt[][] = [
      [{ x: q1.x, y: p1.y }],
      [{ x: p1.x, y: q1.y }],
      ...XS.map((x) => [{ x, y: p1.y }, { x, y: q1.y }]),
      ...YS.map((y) => [{ x: p1.x, y }, { x: q1.x, y }]),
    ];
    for (const mid of middles) {
      const pts = simplify([p, p1, ...mid, q1, q]);
      if (!pts.every(inView)) continue;
      const bends = pts.length - 2;
      const score = length(pts) + bends * BEND_COST;
      if (score >= bestScore) continue;
      let ok = true;
      for (let i = 1; i < pts.length && ok; i++) {
        const s0 = pts[i - 1];
        const s1 = pts[i];
        if (blockers.some((r) => segHits(s0, s1, r))) ok = false;
        // Not back through its own boxes, except the stub out and the stub in.
        else if (i > 1 && segHits(s0, s1, own[0])) ok = false;
        else if (i < pts.length - 1 && segHits(s0, s1, own[1])) ok = false;
      }
      if (ok) {
        best = pts;
        bestScore = score;
      }
    }
  }
  if (best) return roundedPath(best);

  // 3. Nothing is clear (boxes dragged on top of each other): go direct.
  const p = port(a, "r");
  const q = port(b, "l");
  return `M ${p.x} ${p.y} L ${q.x} ${q.y}`;
}
