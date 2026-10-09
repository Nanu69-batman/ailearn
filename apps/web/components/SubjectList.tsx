"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import {
  formatApiError,
  getSubjects,
  isApiConfigured,
  type Subject,
} from "@/lib/api";
import { subjectPath } from "@/lib/learn";

/**
 * The subject directory, shared by /dashboard and /ai-tutor/subjects.
 *
 * Everything shown here comes straight from `GET /subjects`. There is no
 * built-in subject and no placeholder row: if the backend has no subjects the
 * list says so, because a fabricated subject would send the user into a page
 * with no content behind it.
 */

type ListState = "loading" | "ready" | "error";

export interface SubjectListProps {
  /** Shown above the grid; each route supplies its own framing. */
  description: string;
}

export default function SubjectList({ description }: SubjectListProps) {
  const apiLive = isApiConfigured();
  const [subjects, setSubjects] = useState<Subject[]>([]);
  const [state, setState] = useState<ListState>("loading");
  const [error, setError] = useState<string | null>(null);

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

  return (
    <section aria-label="Subjects" className="flex flex-col gap-4">
      <p className="text-sm text-gray-500">{description}</p>

      {state === "loading" && (
        <div
          className="rounded-xl border border-gray-200 bg-white p-4 text-sm text-gray-500 shadow-sm"
          role="status"
        >
          <span className="mr-2 inline-block h-4 w-4 animate-spin rounded-full border-2 border-violet-400 border-t-transparent align-middle" />
          Loading subjects…
        </div>
      )}

      {state === "error" && (
        <div
          className="rounded-xl border border-red-200 bg-red-50 p-4 shadow-sm"
          role="alert"
        >
          <p className="text-sm font-semibold text-red-600">
            Could not load subjects.
          </p>
          <p className="mt-1 text-sm text-gray-600">{error}</p>
          {apiLive && (
            <button
              type="button"
              className="mt-3 rounded-lg bg-violet-600 px-3 py-1.5 text-sm font-medium text-white transition hover:bg-violet-700"
              onClick={() => void load()}
            >
              Retry
            </button>
          )}
        </div>
      )}

      {state === "ready" && subjects.length === 0 && (
        <div className="rounded-xl border border-gray-200 bg-white p-6 text-sm text-gray-500 shadow-sm">
          <p className="font-semibold text-gray-700">No subjects yet.</p>
          <p className="mt-1">
            Subjects are created through the API
            (<code className="rounded bg-gray-100 px-1">POST /subjects</code>)
            and only appear here once they exist there. Nothing is listed until
            then — this page never invents a subject.
          </p>
        </div>
      )}

      {state === "ready" && subjects.length > 0 && (
        <ul className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {subjects.map((s) => (
            <li key={s.id}>
              <Link
                href={subjectPath(s.id)}
                className="flex h-full flex-col gap-2 rounded-xl border border-gray-200 bg-white p-4 shadow-sm transition hover:border-violet-300 hover:shadow focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-violet-500"
              >
                <span className="text-sm font-semibold text-gray-900">
                  {s.name}
                </span>
                {s.description && (
                  <span className="line-clamp-3 text-sm text-gray-500">
                    {s.description}
                  </span>
                )}
                <span className="mt-auto pt-2 text-xs text-gray-400">
                  {s.resource_count === 1
                    ? "1 resource"
                    : `${s.resource_count} resources`}
                </span>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}