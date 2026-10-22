// Diagram layouts for the developer view (/dev).
//
// Each node is one pipeline step the API reports through GET /dev/trace
// (`app/devtrace.py`); its `id` is the event's `step`. Positions are in the
// SVG viewBox below, laid out in swimlanes by WHERE the step runs.

export type Lane = "browser" | "api" | "models" | "store";
export type FlowTab = "ingest" | "chat" | "quiz";

export interface DiagramNode {
  id: string;
  label: string;
  lane: Lane;
  x: number;
  y: number;
  /** Key into the `where` map from /dev/trace, for the default subtitle. */
  role?: "asr" | "vision" | "embed" | "store" | "llm" | "agent";
}

export interface Diagram {
  nodes: DiagramNode[];
  edges: Array<[string, string]>;
}

export const VIEW_W = 1320;
export const VIEW_H = 612;
export const NODE_W = 112;
export const NODE_H = 56;

export const LANES: Record<Lane, { top: number; bottom: number; label: string }> = {
  browser: { top: 12, bottom: 118, label: "Browser · Next.js on Vercel" },
  api: { top: 128, bottom: 304, label: "API · FastAPI on Render" },
  models: { top: 314, bottom: 498, label: "AI models" },
  store: { top: 508, bottom: 600, label: "Vector database" },
};

const Y = {
  browser: 70,
  api: 220,
  apiHi: 180,
  apiLo: 262,
  models: 410,
  modelsHi: 372,
  modelsLo: 452,
  store: 556,
};

export const DIAGRAMS: Record<FlowTab, Diagram> = {
  ingest: {
    nodes: [
      { id: "request", label: "Upload file", lane: "browser", x: 90, y: Y.browser },
      { id: "extract", label: "Read & plan", lane: "api", x: 250, y: Y.api },
      { id: "transcribe", label: "Transcribe audio", lane: "models", x: 500, y: Y.modelsHi, role: "asr" },
      { id: "describe", label: "Describe frames", lane: "models", x: 500, y: Y.modelsLo, role: "vision" },
      { id: "chunk", label: "Chunk + timestamps", lane: "api", x: 760, y: Y.api },
      { id: "embed", label: "Embed chunks", lane: "models", x: 940, y: Y.models, role: "embed" },
      { id: "index", label: "Upsert vectors", lane: "store", x: 1100, y: Y.store, role: "store" },
      { id: "respond", label: "Ready to ask", lane: "browser", x: 1236, y: Y.browser },
    ],
    edges: [
      ["request", "extract"],
      ["extract", "transcribe"],
      ["extract", "describe"],
      ["extract", "chunk"],
      ["transcribe", "chunk"],
      ["describe", "chunk"],
      ["chunk", "embed"],
      ["embed", "index"],
      ["index", "respond"],
    ],
  },
  chat: {
    nodes: [
      { id: "request", label: "Ask question", lane: "browser", x: 80, y: Y.browser },
      { id: "scope", label: "Scope check", lane: "api", x: 236, y: Y.apiHi },
      { id: "route", label: "Agent or RAG?", lane: "api", x: 236, y: Y.apiLo },
      { id: "plan", label: "Plan query", lane: "api", x: 410, y: Y.apiHi },
      { id: "tool", label: "Agent tool call", lane: "api", x: 410, y: Y.apiLo },
      { id: "embed_query", label: "Embed question", lane: "models", x: 570, y: Y.models, role: "embed" },
      { id: "search", label: "Vector search", lane: "store", x: 720, y: Y.store, role: "store" },
      { id: "gate", label: "Gate end_ms ≤ T", lane: "api", x: 860, y: Y.api },
      { id: "llm", label: "LLM writes answer", lane: "models", x: 1000, y: Y.models, role: "llm" },
      { id: "validate", label: "Validate citations", lane: "api", x: 1120, y: Y.api },
      { id: "respond", label: "Answer + citations", lane: "browser", x: 1236, y: Y.browser },
    ],
    edges: [
      ["request", "scope"],
      ["scope", "route"],
      ["route", "plan"],
      ["route", "tool"],
      ["plan", "embed_query"],
      ["tool", "embed_query"],
      ["tool", "search"],
      ["embed_query", "search"],
      ["search", "gate"],
      ["gate", "llm"],
      ["llm", "validate"],
      ["validate", "respond"],
    ],
  },
  quiz: {
    nodes: [
      { id: "request", label: "Request quiz", lane: "browser", x: 80, y: Y.browser },
      { id: "plan", label: "Plan retrieval", lane: "api", x: 220, y: Y.api },
      { id: "embed_query", label: "Embed query", lane: "models", x: 350, y: Y.models, role: "embed" },
      { id: "search", label: "Read watched range", lane: "store", x: 480, y: Y.store, role: "store" },
      { id: "gate", label: "Gate end_ms ≤ T", lane: "api", x: 610, y: Y.api },
      { id: "sample", label: "Sample lesson", lane: "api", x: 750, y: Y.api },
      { id: "llm", label: "LLM writes quiz", lane: "models", x: 900, y: Y.models, role: "llm" },
      { id: "validate", label: "Check each citation", lane: "api", x: 1060, y: Y.api },
      { id: "respond", label: "Quiz / grade", lane: "browser", x: 1230, y: Y.browser },
    ],
    edges: [
      ["request", "plan"],
      ["plan", "embed_query"],
      ["plan", "search"],
      ["embed_query", "search"],
      ["search", "gate"],
      ["gate", "sample"],
      ["gate", "llm"],
      ["sample", "llm"],
      ["llm", "validate"],
      ["validate", "respond"],
    ],
  },
};

export function flowTab(flow: string): FlowTab {
  if (flow === "ingest") return "ingest";
  if (flow === "quiz" || flow === "grade") return "quiz";
  return "chat";
}

export const TAB_LABEL: Record<FlowTab, string> = {
  ingest: "Ingestion",
  chat: "AI Tutor",
  quiz: "Quiz",
};

/** A curve between two node centres; loops back below when going left. */
export function curve(ax: number, ay: number, bx: number, by: number): string {
  if (bx >= ax) {
    const mx = (ax + bx) / 2;
    return `M ${ax} ${ay} C ${mx} ${ay}, ${mx} ${by}, ${bx} ${by}`;
  }
  const dip = Math.max(ay, by) + 90;
  return `M ${ax} ${ay} C ${ax} ${dip}, ${bx} ${dip}, ${bx} ${by}`;
}

/** Point on the same curve at t in [0, 1]. */
export function pointOn(ax: number, ay: number, bx: number, by: number, t: number) {
  let c1x: number, c1y: number, c2x: number, c2y: number;
  if (bx >= ax) {
    const mx = (ax + bx) / 2;
    [c1x, c1y, c2x, c2y] = [mx, ay, mx, by];
  } else {
    const dip = Math.max(ay, by) + 90;
    [c1x, c1y, c2x, c2y] = [ax, dip, bx, dip];
  }
  const u = 1 - t;
  const x = u * u * u * ax + 3 * u * u * t * c1x + 3 * u * t * t * c2x + t * t * t * bx;
  const y = u * u * u * ay + 3 * u * u * t * c1y + 3 * u * t * t * c2y + t * t * t * by;
  return { x, y };
}
