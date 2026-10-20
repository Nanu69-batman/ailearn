"use client";

import { useCallback, useEffect, useState } from "react";
import {
  formatApiError,
  getSubjects,
  isApiConfigured,
  type Subject,
} from "@/lib/api";
import SubjectCard, { SubjectCardSkeleton } from "@/components/SubjectCard";
import SubjectCreateDialog from "@/components/SubjectCreateDialog";
import SubjectDeleteDialog from "@/components/SubjectDeleteDialog";

/**
 * The subject directory, shared by /dashboard and /ai-tutor/subjects.
 *
 * Everything shown here comes straight from `GET /subjects`. There is no
 * built-in subject and no placeholder row: if the backend has no subjects the
 * list says so, because a fabricated subject would send the user into a page
 * with no content behind it. Subjects are created / deleted through the real
 * API (`POST /subjects`, `DELETE /subjects/{id}`) and the list is updated from
 * the server's response.
 */

type ListState = "loading" | "ready" | "error";

export interface SubjectListProps {
  /** Page heading. */
  title: string;
  /** One-line framing under the heading. */
  description: string;
  /** Prefix the heading with a time-of-day greeting (dashboard only). */
  greeting?: boolean;
}

const SKELETON_COUNT = 6;

function timeGreeting(date: Date): string {
  const h = date.getHours();
  if (h < 12) return "Good morning";
  if (h < 18) return "Good afternoon";
  return "Good evening";
}

function PlusIcon({ className = "h-4 w-4" }: { className?: string }) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2.2"
      strokeLinecap="round"
      className={className}
      aria-hidden="true"
    >
      <path d="M12 5v14M5 12h14" />
    </svg>
  );
}

export default function SubjectList({
  title,
  description,
  greeting = false,
}: SubjectListProps) {
  const apiLive = isApiConfigured();
  const [subjects, setSubjects] = useState<Subject[]>([]);
  const [state, setState] = useState<ListState>("loading");
  const [error, setError] = useState<string | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  const [pendingDelete, setPendingDelete] = useState<Subject | null>(null);
  const [highlightId, setHighlightId] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  // Computed after mount so server and client HTML agree.
  const [hello, setHello] = useState<string | null>(null);

  useEffect(() => {
    if (greeting) setHello(timeGreeting(new Date()));
  }, [greeting]);

  const load = useCallback(async (): Promise<void> => {
    if (!apiLive) {
      // No backend to ask. Stated plainly rather than showing an empty grid
      // that reads like "this account has no subjects".
      setState("error");
      setError(
        "NEXT_PUBLIC_API_URL is not set, so subjects cannot be loaded from the API.",
      );
      return;
    }
    setState("loading");
    setError(null);
    try {
      const res = await getSubjects();
      setSubjects(Array.isArray(res.subjects) ? res.subjects : []);
      setState("ready");
    } catch (err) {
      setError(formatApiError(err));
      setState("error");
    }
  }, [apiLive]);

  useEffect(() => {
    void load();
  }, [load]);

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

  const closeCreate = useCallback(() => setCreateOpen(false), []);
  const closeDelete = useCallback(() => setPendingDelete(null), []);

  const onCreated = useCallback((created: Subject): void => {
    setSubjects((prev) => [created, ...prev.filter((s) => s.id !== created.id)]);
    setState("ready");
    setHighlightId(created.id);
    setNotice(`“${created.name}” created.`);
    setCreateOpen(false);
  }, []);

  const onDeleted = useCallback(
    (id: string): void => {
      const gone = subjects.find((s) => s.id === id);
      if (gone) setNotice(`“${gone.name}” removed. Its materials stay indexed.`);
      setSubjects((prev) => prev.filter((s) => s.id !== id));
      setPendingDelete(null);
    },
    [subjects],
  );

  const canCreate = apiLive;
  const totalResources = subjects.reduce(
    (sum, s) => sum + (s.resource_count ?? 0),
    0,
  );

  return (
    <section aria-labelledby="subject-list-title" className="flex flex-col gap-6">
      {/* Header */}
      <div className="relative overflow-hidden rounded-2xl border border-gray-200 bg-white p-6 shadow-sm sm:p-7">
        <div
          aria-hidden="true"
          className="pointer-events-none absolute -right-16 -top-20 h-56 w-56 rounded-full bg-gradient-to-br from-violet-200/70 to-indigo-200/40 blur-3xl"
        />
        <div className="relative flex flex-col gap-5 sm:flex-row sm:items-end sm:justify-between">
          <div className="min-w-0">
            {greeting && (
              <p className="text-sm font-medium text-violet-600">
                {hello ?? "Welcome back"}
              </p>
            )}
            <h1
              id="subject-list-title"
              className="mt-1 text-2xl font-bold tracking-tight text-gray-900 sm:text-3xl"
            >
              {title}
            </h1>
            <p className="mt-1.5 max-w-xl text-sm text-gray-500">{description}</p>
            {state === "ready" && subjects.length > 0 && (
              <div className="mt-4 flex flex-wrap gap-2 text-xs font-medium">
                <span className="rounded-full bg-violet-50 px-2.5 py-1 text-violet-700">
                  {subjects.length === 1 ? "1 subject" : `${subjects.length} subjects`}
                </span>
                <span className="rounded-full bg-gray-100 px-2.5 py-1 text-gray-600">
                  {totalResources === 1
                    ? "1 indexed resource"
                    : `${totalResources} indexed resources`}
                </span>
              </div>
            )}
          </div>
          {canCreate && (
            <button
              type="button"
              onClick={() => setCreateOpen(true)}
              className="inline-flex shrink-0 items-center justify-center gap-2 rounded-xl bg-violet-600 px-4 py-2.5 text-sm font-semibold text-white shadow-sm shadow-violet-600/20 transition hover:bg-violet-700 hover:shadow-md focus:outline-none focus-visible:ring-2 focus-visible:ring-violet-500 focus-visible:ring-offset-2"
            >
              <PlusIcon />
              New subject
            </button>
          )}
        </div>
      </div>

      {notice && (
        <p
          role="status"
          className="flex items-center gap-2 self-start rounded-xl border border-emerald-200 bg-emerald-50 px-3.5 py-2 text-sm text-emerald-700"
        >
          <svg
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2.2"
            strokeLinecap="round"
            strokeLinejoin="round"
            className="h-4 w-4"
            aria-hidden="true"
          >
            <path d="M20 6L9 17l-5-5" />
          </svg>
          {notice}
        </p>
      )}

      {state === "loading" && (
        <div role="status" aria-live="polite">
          <span className="sr-only">Loading subjects…</span>
          <ul className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3">
            {Array.from({ length: SKELETON_COUNT }, (_, i) => (
              <li key={i}>
                <SubjectCardSkeleton />
              </li>
            ))}
          </ul>
        </div>
      )}

      {state === "error" && (
        <div
          className="flex flex-col items-start gap-3 rounded-2xl border border-red-200 bg-red-50 p-5 shadow-sm sm:flex-row sm:items-center"
          role="alert"
        >
          <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-white text-red-600 shadow-sm">
            <svg
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              className="h-5 w-5"
              aria-hidden="true"
            >
              <path d="M12 8v5M12 16.5v.01M10.3 3.9L1.8 18a2 2 0 001.7 3h17a2 2 0 001.7-3L13.7 3.9a2 2 0 00-3.4 0z" />
            </svg>
          </span>
          <div className="min-w-0 flex-1">
            <p className="text-sm font-semibold text-red-700">
              Could not load subjects.
            </p>
            <p className="mt-0.5 text-sm text-gray-600">{error}</p>
          </div>
          {apiLive && (
            <button
              type="button"
              className="rounded-xl bg-white px-4 py-2 text-sm font-medium text-gray-700 shadow-sm ring-1 ring-gray-200 transition hover:bg-gray-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-violet-500"
              onClick={() => void load()}
            >
              Retry
            </button>
          )}
        </div>
      )}

      {state === "ready" && subjects.length === 0 && (
        <div className="flex flex-col items-center rounded-2xl border-2 border-dashed border-gray-200 bg-white px-6 py-14 text-center">
          <span className="relative flex h-16 w-16 items-center justify-center rounded-2xl bg-gradient-to-br from-violet-500 to-indigo-500 text-white shadow-md shadow-violet-500/30">
            <svg
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.8"
              strokeLinecap="round"
              strokeLinejoin="round"
              className="h-8 w-8"
              aria-hidden="true"
            >
              <path d="M4 19.5A2.5 2.5 0 016.5 17H20V4H6.5A2.5 2.5 0 004 6.5v13zM4 19.5A2.5 2.5 0 006.5 22H20v-5" />
            </svg>
          </span>
          <h2 className="mt-5 text-lg font-semibold text-gray-900">
            No subjects yet
          </h2>
          <p className="mt-1.5 max-w-sm text-sm text-gray-500">
            Create your first subject, then add videos and files to it. Malai
            will answer your questions using only those materials.
          </p>
          <button
            type="button"
            onClick={() => setCreateOpen(true)}
            className="mt-6 inline-flex items-center gap-2 rounded-xl bg-violet-600 px-5 py-2.5 text-sm font-semibold text-white shadow-sm transition hover:bg-violet-700 hover:shadow-md focus:outline-none focus-visible:ring-2 focus-visible:ring-violet-500 focus-visible:ring-offset-2"
          >
            <PlusIcon />
            Create your first subject
          </button>
        </div>
      )}

      {state === "ready" && subjects.length > 0 && (
        <ul className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3">
          {subjects.map((s) => (
            <li key={s.id}>
              <SubjectCard
                subject={s}
                highlight={s.id === highlightId}
                onRequestDelete={setPendingDelete}
              />
            </li>
          ))}
          <li>
            <button
              type="button"
              onClick={() => setCreateOpen(true)}
              className="group flex h-full min-h-[11rem] w-full flex-col items-center justify-center gap-2 rounded-2xl border-2 border-dashed border-gray-200 bg-white/50 p-5 text-sm font-medium text-gray-500 transition hover:border-violet-300 hover:bg-violet-50/40 hover:text-violet-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-violet-500 focus-visible:ring-offset-2"
            >
              <span className="flex h-10 w-10 items-center justify-center rounded-xl bg-gray-100 text-gray-500 transition group-hover:bg-violet-100 group-hover:text-violet-700">
                <PlusIcon className="h-5 w-5" />
              </span>
              New subject
            </button>
          </li>
        </ul>
      )}

      <SubjectCreateDialog
        open={createOpen}
        onClose={closeCreate}
        onCreated={onCreated}
      />
      <SubjectDeleteDialog
        subject={pendingDelete}
        onClose={closeDelete}
        onDeleted={onDeleted}
      />
    </section>
  );
}
