"use client";

// Renders one step's trace data for the developer view's inspector. Known
// shapes (passages, chunk samples, vectors, citation id lists, timestamps)
// get a purpose-built view; anything else falls back to key/value rows.

export function fmtMs(ms: number): string {
  const s = ms / 1000;
  const m = Math.floor(s / 60);
  const rest = s - m * 60;
  return `${m}:${rest < 10 ? "0" : ""}${rest.toFixed(rest % 1 ? 1 : 0)}`;
}

const LABELS: Record<string, string> = {
  T_ms: "Playhead T",
  question: "Question",
  search_query: "Search query",
  query: "Query",
  reply: "Model reply",
  answer: "Answer",
  prompt_tail: "End of prompt",
  rule: "Rule",
  filter: "Filter",
  verdict: "Verdict",
};

type Row = { label: string; value: unknown; key: string };

function label(key: string): string {
  return LABELS[key] ?? key.replace(/_/g, " ");
}

function isPassageList(v: unknown): v is Array<Record<string, unknown>> {
  return Array.isArray(v) && v.length > 0 && typeof v[0] === "object" && v[0] !== null && "text" in v[0];
}

function idOf(v: unknown): string {
  const s = String(v ?? "");
  return s.length > 26 ? `${s.slice(0, 12)}…${s.slice(-10)}` : s;
}

export function DataView({ data, T }: { data: Record<string, unknown>; T?: number }) {
  const rows: Row[] = Object.entries(data).map(([key, value]) => ({ key, label: label(key), value }));
  if (!rows.length) return <p className="text-sm text-zinc-500">This step carried no data.</p>;
  const retrieved = new Set(
    (Array.isArray(data.retrieved_ids) ? data.retrieved_ids : []).map((x) => String(x)),
  );
  return (
    <dl className="space-y-3">
      {rows.map((r) => (
        <div key={r.key} className="dev-in">
          <dt className="mb-1 text-[10.5px] font-semibold uppercase tracking-wider text-zinc-500">{r.label}</dt>
          <dd>
            <Value k={r.key} v={r.value} T={T} retrieved={retrieved} />
          </dd>
        </div>
      ))}
    </dl>
  );
}

function Value({
  k,
  v,
  T,
  retrieved,
}: {
  k: string;
  v: unknown;
  T?: number;
  retrieved: Set<string>;
}) {
  if (v === null || v === undefined) return <span className="text-sm text-zinc-600">none</span>;
  if (k === "error") return <span className="text-sm font-medium text-red-400">{String(v)}</span>;
  if (typeof v === "boolean")
    return <span className={`text-sm ${v ? "text-emerald-300" : "text-zinc-400"}`}>{v ? "yes" : "no"}</span>;
  if (typeof v === "number") {
    if (k.endsWith("_ms")) return <span className="font-mono text-sm text-amber-200">{fmtMs(v)} <span className="text-zinc-500">({v} ms)</span></span>;
    if (k === "bytes") return <span className="font-mono text-sm text-zinc-200">{(v / 1024 / 1024).toFixed(2)} MB</span>;
    return <span className="font-mono text-sm text-zinc-100">{v.toLocaleString()}</span>;
  }
  if (typeof v === "string") {
    if (k === "verdict") {
      const ok = v === "ok" || v === "grounded";
      return (
        <span className={`rounded px-2 py-0.5 text-sm font-medium ${ok ? "bg-emerald-500/15 text-emerald-300" : "bg-amber-500/15 text-amber-300"}`}>
          {v}
        </span>
      );
    }
    if (v.length > 60)
      return <p className="rounded-md border border-white/5 bg-black/30 p-2 text-[12.5px] leading-relaxed text-zinc-200">{v}</p>;
    return <span className="text-sm text-zinc-100">{v}</span>;
  }
  if (Array.isArray(v)) {
    if ((k === "range_ms" || k === "span_ms") && v.length === 2)
      return (
        <span className="font-mono text-sm text-amber-200">
          {typeof v[0] === "number" ? fmtMs(v[0]) : "?"} → {typeof v[1] === "number" ? fmtMs(v[1]) : "?"}
        </span>
      );
    if (k === "vector_head") return <VectorBars values={v as number[]} />;
    if (k === "windows")
      return (
        <div className="flex flex-wrap gap-1">
          {v.map((w, i) => (
            <span key={i} className="rounded bg-white/5 px-1.5 py-0.5 font-mono text-[11px] text-zinc-300">
              {Array.isArray(w) ? `${fmtMs(w[0] as number)}–${fmtMs(w[1] as number)}` : String(w)}
            </span>
          ))}
        </div>
      );
    if (isPassageList(v)) return <Passages items={v} T={T} />;
    if (v.length === 0) return <span className="text-sm text-zinc-600">empty</span>;
    if (v.every((x) => typeof x !== "object" || x === null)) {
      const tone =
        k === "unknown_ids"
          ? "bg-red-500/15 text-red-300"
          : k === "cited_ids"
            ? ""
            : "bg-white/5 text-zinc-300";
      return (
        <div className="flex flex-wrap gap-1">
          {v.map((x, i) => {
            const s = String(x);
            const cls =
              k === "cited_ids"
                ? retrieved.size === 0 || retrieved.has(s)
                  ? "bg-emerald-500/15 text-emerald-300"
                  : "bg-red-500/15 text-red-300"
                : tone;
            return (
              <span key={i} title={s} className={`rounded px-1.5 py-0.5 font-mono text-[11px] ${cls}`}>
                {k === "cited_ids" && (retrieved.size === 0 || retrieved.has(s)) ? "✓ " : ""}
                {idOf(s)}
              </span>
            );
          })}
        </div>
      );
    }
    return (
      <div className="space-y-1.5">
        {v.map((x, i) => (
          <div key={i} className="rounded-md border border-white/5 bg-black/30 p-2">
            <Nested obj={x as Record<string, unknown>} T={T} retrieved={retrieved} />
          </div>
        ))}
      </div>
    );
  }
  if (typeof v === "object") {
    return (
      <div className="rounded-md border border-white/5 bg-black/30 p-2">
        <Nested obj={v as Record<string, unknown>} T={T} retrieved={retrieved} />
      </div>
    );
  }
  return <span className="text-sm">{String(v)}</span>;
}

function Nested({ obj, T, retrieved }: { obj: Record<string, unknown>; T?: number; retrieved: Set<string> }) {
  return (
    <div className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-1">
      {Object.entries(obj).map(([k, v]) => (
        <div key={k} className="contents">
          <span className="text-[11px] text-zinc-500">{label(k)}</span>
          <span className="min-w-0 break-words">
            <Value k={k} v={v} T={T} retrieved={retrieved} />
          </span>
        </div>
      ))}
    </div>
  );
}

function VectorBars({ values }: { values: number[] }) {
  const max = Math.max(1e-6, ...values.map((x) => Math.abs(x)));
  return (
    <div>
      <div className="flex h-12 items-center gap-1 rounded-md border border-white/5 bg-black/30 px-2">
        {values.map((x, i) => (
          <div key={i} className="flex h-full flex-1 flex-col justify-center">
            <div
              className={`dev-in mx-auto w-full rounded-sm ${x >= 0 ? "bg-violet-400/80" : "bg-sky-400/80"}`}
              style={{
                height: `${(Math.abs(x) / max) * 45}%`,
                transform: x >= 0 ? "translateY(-50%)" : "translateY(50%)",
                animationDelay: `${i * 40}ms`,
              }}
            />
          </div>
        ))}
        <span className="pl-1 text-[11px] text-zinc-500">…</span>
      </div>
      <p className="mt-1 font-mono text-[10.5px] text-zinc-500">[{values.map((x) => x.toFixed(3)).join(", ")}, …]</p>
    </div>
  );
}

function Passages({ items, T }: { items: Array<Record<string, unknown>>; T?: number }) {
  return (
    <div className="space-y-1.5">
      {items.map((p, i) => {
        const end = typeof p.end_ms === "number" ? p.end_ms : null;
        const start = typeof p.start_ms === "number" ? p.start_ms : null;
        const gated = end !== null && T !== undefined;
        const ok = gated ? end! <= T! : null;
        const kind = String(p.kind ?? "");
        return (
          <div
            key={String(p.chunk_id ?? i)}
            className="dev-in rounded-md border border-white/5 bg-black/30 p-2"
            style={{ animationDelay: `${i * 45}ms` }}
          >
            <div className="mb-1 flex flex-wrap items-center gap-1.5 text-[10.5px]">
              {kind && (
                <span
                  className={`rounded px-1.5 py-0.5 font-medium ${
                    kind === "visual"
                      ? "bg-sky-500/15 text-sky-300"
                      : kind === "transcript"
                        ? "bg-violet-500/15 text-violet-300"
                        : "bg-amber-500/15 text-amber-300"
                  }`}
                >
                  {kind === "visual" ? "frame" : kind === "transcript" ? "audio" : kind}
                </span>
              )}
              {start !== null && end !== null && (
                <span className="font-mono text-amber-200">
                  {fmtMs(start)}–{fmtMs(end)}
                </span>
              )}
              {typeof p.page_no === "number" && <span className="text-amber-200">page {p.page_no}</span>}
              {ok !== null && (
                <span className={ok ? "text-emerald-300" : "text-red-400"}>
                  {ok ? "✓ end ≤ T" : "✗ after T"}
                </span>
              )}
              {typeof p.score === "number" && p.score !== 1 && (
                <span className="text-zinc-500">dist {p.score.toFixed(3)}</span>
              )}
              {p.chunk_id != null && (
                <span className="ml-auto font-mono text-zinc-600" title={String(p.chunk_id)}>
                  {idOf(p.chunk_id)}
                </span>
              )}
            </div>
            <p className="text-[12px] leading-snug text-zinc-300">{String(p.text ?? "")}</p>
          </div>
        );
      })}
    </div>
  );
}
