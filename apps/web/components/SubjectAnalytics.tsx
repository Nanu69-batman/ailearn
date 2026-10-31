"use client";

import Link from "next/link";
import type { CourseResource } from "@/lib/api";
import { quizPath, useSavedQuizzes } from "@/lib/quizStore";
import { useSubjectCompletion, useWatchProgress, watchFraction, type WatchProgress } from "@/lib/progress";
import { formatStudyTime } from "@/lib/activity";

/**
 * A subject's learning analytics: overall completion (completed resources,
 * plus how far each unfinished video has been watched), and its quizzes
 * (attempts, average and best score, the latest scores as bars).
 * Shown on the subject page in My subjects and on its Malai page.
 */

export function subjectCompletion(
  resources: CourseResource[],
  done: readonly string[],
  watch: Record<string, WatchProgress>,
): number {
  if (resources.length === 0) return 0;
  let sum = 0;
  for (const r of resources) {
    if (done.includes(r.id)) sum += 1;
    else sum += watchFraction(watch[r.id]) ?? 0;
  }
  return Math.round((sum / resources.length) * 100);
}

function pct(score: number, total: number): number {
  return total > 0 ? Math.round((score / total) * 100) : 0;
}

export default function SubjectAnalytics({
  subjectId,
  resources,
  className = "",
}: {
  subjectId: string;
  resources: CourseResource[];
  className?: string;
}) {
  const done = useSubjectCompletion(subjectId);
  const watch = useWatchProgress();
  const quizzes = useSavedQuizzes().filter((q) => q.subjectId === subjectId);

  const completion = subjectCompletion(resources, done, watch);
  const completed = resources.filter((r) => done.includes(r.id)).length;
  const watchedSeconds = resources.reduce((s, r) => s + (watch[r.id]?.ms ?? 0) / 1000, 0);

  const attempts = quizzes
    .flatMap((q) => q.attempts.map((a) => ({ quiz: q, at: a.at, p: pct(a.score, a.total) })))
    .sort((a, b) => a.at - b.at);
  const avg = attempts.length ? Math.round(attempts.reduce((s, a) => s + a.p, 0) / attempts.length) : null;
  const best = attempts.length ? Math.max(...attempts.map((a) => a.p)) : null;
  const recent = attempts.slice(-10);
  const recentQuizzes = [...quizzes].sort((a, b) => (b.attempts.at(-1)?.at ?? b.createdAt) - (a.attempts.at(-1)?.at ?? a.createdAt)).slice(0, 3);

  const card = "rounded-2xl border border-gray-200 bg-white shadow-sm";
  const stat = "rounded-xl bg-gray-50 px-3 py-2.5";

  return (
    <section className={`${card} ${className}`} aria-labelledby={`analytics-${subjectId}`}>
      <h2 id={`analytics-${subjectId}`} className="border-b border-gray-100 px-5 py-3.5 text-sm font-semibold text-gray-900">
        Progress &amp; analytics
      </h2>
      <div className="grid gap-5 p-5 md:grid-cols-2">
        <div>
          <div className="flex items-baseline justify-between">
            <span className="text-sm font-medium text-gray-700">Subject completion</span>
            <span className="text-2xl font-bold tabular-nums text-gray-900">{completion}%</span>
          </div>
          <div
            className="mt-2 h-2.5 w-full overflow-hidden rounded-full bg-gray-100"
            role="progressbar"
            aria-valuenow={completion}
            aria-valuemin={0}
            aria-valuemax={100}
            aria-label="Subject completion"
          >
            <div className="h-full rounded-full bg-emerald-500 transition-[width] duration-500" style={{ width: `${completion}%` }} />
          </div>
          <div className="mt-3 grid grid-cols-2 gap-2 text-xs text-gray-500">
            <div className={stat}>
              <div className="text-base font-semibold tabular-nums text-gray-900">
                {completed}/{resources.length}
              </div>
              resources completed
            </div>
            <div className={stat}>
              <div className="text-base font-semibold tabular-nums text-gray-900">{formatStudyTime(watchedSeconds)}</div>
              of video watched
            </div>
          </div>
          <p className="mt-2 text-[11px] text-gray-400">Unfinished videos count by how far you&apos;ve watched.</p>
        </div>

        <div>
          <div className="grid grid-cols-3 gap-2 text-xs text-gray-500">
            <div className={stat}>
              <div className="text-base font-semibold tabular-nums text-gray-900">{quizzes.length}</div>
              quiz{quizzes.length === 1 ? "" : "zes"}
            </div>
            <div className={stat}>
              <div className="text-base font-semibold tabular-nums text-gray-900">{avg === null ? "—" : `${avg}%`}</div>
              average
            </div>
            <div className={stat}>
              <div className="text-base font-semibold tabular-nums text-gray-900">{best === null ? "—" : `${best}%`}</div>
              best
            </div>
          </div>
          {recent.length > 0 ? (
            <div className="mt-3">
              <p className="text-[11px] font-medium text-gray-500">
                Latest {recent.length} attempt{recent.length === 1 ? "" : "s"} · {attempts.length} in all
              </p>
              <div className="mt-1.5 flex h-16 items-end gap-1" role="img" aria-label={`Latest quiz scores: ${recent.map((a) => `${a.p}%`).join(", ")}`}>
                {recent.map((a, i) => (
                  <div
                    key={`${a.quiz.id}-${a.at}-${i}`}
                    title={`${a.quiz.title}: ${a.p}%`}
                    className={`min-h-[3px] flex-1 rounded-t ${a.p >= 70 ? "bg-emerald-400" : a.p >= 40 ? "bg-amber-400" : "bg-rose-400"}`}
                    style={{ height: `${Math.max(4, a.p)}%` }}
                  />
                ))}
              </div>
            </div>
          ) : (
            <p className="mt-3 text-xs text-gray-500">No quiz attempts yet. Ask Malai to quiz you on this subject.</p>
          )}
          {recentQuizzes.length > 0 && (
            <ul className="mt-3 flex flex-col gap-1">
              {recentQuizzes.map((q) => {
                const last = q.attempts.at(-1);
                return (
                  <li key={q.id}>
                    <Link
                      href={quizPath(q.id)}
                      className="flex items-center gap-2 rounded-lg px-2 py-1.5 text-xs transition hover:bg-violet-50"
                    >
                      <span className="min-w-0 flex-1 truncate font-medium text-gray-800">{q.title}</span>
                      <span className="shrink-0 tabular-nums text-gray-500">
                        {last ? `${pct(last.score, last.total)}% · ${q.attempts.length} attempt${q.attempts.length === 1 ? "" : "s"}` : "not taken"}
                      </span>
                    </Link>
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      </div>
    </section>
  );
}
