"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import { MALAI_PATH } from "@/lib/malai";
import { quizPath, useSavedQuizzes, type SavedQuiz } from "@/lib/quizStore";
import { initials, subjectAccent } from "@/components/SubjectCard";
import { useSubjects } from "@/components/useSubjects";

/**
 * Every saved practice test in one place, grouped by subject (tests from a chat
 * with no subject go under "Other"). Each row links to the test's own page:
 * analytics, a re-attempt and every past attempt. Quizzes never show up here,
 * because they are one-time checks that stay in their chat.
 */

const pct = (score: number, total: number) => (total ? Math.round((score / total) * 100) : 0);

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-xl border border-gray-200 bg-white px-4 py-3">
      <p className="text-[11px] font-medium uppercase tracking-wide text-gray-400">{label}</p>
      <p className="mt-0.5 text-xl font-semibold tabular-nums text-gray-900">{value}</p>
    </div>
  );
}

function TestRow({ test }: { test: SavedQuiz }) {
  const last = test.attempts[test.attempts.length - 1];
  const best = test.attempts.reduce<number | null>((b, a) => Math.max(b ?? 0, pct(a.score, a.total)), null);
  return (
    <li>
      <Link
        href={quizPath(test.id)}
        className="flex items-center gap-3 rounded-xl px-3 py-2.5 transition hover:bg-violet-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-violet-400"
      >
        <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-violet-100 text-sm" aria-hidden="true">📝</span>
        <span className="min-w-0 flex-1">
          <span className="block truncate text-sm font-medium text-gray-900">{test.title}</span>
          <span className="block text-xs text-gray-500">
            {test.questions.length} questions · {test.attempts.length} attempt{test.attempts.length === 1 ? "" : "s"}
            {last ? ` · last ${last.score}/${last.total}` : " · not attempted yet"}
          </span>
        </span>
        {best !== null && (
          <span
            className={`shrink-0 rounded-full px-2.5 py-0.5 text-xs font-semibold tabular-nums ${
              best >= 80 ? "bg-emerald-50 text-emerald-700" : best >= 50 ? "bg-amber-50 text-amber-700" : "bg-red-50 text-red-700"
            }`}
            title="Best score"
          >
            Best {best}%
          </span>
        )}
      </Link>
    </li>
  );
}

export default function PracticeView() {
  const tests = useSavedQuizzes();
  const { subjects } = useSubjects();
  const [query, setQuery] = useState("");

  const q = query.trim().toLowerCase();
  const shown = q ? tests.filter((t) => t.title.toLowerCase().includes(q)) : tests;

  const groups = useMemo(() => {
    const names = new Map(subjects.map((s) => [s.id, s.name]));
    const bySubject = new Map<string, SavedQuiz[]>();
    for (const t of shown) {
      const key = t.subjectId && names.has(t.subjectId) ? t.subjectId : "";
      bySubject.set(key, [...(bySubject.get(key) ?? []), t]);
    }
    return [...bySubject.entries()]
      .map(([id, list]) => ({ id, name: id ? names.get(id)! : "Other", list }))
      .sort((a, b) => (a.id === "" ? 1 : b.id === "" ? -1 : a.name.localeCompare(b.name)));
  }, [shown, subjects]);

  const attempts = tests.flatMap((t) => t.attempts);
  const avg = attempts.length ? Math.round(attempts.reduce((s, a) => s + pct(a.score, a.total), 0) / attempts.length) : null;

  return (
    <div className="mx-auto flex w-full max-w-3xl flex-col gap-5 pb-10">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-gray-900">Practice</h1>
          <p className="mt-1 text-sm text-gray-500">Your practice tests, their attempts and progress.</p>
        </div>
        <Link
          href={MALAI_PATH}
          className="rounded-xl bg-violet-600 px-4 py-2 text-sm font-semibold text-white shadow-sm transition hover:bg-violet-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-violet-300"
        >
          New practice test
        </Link>
      </header>

      <section className="grid grid-cols-3 gap-3">
        <Stat label="Tests" value={String(tests.length)} />
        <Stat label="Attempts" value={String(attempts.length)} />
        <Stat label="Avg score" value={avg === null ? "—" : `${avg}%`} />
      </section>

      {tests.length === 0 ? (
        <div className="rounded-2xl border border-dashed border-gray-300 bg-white px-6 py-10 text-center">
          <p className="text-sm font-medium text-gray-900">No practice tests yet</p>
          <p className="mx-auto mt-1 max-w-sm text-sm text-gray-500">
            In a Malai chat, choose Practice test or just ask for one. Each test is saved here with its attempts and progress.
          </p>
        </div>
      ) : (
        <>
          <label className="block">
            <span className="sr-only">Search practice tests</span>
            <input
              type="search"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search practice tests…"
              className="w-full rounded-xl border border-gray-200 bg-white px-3.5 py-2.5 text-sm text-gray-900 outline-none transition focus:border-violet-400 focus:ring-2 focus:ring-violet-500/20"
            />
          </label>
          {groups.length === 0 && <p className="text-sm text-gray-500">No practice tests match your search.</p>}
          {groups.map((g) => (
            <section key={g.id || "other"} className="rounded-2xl border border-gray-200 bg-white p-2">
              <h2 className="flex items-center gap-2.5 px-3 pb-1 pt-2 text-sm font-semibold text-gray-900">
                {g.id ? (
                  <span className={`flex h-6 w-6 items-center justify-center rounded-md text-[10px] font-bold text-white ${subjectAccent(g.name).avatar}`}>
                    {initials(g.name)}
                  </span>
                ) : null}
                <span className="flex-1">{g.name}</span>
                <span className="text-xs font-normal tabular-nums text-gray-400">{g.list.length}</span>
              </h2>
              <ul className="flex flex-col">
                {g.list.map((t) => (
                  <TestRow key={t.id} test={t} />
                ))}
              </ul>
            </section>
          ))}
        </>
      )}
    </div>
  );
}
