"use client";

import { useWatchProgress } from "@/lib/progress";
import { useCallback, useEffect, useMemo, useState } from "react";
import type { Subject } from "@/lib/api";
import {
  readAccessed,
  readArchived,
  subjectProgress,
  writeArchived,
} from "@/lib/activity";
import SubjectCard, {
  AddSubjectTile,
  SubjectCardSkeleton,
} from "@/components/SubjectCard";
import SubjectCreateDialog from "@/components/SubjectCreateDialog";
import SubjectDeleteDialog from "@/components/SubjectDeleteDialog";
import Select from "@/components/Select";
import { useSubjects } from "@/components/useSubjects";

/**
 * My subjects: every subject from `GET /subjects`, with search, status
 * filters, sorting and a grid/list toggle. Progress, "last accessed" and
 * archiving are this browser's own records (see lib/activity.ts).
 */

type Filter = "all" | "ongoing" | "completed" | "archived";
type Sort = "accessed" | "name" | "created" | "progress";
type View = "grid" | "list";

const FILTERS: ReadonlyArray<{ value: Filter; label: string }> = [
  { value: "all", label: "All" },
  { value: "ongoing", label: "Ongoing" },
  { value: "completed", label: "Completed" },
  { value: "archived", label: "Archived" },
];

const SORTS: ReadonlyArray<{ value: Sort; label: string }> = [
  { value: "accessed", label: "Last accessed" },
  { value: "name", label: "Name" },
  { value: "created", label: "Recently added" },
  { value: "progress", label: "Progress" },
];

const VIEW_KEY = "ailearn.subjects.view";
const SKELETON_COUNT = 6;

export function SubjectsError({ error, onRetry }: { error: string | null; onRetry?: () => void }) {
  return (
    <div
      className="flex flex-col items-start gap-3 rounded-2xl border border-red-200 bg-red-50 p-5 shadow-sm sm:flex-row sm:items-center"
      role="alert"
    >
      <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-white text-red-600 shadow-sm">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" className="h-5 w-5" aria-hidden="true">
          <path d="M12 8v5M12 16.5v.01M10.3 3.9L1.8 18a2 2 0 001.7 3h17a2 2 0 001.7-3L13.7 3.9a2 2 0 00-3.4 0z" />
        </svg>
      </span>
      <div className="min-w-0 flex-1">
        <p className="text-sm font-semibold text-red-700">Could not load subjects.</p>
        <p className="mt-0.5 text-sm text-gray-600">{error}</p>
      </div>
      {onRetry && (
        <button
          type="button"
          className="rounded-xl bg-white px-4 py-2 text-sm font-medium text-gray-700 shadow-sm ring-1 ring-gray-200 transition hover:bg-gray-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-violet-500"
          onClick={onRetry}
        >
          Retry
        </button>
      )}
    </div>
  );
}

export function Notice({ text }: { text: string }) {
  return (
    <p role="status" className="flex items-center gap-2 self-start rounded-xl border border-emerald-200 bg-emerald-50 px-3.5 py-2 text-sm text-emerald-700">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" className="h-4 w-4" aria-hidden="true">
        <path d="M20 6L9 17l-5-5" />
      </svg>
      {text}
    </p>
  );
}

function matches(subject: Subject, query: string): boolean {
  if (!query) return true;
  const q = query.toLowerCase();
  return (
    subject.name.toLowerCase().includes(q) ||
    (subject.description ?? "").toLowerCase().includes(q)
  );
}

export default function SubjectList() {
  const { apiLive, subjects, state, error, load, added, removed } = useSubjects();
  const [createOpen, setCreateOpen] = useState(false);
  const [pendingDelete, setPendingDelete] = useState<Subject | null>(null);
  const [highlightId, setHighlightId] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<Filter>("all");
  const [sort, setSort] = useState<Sort>("accessed");
  const [view, setView] = useState<View>("grid");

  // Browser-only records, read after mount so server and client HTML agree.
  const [archived, setArchived] = useState<string[]>([]);
  const [accessed, setAccessed] = useState<Record<string, number>>({});
  const [progress, setProgress] = useState<Record<string, number>>({});

  useEffect(() => {
    setArchived(readArchived());
    setAccessed(readAccessed());
    try {
      if (window.localStorage.getItem(VIEW_KEY) === "list") setView("list");
    } catch {
      // Storage unavailable -> default grid.
    }
  }, []);

  // Watch progress counts toward a subject's progress (and syncs from the account).
  const watch = useWatchProgress();
  useEffect(() => {
    const next: Record<string, number> = {};
    for (const s of subjects) next[s.id] = subjectProgress(s);
    setProgress(next);
  }, [subjects, watch]);

  // Fade the "just created" highlight and transient notices.
  useEffect(() => {
    if (!highlightId) return;
    const t = window.setTimeout(() => setHighlightId(null), 2400);
    return () => window.clearTimeout(t);
  }, [highlightId]);
  useEffect(() => {
    if (!notice) return;
    const t = window.setTimeout(() => setNotice(null), 3500);
    return () => window.clearTimeout(t);
  }, [notice]);

  const chooseView = useCallback((v: View): void => {
    setView(v);
    try {
      window.localStorage.setItem(VIEW_KEY, v);
    } catch {
      // Storage unavailable -> keep in-memory only.
    }
  }, []);

  const toggleArchive = useCallback((subject: Subject): void => {
    setArchived((prev) => {
      const isArchived = prev.includes(subject.id);
      const next = isArchived ? prev.filter((id) => id !== subject.id) : [...prev, subject.id];
      writeArchived(next);
      setNotice(isArchived ? `“${subject.name}” moved back to your subjects.` : `“${subject.name}” archived.`);
      return next;
    });
  }, []);

  const closeCreate = useCallback(() => setCreateOpen(false), []);
  const closeDelete = useCallback(() => setPendingDelete(null), []);

  const onCreated = useCallback(
    (created: Subject): void => {
      added(created);
      setHighlightId(created.id);
      setNotice(`“${created.name}” created.`);
      setCreateOpen(false);
      setFilter("all");
    },
    [added],
  );

  const onDeleted = useCallback(
    (id: string): void => {
      const gone = subjects.find((s) => s.id === id);
      if (gone) setNotice(`“${gone.name}” removed. Its materials stay indexed.`);
      removed(id);
      setPendingDelete(null);
    },
    [subjects, removed],
  );

  const visible = useMemo(() => {
    const list = subjects.filter((s) => {
      if (!matches(s, query.trim())) return false;
      const isArchived = archived.includes(s.id);
      const pct = progress[s.id] ?? 0;
      const done = (s.resource_count ?? 0) > 0 && pct >= 100;
      if (filter === "archived") return isArchived;
      if (isArchived) return false;
      if (filter === "completed") return done;
      if (filter === "ongoing") return !done;
      return true;
    });
    const byName = (a: Subject, b: Subject) => a.name.localeCompare(b.name);
    return [...list].sort((a, b) => {
      if (sort === "name") return byName(a, b);
      if (sort === "progress") return (progress[b.id] ?? 0) - (progress[a.id] ?? 0) || byName(a, b);
      if (sort === "created") return (b.created_at ?? "").localeCompare(a.created_at ?? "") || byName(a, b);
      return (accessed[b.id] ?? 0) - (accessed[a.id] ?? 0) || (b.created_at ?? "").localeCompare(a.created_at ?? "");
    });
  }, [subjects, query, filter, sort, archived, progress, accessed]);

  const searching = query.trim().length > 0;
  const gridClass =
    view === "grid"
      ? "grid grid-cols-1 gap-6 md:grid-cols-2 xl:grid-cols-3"
      : "flex flex-col gap-3";

  return (
    <section aria-labelledby="subject-list-title" className="flex flex-col gap-7">
      <div className="md:pr-14">
        <h1 id="subject-list-title" className="text-3xl font-bold tracking-tight text-gray-900 sm:text-4xl">
          My subjects
        </h1>
        <p className="mt-2 text-base text-gray-500">
          All your subjects in one place. Open a subject to watch, read and ask questions.
        </p>
      </div>

      {/* Toolbar */}
      <div className="flex flex-col gap-3 lg:flex-row lg:items-center">
        <label className="relative min-w-0 lg:max-w-md lg:flex-1">
          <span className="sr-only">Search subjects</span>
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" className="pointer-events-none absolute left-4 top-1/2 h-5 w-5 -translate-y-1/2 text-gray-400" aria-hidden="true">
            <circle cx="11" cy="11" r="7" />
            <path d="M20 20l-3.5-3.5" />
          </svg>
          <input
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search subjects, topics or materials…"
            className="w-full rounded-full border border-gray-200 bg-white py-2.5 pl-12 pr-4 text-sm text-gray-800 placeholder-gray-400 shadow-sm outline-none transition focus:border-violet-400 focus:ring-2 focus:ring-violet-200"
          />
        </label>

        <div className="flex flex-wrap gap-2" role="group" aria-label="Filter subjects">
          {FILTERS.map((f) => (
            <button
              key={f.value}
              type="button"
              aria-pressed={filter === f.value}
              onClick={() => setFilter(f.value)}
              className={[
                "rounded-full px-4 py-2 text-sm font-medium transition focus:outline-none focus-visible:ring-2 focus-visible:ring-violet-400",
                filter === f.value
                  ? "bg-violet-600 text-white shadow-sm shadow-violet-600/30"
                  : "border border-gray-200 bg-white text-gray-700 shadow-sm hover:border-violet-200 hover:text-violet-700",
              ].join(" ")}
            >
              {f.label}
            </button>
          ))}
        </div>

        <div className="flex items-center gap-3 lg:ml-auto">
          <Select value={sort} options={SORTS} onChange={setSort} label="Sort subjects" />
          <div className="flex items-center gap-1" role="group" aria-label="Layout">
            {(["grid", "list"] as const).map((v) => (
              <button
                key={v}
                type="button"
                aria-pressed={view === v}
                aria-label={v === "grid" ? "Grid view" : "List view"}
                title={v === "grid" ? "Grid view" : "List view"}
                onClick={() => chooseView(v)}
                className={[
                  "rounded-lg p-2 transition focus:outline-none focus-visible:ring-2 focus-visible:ring-violet-400",
                  view === v ? "bg-violet-100 text-violet-700" : "text-gray-500 hover:bg-gray-100 hover:text-gray-800",
                ].join(" ")}
              >
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="h-5 w-5" aria-hidden="true">
                  {v === "grid" ? (
                    <path d="M4 4h6v6H4zM14 4h6v6h-6zM4 14h6v6H4zM14 14h6v6h-6z" />
                  ) : (
                    <path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01" />
                  )}
                </svg>
              </button>
            ))}
          </div>
        </div>
      </div>

      {notice && <Notice text={notice} />}

      {state === "loading" && (
        <div role="status" aria-live="polite">
          <span className="sr-only">Loading subjects…</span>
          <ul className="grid grid-cols-1 gap-6 md:grid-cols-2 xl:grid-cols-3">
            {Array.from({ length: SKELETON_COUNT }, (_, i) => (
              <li key={i}>
                <SubjectCardSkeleton />
              </li>
            ))}
          </ul>
        </div>
      )}

      {state === "error" && <SubjectsError error={error} onRetry={apiLive ? () => void load() : undefined} />}

      {state === "ready" && (
        <ul className={gridClass}>
          {visible.map((s) => (
            <li key={s.id}>
              <SubjectCard
                subject={s}
                layout={view}
                progress={progress[s.id] ?? 0}
                started={accessed[s.id] !== undefined}
                archived={archived.includes(s.id)}
                onToggleArchive={toggleArchive}
                highlight={s.id === highlightId}
                onRequestDelete={setPendingDelete}
              />
            </li>
          ))}
          {visible.length === 0 && (searching || filter !== "all") && (
            <li className="col-span-full rounded-2xl border border-gray-200 bg-white px-6 py-10 text-center text-sm text-gray-500">
              {searching ? `No subjects match “${query.trim()}”.` : "Nothing here yet."}
            </li>
          )}
          {apiLive && filter !== "archived" && !searching && (
            <li className={view === "grid" ? "min-h-[14rem]" : ""}>
              <AddSubjectTile onClick={() => setCreateOpen(true)} detailed={view === "grid"} />
            </li>
          )}
        </ul>
      )}

      <SubjectCreateDialog open={createOpen} onClose={closeCreate} onCreated={onCreated} />
      <SubjectDeleteDialog subject={pendingDelete} onClose={closeDelete} onDeleted={onDeleted} />
    </section>
  );
}
