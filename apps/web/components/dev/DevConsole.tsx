"use client";

// Developer view: a live, animated picture of what the API is doing.
//
// Polls GET /dev/trace (see `apps/api/app/devtrace.py`) for step events and
// plays them onto a swimlane diagram -- one per flow (ingestion, AI tutor,
// quiz). Each event moves a packet from the previous step to this one, lights
// the node, and lands its data in the inspector. Events that arrive in one
// poll are queued and played one by one, so a fast answer is still readable.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  DIAGRAMS,
  LANES,
  NODE_H,
  NODE_W,
  TAB_LABEL,
  VIEW_H,
  VIEW_W,
  curve,
  flowTab,
  pointOn,
  type DiagramNode,
  type FlowTab,
} from "./flows";
import { DataView, fmtMs } from "./DataView";

export interface TraceEvent {
  seq: number;
  ts: number;
  run_id: string;
  flow: string;
  course_id: string | null;
  step: string;
  phase: "start" | "end" | "error" | "info" | "progress";
  title: string;
  where: string;
  data: Record<string, unknown>;
  ms: number | null;
}

type NodeStatus = "idle" | "active" | "done" | "error";

interface NodeState {
  status: NodeStatus;
  count: number;
  ms: number | null;
  events: TraceEvent[];
}

interface RunState {
  run_id: string;
  flow: string;
  startedAt: number;
  endedAt: number | null;
  status: "running" | "done" | "error";
  title: string;
  nodes: Record<string, NodeState>;
  events: TraceEvent[];
  last: string | null;
  trail: string[];
  lastEdge: string | null;
}

interface Packet {
  id: number;
  from: DiagramNode;
  to: DiagramNode;
  color: string;
}

type WhereMap = Record<string, string>;

const POLL_MS = 800;
const STEP_MS = 560;
const FAST_STEP_MS = 90;
const MAX_RUNS = 60;

function apiBase(): string {
  return (process.env.NEXT_PUBLIC_API_URL ?? "").replace(/\/$/, "");
}

function emptyRun(ev: TraceEvent): RunState {
  return {
    run_id: ev.run_id,
    flow: ev.flow,
    startedAt: ev.ts,
    endedAt: null,
    status: "running",
    title: ev.title,
    nodes: {},
    events: [],
    last: null,
    trail: [],
    lastEdge: null,
  };
}

/** Fold one event into its run. Pure: returns a new run object. */
function applyEvent(run: RunState, ev: TraceEvent): RunState {
  const prev = run.nodes[ev.step] ?? { status: "idle" as NodeStatus, count: 0, ms: null, events: [] };
  const node: NodeState = { ...prev, events: [...prev.events, ev].slice(-40) };
  if (ev.phase === "start") {
    node.status = "active";
    node.count += 1;
  } else if (ev.phase === "progress") {
    node.status = "active";
  } else if (ev.phase === "error") {
    node.status = "error";
    if (prev.status !== "active") node.count += 1;
    node.ms = ev.ms;
  } else {
    if (prev.status !== "active") node.count += 1;
    node.status = "done";
    node.ms = ev.ms ?? prev.ms;
  }
  const next: RunState = {
    ...run,
    nodes: { ...run.nodes, [ev.step]: node },
    events: [...run.events, ev],
  };
  if (run.last && run.last !== ev.step) {
    const edge = `${run.last}>${ev.step}`;
    next.lastEdge = edge;
    if (!run.trail.includes(edge)) next.trail = [...run.trail, edge];
  }
  next.last = ev.step;
  if (ev.step === "request" && ev.phase === "start") next.title = ev.title;
  if (ev.step === "respond") {
    next.endedAt = ev.ts;
    next.status = ev.phase === "error" ? "error" : "done";
  }
  return next;
}

function statusColor(s: NodeStatus): string {
  if (s === "active") return "#a78bfa";
  if (s === "done") return "#34d399";
  if (s === "error") return "#f87171";
  return "#3f3f46";
}

function laneOf(where: string): string {
  return where.split(" · ")[0];
}

export default function DevConsole() {
  const [runs, setRuns] = useState<Record<string, RunState>>({});
  const [order, setOrder] = useState<string[]>([]);
  const [tab, setTab] = useState<FlowTab>("chat");
  const [selectedRun, setSelectedRun] = useState<Record<FlowTab, string | null>>({
    ingest: null,
    chat: null,
    quiz: null,
  });
  const [selectedStep, setSelectedStep] = useState<string | null>(null);
  const [follow, setFollow] = useState(true);
  const [where, setWhere] = useState<WhereMap>({});
  const [agentOn, setAgentOn] = useState(false);
  const [conn, setConn] = useState<"connecting" | "live" | "offline" | "disabled" | "unconfigured">(
    "connecting",
  );
  const [packets, setPackets] = useState<Packet[]>([]);
  const [queued, setQueued] = useState(0);

  const cursor = useRef(0);
  const queue = useRef<TraceEvent[]>([]);
  const runsRef = useRef(runs);
  runsRef.current = runs;
  const followRef = useRef(follow);
  followRef.current = follow;
  const packetId = useRef(0);
  const primed = useRef(false);

  const touchOrder = useCallback((runId: string) => {
    setOrder((o) => (o.includes(runId) ? o : [runId, ...o].slice(0, MAX_RUNS)));
  }, []);

  // Apply one event with animation: move a packet, light the node.
  const play = useCallback(
    (ev: TraceEvent) => {
      const current = runsRef.current[ev.run_id] ?? emptyRun(ev);
      const updated = applyEvent(current, ev);
      const t = flowTab(ev.flow);
      const diagram = DIAGRAMS[t];
      if (current.last && current.last !== ev.step) {
        const from = diagram.nodes.find((n) => n.id === current.last);
        const to = diagram.nodes.find((n) => n.id === ev.step);
        if (from && to) {
          const id = ++packetId.current;
          const color = ev.phase === "error" ? "#f87171" : "#c4b5fd";
          setPackets((p) => [...p, { id, from, to, color }]);
          window.setTimeout(() => setPackets((p) => p.filter((x) => x.id !== id)), 900);
        }
      }
      runsRef.current = { ...runsRef.current, [ev.run_id]: updated };
      setRuns(runsRef.current);
      touchOrder(ev.run_id);
      if (followRef.current && ev.step === "request" && ev.phase === "start") {
        setTab(t);
        setSelectedRun((s) => ({ ...s, [t]: ev.run_id }));
        setSelectedStep(null);
      }
    },
    [touchOrder],
  );

  // Fold history in without animation (first load).
  const absorb = useCallback(
    (events: TraceEvent[]) => {
      let next = { ...runsRef.current };
      const latestByTab: Partial<Record<FlowTab, string>> = {};
      for (const ev of events) {
        next = { ...next, [ev.run_id]: applyEvent(next[ev.run_id] ?? emptyRun(ev), ev) };
        latestByTab[flowTab(ev.flow)] = ev.run_id;
      }
      runsRef.current = next;
      setRuns(next);
      const ids = Object.values(next)
        .sort((a, b) => b.startedAt - a.startedAt)
        .map((r) => r.run_id)
        .slice(0, MAX_RUNS);
      setOrder(ids);
      setSelectedRun((s) => ({
        ingest: s.ingest ?? latestByTab.ingest ?? null,
        chat: s.chat ?? latestByTab.chat ?? null,
        quiz: s.quiz ?? latestByTab.quiz ?? null,
      }));
      const newest = events[events.length - 1];
      if (newest) setTab(flowTab(newest.flow));
    },
    [],
  );

  // Poll loop.
  useEffect(() => {
    const base = apiBase();
    if (!base) {
      setConn("unconfigured");
      return;
    }
    let stopped = false;
    let timer: number | undefined;
    const tick = async () => {
      try {
        const res = await fetch(`${base}/dev/trace?after=${cursor.current}`, { cache: "no-store" });
        if (!res.ok) throw new Error(String(res.status));
        const body = (await res.json()) as {
          enabled: boolean;
          events: TraceEvent[];
          latest: number;
          where: WhereMap;
          agent: boolean;
        };
        if (stopped) return;
        setWhere(body.where ?? {});
        setAgentOn(Boolean(body.agent));
        setConn(body.enabled ? "live" : "disabled");
        if (body.latest < cursor.current) {
          // The API restarted and its sequence began again.
          cursor.current = 0;
        } else if (body.events.length) {
          cursor.current = body.events[body.events.length - 1].seq;
          if (!primed.current) absorb(body.events);
          else {
            queue.current.push(...body.events);
            setQueued(queue.current.length);
          }
        }
        primed.current = true;
      } catch {
        if (!stopped) setConn("offline");
      }
      if (!stopped) timer = window.setTimeout(tick, POLL_MS);
    };
    tick();
    return () => {
      stopped = true;
      if (timer) window.clearTimeout(timer);
    };
  }, [absorb]);

  // Player: one queued event per beat; hurry when the queue backs up.
  useEffect(() => {
    let timer: number | undefined;
    let stopped = false;
    const beat = () => {
      const q = queue.current;
      let delay = STEP_MS;
      if (q.length) {
        let ev = q.shift()!;
        // Collapse runs of embedding progress into the newest one.
        while (ev.phase === "progress" && q[0]?.phase === "progress" && q[0].run_id === ev.run_id) {
          ev = q.shift()!;
        }
        play(ev);
        setQueued(q.length);
        if (q.length > 14) delay = FAST_STEP_MS;
        else if (ev.phase === "progress") delay = 220;
      } else {
        delay = 150;
      }
      if (!stopped) timer = window.setTimeout(beat, delay);
    };
    beat();
    return () => {
      stopped = true;
      if (timer) window.clearTimeout(timer);
    };
  }, [play]);

  const replay = useCallback((runId: string) => {
    const run = runsRef.current[runId];
    if (!run) return;
    const events = run.events;
    const reset = { ...runsRef.current };
    delete reset[runId];
    runsRef.current = reset;
    setRuns(reset);
    queue.current = [...events, ...queue.current.filter((e) => e.run_id !== runId)];
    setQueued(queue.current.length);
    setSelectedStep(null);
  }, []);

  const tabRuns = useMemo(
    () => order.map((id) => runs[id]).filter((r): r is RunState => !!r && flowTab(r.flow) === tab),
    [order, runs, tab],
  );
  const runId = selectedRun[tab] ?? tabRuns[0]?.run_id ?? null;
  const run = runId ? runs[runId] : undefined;
  const diagram = DIAGRAMS[tab];
  const focusStep = selectedStep ?? run?.last ?? null;
  const focusNode = focusStep ? run?.nodes[focusStep] : undefined;
  const focusDef = diagram.nodes.find((n) => n.id === focusStep);

  return (
    <div className="flex min-h-screen flex-col bg-[#070a12] text-zinc-200">
      <header className="flex flex-wrap items-center justify-between gap-3 border-b border-white/10 bg-[#0b0f1a] px-4 py-3">
        <div className="flex items-center gap-3">
          <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-gradient-to-br from-indigo-500 to-violet-500 text-sm font-bold text-white">
            M
          </span>
          <div>
            <h1 className="text-sm font-semibold text-white">Malai Pioneer · Developer view</h1>
            <p className="text-xs text-zinc-500">
              Live pipeline trace: every step the API takes, where it runs, and the data it carries
            </p>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <nav className="flex rounded-lg border border-white/10 bg-black/30 p-0.5" aria-label="Flow">
            {(Object.keys(TAB_LABEL) as FlowTab[]).map((t) => (
              <button
                key={t}
                type="button"
                onClick={() => {
                  setTab(t);
                  setSelectedStep(null);
                }}
                className={`rounded-md px-3 py-1.5 text-xs font-medium transition ${
                  tab === t ? "bg-violet-500/90 text-white shadow" : "text-zinc-400 hover:text-white"
                }`}
              >
                {TAB_LABEL[t]}
                {order.some((id) => runs[id] && flowTab(runs[id].flow) === t && runs[id].status === "running") && (
                  <span className="dev-pulse ml-1.5 inline-block h-1.5 w-1.5 rounded-full bg-emerald-400 align-middle" />
                )}
              </button>
            ))}
          </nav>
          <label className="flex cursor-pointer items-center gap-1.5 rounded-lg border border-white/10 px-2.5 py-1.5 text-xs text-zinc-400">
            <input
              type="checkbox"
              checked={follow}
              onChange={(e) => setFollow(e.target.checked)}
              className="accent-violet-500"
            />
            Follow live
          </label>
          <ConnBadge conn={conn} queued={queued} />
        </div>
      </header>

      <div className="grid flex-1 grid-cols-1 gap-3 p-3 xl:grid-cols-[minmax(0,1fr)_380px]">
        <section className="flex min-w-0 flex-col gap-3">
          <RunBar
            runs={tabRuns}
            run={run}
            onPick={(id) => {
              setSelectedRun((s) => ({ ...s, [tab]: id }));
              setSelectedStep(null);
            }}
            onReplay={replay}
            agentOn={agentOn}
            tab={tab}
          />
          <div className="relative overflow-x-auto rounded-xl border border-white/10 bg-[#0b0f1a]">
            <svg
              viewBox={`0 0 ${VIEW_W} ${VIEW_H}`}
              className="block h-auto w-full min-w-[880px]"
              role="img"
              aria-label={`${TAB_LABEL[tab]} pipeline diagram`}
            >
              <defs>
                <pattern id="dev-grid" width="24" height="24" patternUnits="userSpaceOnUse">
                  <path d="M 24 0 L 0 0 0 24" fill="none" stroke="#ffffff08" strokeWidth="1" />
                </pattern>
                <filter id="dev-glow" x="-50%" y="-50%" width="200%" height="200%">
                  <feGaussianBlur stdDeviation="6" result="b" />
                  <feMerge>
                    <feMergeNode in="b" />
                    <feMergeNode in="SourceGraphic" />
                  </feMerge>
                </filter>
              </defs>
              <rect width={VIEW_W} height={VIEW_H} fill="url(#dev-grid)" />
              {(Object.keys(LANES) as Array<keyof typeof LANES>).map((k) => {
                const lane = LANES[k];
                const sub =
                  k === "models"
                    ? tab === "ingest"
                      ? `${laneOf(where.asr ?? "")} · ${laneOf(where.vision ?? "")} · ${laneOf(where.embed ?? "")}`
                      : `${laneOf(where.embed ?? "")} · ${laneOf(where.llm ?? "")}${agentOn ? " · OpenRouter" : ""}`
                    : k === "store"
                      ? where.store ?? ""
                      : "";
                return (
                  <g key={k}>
                    <rect
                      x={6}
                      y={lane.top}
                      width={VIEW_W - 12}
                      height={lane.bottom - lane.top}
                      rx={12}
                      fill={k === "api" ? "#ffffff06" : "#ffffff03"}
                      stroke="#ffffff10"
                    />
                    <text x={18} y={lane.top + 18} fill="#a1a1aa" fontSize={11} fontWeight={600} letterSpacing={0.6}>
                      {lane.label.toUpperCase()}
                    </text>
                    {sub && (
                      <text x={18} y={lane.top + 32} fill="#71717a" fontSize={10}>
                        {sub}
                      </text>
                    )}
                  </g>
                );
              })}

              {diagram.edges.map(([a, b]) => {
                const na = diagram.nodes.find((n) => n.id === a)!;
                const nb = diagram.nodes.find((n) => n.id === b)!;
                const key = `${a}>${b}`;
                const walked = run?.trail.includes(key);
                const hot = run?.lastEdge === key;
                return (
                  <path
                    key={key}
                    d={curve(na.x, na.y, nb.x, nb.y)}
                    fill="none"
                    stroke={hot ? "#a78bfa" : walked ? "#6d28d9" : "#27272a"}
                    strokeWidth={hot ? 2.5 : walked ? 2 : 1.5}
                    className={hot ? "dev-flow" : undefined}
                  />
                );
              })}
              {/* Hops the run made that are not static edges (e.g. agent loops). */}
              {run?.trail
                .filter((k) => !diagram.edges.some(([a, b]) => `${a}>${b}` === k))
                .map((k) => {
                  const [a, b] = k.split(">");
                  const na = diagram.nodes.find((n) => n.id === a);
                  const nb = diagram.nodes.find((n) => n.id === b);
                  if (!na || !nb) return null;
                  const hot = run.lastEdge === k;
                  return (
                    <path
                      key={`x-${k}`}
                      d={curve(na.x, na.y, nb.x, nb.y)}
                      fill="none"
                      stroke={hot ? "#a78bfa" : "#4c1d95"}
                      strokeWidth={hot ? 2.5 : 1.6}
                      strokeDasharray="4 5"
                      className={hot ? "dev-flow" : undefined}
                    />
                  );
                })}

              {diagram.nodes.map((n) => (
                <NodeBox
                  key={n.id}
                  node={n}
                  state={run?.nodes[n.id]}
                  sub={run?.nodes[n.id]?.events.at(-1)?.where ?? (n.role ? where[n.role] : undefined)}
                  selected={focusStep === n.id}
                  onClick={() => setSelectedStep(n.id)}
                />
              ))}

              {packets.map((p) => (
                <PacketDot key={p.id} packet={p} />
              ))}
            </svg>
            {!run && (
              <div className="pointer-events-none absolute inset-0 flex items-center justify-center">
                <p className="rounded-lg border border-white/10 bg-black/60 px-4 py-2 text-sm text-zinc-300">
                  {tab === "ingest"
                    ? "Upload a video or PDF in the app to watch it get indexed."
                    : tab === "chat"
                      ? "Ask the AI tutor a question in the app to watch the pipeline."
                      : "Open the Quiz tab in the app to watch a quiz get written."}
                </p>
              </div>
            )}
          </div>
          <EventLog run={run} focus={focusStep} onPick={setSelectedStep} />
        </section>

        <aside className="flex min-h-0 flex-col rounded-xl border border-white/10 bg-[#0b0f1a]">
          <Inspector def={focusDef} step={focusStep} state={focusNode} run={run} />
        </aside>
      </div>
    </div>
  );
}

function ConnBadge({ conn, queued }: { conn: string; queued: number }) {
  const map: Record<string, [string, string]> = {
    connecting: ["bg-amber-400", "Connecting"],
    live: ["bg-emerald-400", "Live"],
    offline: ["bg-red-400", "API unreachable"],
    disabled: ["bg-zinc-500", "Tracing off (DEV_TRACE=false)"],
    unconfigured: ["bg-red-400", "NEXT_PUBLIC_API_URL not set"],
  };
  const [dot, label] = map[conn] ?? map.connecting;
  return (
    <span className="flex items-center gap-2 rounded-lg border border-white/10 px-2.5 py-1.5 text-xs text-zinc-300">
      <span className={`h-2 w-2 rounded-full ${dot} ${conn === "live" ? "dev-pulse" : ""}`} />
      {label}
      {queued > 0 && <span className="text-zinc-500">· {queued} queued</span>}
    </span>
  );
}

function NodeBox({
  node,
  state,
  sub,
  selected,
  onClick,
}: {
  node: DiagramNode;
  state?: NodeState;
  sub?: string;
  selected: boolean;
  onClick: () => void;
}) {
  const status = state?.status ?? "idle";
  const color = statusColor(status);
  const x = node.x - NODE_W / 2;
  const y = node.y - NODE_H / 2;
  const last = state?.events.at(-1);
  const progress =
    last?.phase === "progress" && typeof last.data.done === "number" && typeof last.data.total === "number"
      ? (last.data.done as number) / Math.max(1, last.data.total as number)
      : null;
  const subText = sub ? (sub.length > 24 ? sub.slice(0, 23) + "…" : sub) : "";
  return (
    <g
      onClick={onClick}
      className="cursor-pointer"
      role="button"
      tabIndex={0}
      aria-label={`${node.label}: ${status}`}
      onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && onClick()}
    >
      {status === "active" && (
        <rect
          x={x - 6}
          y={y - 6}
          width={NODE_W + 12}
          height={NODE_H + 12}
          rx={16}
          fill="none"
          stroke="#a78bfa"
          strokeWidth={2}
          filter="url(#dev-glow)"
          className="dev-pulse"
        />
      )}
      <rect
        x={x}
        y={y}
        width={NODE_W}
        height={NODE_H}
        rx={11}
        fill={status === "idle" ? "#111827" : "#151b2e"}
        stroke={selected ? "#e4e4e7" : color}
        strokeWidth={selected ? 2 : status === "idle" ? 1 : 1.6}
      />
      <text
        x={node.x}
        y={node.y - 5}
        textAnchor="middle"
        fill={status === "idle" ? "#a1a1aa" : "#f4f4f5"}
        fontSize={11.5}
        fontWeight={600}
      >
        {node.label}
      </text>
      <text x={node.x} y={node.y + 11} textAnchor="middle" fill="#71717a" fontSize={9}>
        {subText}
      </text>
      {progress !== null && (
        <g>
          <rect x={x + 8} y={y + NODE_H - 9} width={NODE_W - 16} height={3} rx={1.5} fill="#27272a" />
          <rect x={x + 8} y={y + NODE_H - 9} width={(NODE_W - 16) * progress} height={3} rx={1.5} fill="#a78bfa" />
        </g>
      )}
      {status === "active" && (
        <circle
          cx={x + NODE_W - 2}
          cy={y + 2}
          r={7}
          fill="none"
          stroke="#c4b5fd"
          strokeWidth={2}
          strokeDasharray="22 22"
          className="dev-spin"
        />
      )}
      {(status === "done" || status === "error") && (
        <g key={`${status}-${state?.count}`} className="dev-pop">
          <circle cx={x + NODE_W - 2} cy={y + 2} r={8} fill={color} />
          <path
            d={
              status === "done"
                ? `M ${x + NODE_W - 6} ${y + 2} l 3 3 l 5 -6`
                : `M ${x + NODE_W - 5} ${y - 1} l 6 6 M ${x + NODE_W + 1} ${y - 1} l -6 6`
            }
            stroke="#0b0f1a"
            strokeWidth={2}
            fill="none"
            strokeLinecap="round"
          />
        </g>
      )}
      {state && state.count > 1 && (
        <text x={x + 6} y={y - 5} fill="#a78bfa" fontSize={10} fontWeight={700}>
          ×{state.count}
        </text>
      )}
      {state?.ms != null && (
        <text x={node.x} y={y + NODE_H + 13} textAnchor="middle" fill="#71717a" fontSize={9.5}>
          {state.ms >= 1000 ? `${(state.ms / 1000).toFixed(2)} s` : `${Math.round(state.ms)} ms`}
        </text>
      )}
    </g>
  );
}

function PacketDot({ packet }: { packet: Packet }) {
  const dot = useRef<SVGCircleElement>(null);
  const halo = useRef<SVGCircleElement>(null);
  useEffect(() => {
    const { from, to } = packet;
    const start = performance.now();
    const dur = 620;
    let raf = 0;
    const frame = (now: number) => {
      const t = Math.min(1, (now - start) / dur);
      const e = t < 0.5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2;
      const p = pointOn(from.x, from.y, to.x, to.y, e);
      for (const el of [dot.current, halo.current]) {
        el?.setAttribute("cx", String(p.x));
        el?.setAttribute("cy", String(p.y));
      }
      if (t < 1) raf = requestAnimationFrame(frame);
      else halo.current?.setAttribute("opacity", "0");
    };
    raf = requestAnimationFrame(frame);
    return () => cancelAnimationFrame(raf);
  }, [packet]);
  return (
    <g pointerEvents="none">
      <circle ref={halo} cx={packet.from.x} cy={packet.from.y} r={11} fill={packet.color} opacity={0.25} filter="url(#dev-glow)" />
      <circle ref={dot} cx={packet.from.x} cy={packet.from.y} r={5} fill={packet.color} />
    </g>
  );
}

function RunBar({
  runs,
  run,
  onPick,
  onReplay,
  agentOn,
  tab,
}: {
  runs: RunState[];
  run?: RunState;
  onPick: (id: string) => void;
  onReplay: (id: string) => void;
  agentOn: boolean;
  tab: FlowTab;
}) {
  const total = run ? ((run.endedAt ?? run.events.at(-1)?.ts ?? run.startedAt) - run.startedAt) : 0;
  const steps = run ? Object.keys(run.nodes).length : 0;
  return (
    <div className="flex flex-wrap items-center gap-3 rounded-xl border border-white/10 bg-[#0b0f1a] px-3 py-2 text-xs">
      <label className="flex items-center gap-2 text-zinc-400">
        Run
        <select
          value={run?.run_id ?? ""}
          onChange={(e) => onPick(e.target.value)}
          className="max-w-[320px] rounded-md border border-white/10 bg-black/40 px-2 py-1 text-zinc-200"
        >
          {runs.length === 0 && <option value="">No runs yet</option>}
          {runs.map((r) => (
            <option key={r.run_id} value={r.run_id}>
              {new Date(r.startedAt * 1000).toLocaleTimeString()} · {r.flow} · {r.status}
              {r.events[0]?.data.question ? ` · ${String(r.events[0].data.question).slice(0, 40)}` : ""}
              {r.events[0]?.data.file ? ` · ${String(r.events[0].data.file)}` : ""}
            </option>
          ))}
        </select>
      </label>
      {run && (
        <>
          <span
            className={`rounded-full px-2 py-0.5 font-medium ${
              run.status === "running"
                ? "bg-violet-500/20 text-violet-300"
                : run.status === "done"
                  ? "bg-emerald-500/15 text-emerald-300"
                  : "bg-red-500/15 text-red-300"
            }`}
          >
            {run.status}
          </span>
          <span className="text-zinc-400">
            {steps} steps · {run.events.length} events · {total.toFixed(2)} s
          </span>
          <button
            type="button"
            onClick={() => onReplay(run.run_id)}
            className="ml-auto rounded-md border border-violet-400/40 bg-violet-500/10 px-2.5 py-1 font-medium text-violet-200 transition hover:bg-violet-500/25"
          >
            ▶ Replay
          </button>
        </>
      )}
      {tab === "chat" && (
        <span className="text-zinc-500">{agentOn ? "Agent enabled: tool calls loop through the gate" : "Classic RAG pipeline"}</span>
      )}
    </div>
  );
}

function EventLog({
  run,
  focus,
  onPick,
}: {
  run?: RunState;
  focus: string | null;
  onPick: (step: string) => void;
}) {
  const box = useRef<HTMLDivElement>(null);
  const count = run?.events.length ?? 0;
  useEffect(() => {
    box.current?.scrollTo({ top: box.current.scrollHeight, behavior: "smooth" });
  }, [count]);
  return (
    <div className="rounded-xl border border-white/10 bg-[#0b0f1a]">
      <div className="border-b border-white/10 px-3 py-2 text-[11px] font-semibold uppercase tracking-wider text-zinc-400">
        Event log
      </div>
      <div ref={box} className="max-h-56 overflow-y-auto px-1 py-1 font-mono text-[11.5px]">
        {!run && <p className="px-2 py-3 text-zinc-500">Waiting for the first run…</p>}
        {run?.events.map((ev) => (
          <button
            key={ev.seq}
            type="button"
            onClick={() => onPick(ev.step)}
            className={`dev-in flex w-full items-center gap-3 rounded px-2 py-1 text-left hover:bg-white/5 ${
              focus === ev.step ? "bg-white/[0.04]" : ""
            }`}
          >
            <span className="w-14 shrink-0 text-zinc-500">+{(ev.ts - run.startedAt).toFixed(2)}s</span>
            <span
              className={`w-16 shrink-0 ${
                ev.phase === "error"
                  ? "text-red-400"
                  : ev.phase === "start" || ev.phase === "progress"
                    ? "text-violet-300"
                    : "text-emerald-300"
              }`}
            >
              {ev.phase}
            </span>
            <span className="w-24 shrink-0 text-zinc-300">{ev.step}</span>
            <span className="min-w-0 flex-1 truncate text-zinc-400">{ev.title}</span>
            <span className="hidden shrink-0 text-zinc-500 md:inline">{ev.where}</span>
            <span className="w-16 shrink-0 text-right text-zinc-500">
              {ev.ms != null ? (ev.ms >= 1000 ? `${(ev.ms / 1000).toFixed(2)}s` : `${Math.round(ev.ms)}ms`) : ""}
            </span>
          </button>
        ))}
      </div>
    </div>
  );
}

function Inspector({
  def,
  step,
  state,
  run,
}: {
  def?: DiagramNode;
  step: string | null;
  state?: NodeState;
  run?: RunState;
}) {
  if (!step || !state) {
    return (
      <div className="p-4 text-sm text-zinc-500">
        <h2 className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-zinc-400">Inspector</h2>
        Click any step on the diagram to see the data it received and produced.
      </div>
    );
  }
  // The latest invocation: its start event's inputs plus its end event's outputs.
  const evs = state.events;
  const lastIdx = evs.length - 1;
  let startIdx = lastIdx;
  while (startIdx > 0 && evs[startIdx].phase !== "start") startIdx--;
  const inv = evs.slice(evs[startIdx].phase === "start" ? startIdx : lastIdx);
  const merged: Record<string, unknown> = {};
  for (const ev of inv) Object.assign(merged, ev.data);
  const last = evs[lastIdx];
  const T = run?.events.find((e) => e.step === "request")?.data.T_ms;
  return (
    <div key={`${step}-${last.seq}`} className="dev-in flex min-h-0 flex-1 flex-col">
      <div className="border-b border-white/10 p-4">
        <h2 className="text-[11px] font-semibold uppercase tracking-wider text-zinc-400">Inspector</h2>
        <p className="mt-1 text-base font-semibold text-white">{def?.label ?? step}</p>
        <p className="text-sm text-zinc-300">{last.title}</p>
        <div className="mt-2 flex flex-wrap gap-1.5 text-[11px]">
          <span className="rounded-full bg-white/5 px-2 py-0.5 text-zinc-300">📍 {last.where}</span>
          <span
            className="rounded-full px-2 py-0.5"
            style={{ background: `${statusColor(state.status)}22`, color: statusColor(state.status) }}
          >
            {state.status}
          </span>
          {state.ms != null && (
            <span className="rounded-full bg-white/5 px-2 py-0.5 text-zinc-300">
              ⏱ {state.ms >= 1000 ? `${(state.ms / 1000).toFixed(2)} s` : `${Math.round(state.ms)} ms`}
            </span>
          )}
          {state.count > 1 && (
            <span className="rounded-full bg-violet-500/15 px-2 py-0.5 text-violet-300">ran ×{state.count}</span>
          )}
          {typeof T === "number" && (
            <span className="rounded-full bg-amber-500/10 px-2 py-0.5 text-amber-300">T = {fmtMs(T)}</span>
          )}
        </div>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto p-4">
        <DataView data={merged} T={typeof T === "number" ? T : undefined} />
      </div>
    </div>
  );
}
