"use client";

import { useState } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import QuizPanel, { QuestionCard } from "@/components/QuizPanel";
import { useSubjects } from "@/components/useSubjects";
import { MALAI_PATH, malaiSubjectPath } from "@/lib/malai";
import { deleteAttempt, deleteQuiz, useSavedQuizzes, type QuizAttempt, type SavedQuiz } from "@/lib/quizStore";

const pct = (score: number, total: number) => (total > 0 ? Math.round((score / total) * 100) : 0);
const fmtDate = (t: number) =>
  new Date(t).toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-xl border border-gray-200 bg-white px-4 py-3">
      <p className="text-[11px] font-medium uppercase tracking-wide text-gray-400">{label}</p>
      <p className="mt-0.5 text-xl font-semibold tabular-nums text-gray-900">{value}</p>
    </div>
  );
}

function Bar({ value, tone = "bg-violet-500" }: { value: number; tone?: string }) {
  return (
    <div className="h-2 w-full overflow-hidden rounded-full bg-gray-100">
      <div className={`h-full rounded-full ${tone}`} style={{ width: `${Math.max(2, value)}%` }} />
    </div>
  );
}

const tone = (p: number) => (p >= 80 ? "bg-emerald-500" : p >= 50 ? "bg-amber-500" : "bg-red-500");

/** Score trend and per-question accuracy over the given attempts. */
function Analytics({ quiz, attempts }: { quiz: SavedQuiz; attempts: QuizAttempt[] }) {
  if (attempts.length === 0) {
    return <p className="rounded-xl border border-dashed border-gray-200 p-4 text-sm text-gray-500">No attempts yet.</p>;
  }
  const scores = attempts.map((a) => pct(a.score, a.total));
  const best = Math.max(...scores);
  const avg = Math.round(scores.reduce((s, x) => s + x, 0) / scores.length);
  return (
    <div className="flex flex-col gap-4">
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Stat label="Attempts" value={String(attempts.length)} />
        <Stat label="Latest" value={`${scores[scores.length - 1]}%`} />
        <Stat label="Best" value={`${best}%`} />
        <Stat label="Average" value={`${avg}%`} />
      </div>
      {attempts.length > 1 && (
        <div className="rounded-xl border border-gray-200 bg-white p-4">
          <p className="mb-3 text-xs font-semibold text-gray-700">Score by attempt</p>
          <div className="flex h-28 items-end gap-2">
            {scores.map((s, i) => (
              <div key={attempts[i].id} className="flex h-full flex-1 flex-col items-center justify-end gap-1" title={`Attempt ${i + 1}: ${s}%`}>
                <span className="text-[10px] tabular-nums text-gray-500">{s}%</span>
                <div className={`w-full max-w-10 rounded-t-md ${tone(s)}`} style={{ height: `${Math.max(4, s)}%` }} />
                <span className="text-[10px] text-gray-400">#{i + 1}</span>
              </div>
            ))}
          </div>
        </div>
      )}
      <div className="rounded-xl border border-gray-200 bg-white p-4">
        <p className="mb-3 text-xs font-semibold text-gray-700">Per question{attempts.length > 1 ? " (across attempts)" : ""}</p>
        <ol className="flex flex-col gap-2.5">
          {quiz.questions.map((q, i) => {
            const p = Math.round((attempts.reduce((s, a) => s + (a.results[q.id]?.score ?? 0), 0) / attempts.length) * 100);
            return (
              <li key={q.id} className="grid grid-cols-[1.5rem_1fr_6rem_2.5rem] items-center gap-2 text-sm">
                <span className="text-xs text-gray-400">{i + 1}</span>
                <span className="truncate text-gray-700" title={q.prompt}>{q.prompt}</span>
                <Bar value={p} tone={tone(p)} />
                <span className="text-right text-xs tabular-nums text-gray-600">{p}%</span>
              </li>
            );
          })}
        </ol>
      </div>
    </div>
  );
}

export default function QuizView() {
  const params = useParams<{ quizId: string }>();
  const router = useRouter();
  const quizzes = useSavedQuizzes();
  const { subjects } = useSubjects();
  const quiz = quizzes.find((q) => q.id === decodeURIComponent(params.quizId));
  const [run, setRun] = useState(0);
  const [openAttempt, setOpenAttempt] = useState<string | null>(null);

  if (!quiz) {
    return (
      <div className="mx-auto max-w-3xl p-6 text-sm text-gray-600">
        This quiz isn&apos;t saved in this browser. <Link href={MALAI_PATH} className="text-violet-700 underline">Back to Malai</Link>
      </div>
    );
  }
  const subject = subjects.find((s) => s.id === quiz.subjectId);
  const shown = quiz.attempts.find((a) => a.id === openAttempt) ?? null;

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto flex max-w-3xl flex-col gap-6 px-4 py-6">
        <header className="flex flex-wrap items-start gap-3">
          <div className="min-w-0 flex-1">
            {subject && (
              <Link href={malaiSubjectPath(subject.id)} className="text-xs font-medium text-violet-700 hover:underline">
                {subject.name} · Quizzes
              </Link>
            )}
            <h1 className="mt-1 text-xl font-bold text-gray-900">{quiz.title}</h1>
            <p className="text-xs text-gray-500">
              {quiz.questions.length} questions · created {fmtDate(quiz.createdAt)}
            </p>
          </div>
          <button
            type="button"
            onClick={() => setRun((n) => n + 1)}
            className="rounded-xl bg-violet-600 px-4 py-2 text-sm font-medium text-white shadow-sm hover:bg-violet-700"
          >
            {quiz.attempts.length ? "Re-attempt" : "Attempt"}
          </button>
          <button
            type="button"
            onClick={() => {
              if (!window.confirm("Delete this quiz and all its attempts?")) return;
              deleteQuiz(quiz.id);
              router.push(subject ? malaiSubjectPath(subject.id) : MALAI_PATH);
            }}
            className="rounded-xl border border-gray-200 px-3 py-2 text-sm text-gray-600 hover:border-red-200 hover:bg-red-50 hover:text-red-700"
          >
            Delete quiz
          </button>
        </header>

        {run > 0 && (
          <section className="rounded-2xl border border-violet-200 bg-violet-50/40 p-4">
            <div className="mb-3 flex items-center justify-between">
              <h2 className="text-sm font-semibold text-gray-900">New attempt</h2>
              <button type="button" onClick={() => setRun(0)} className="text-xs text-gray-500 hover:text-gray-800">
                Close
              </button>
            </div>
            <QuizPanel
              key={run}
              saved={quiz}
              sessionId=""
              sessionToken=""
              getCurrentTimeMs={() => 0}
              scope={{}}
              sourceNote=""
              onSeek={() => {}}
              offline={false}
            />
          </section>
        )}

        <section>
          <h2 className="mb-3 text-sm font-semibold text-gray-900">Analytics</h2>
          <Analytics quiz={quiz} attempts={quiz.attempts} />
        </section>

        <section>
          <h2 className="mb-3 text-sm font-semibold text-gray-900">Attempts</h2>
          {quiz.attempts.length === 0 ? (
            <p className="text-sm text-gray-500">None yet.</p>
          ) : (
            <ul className="flex flex-col gap-2">
              {[...quiz.attempts].reverse().map((a) => {
                const n = quiz.attempts.indexOf(a) + 1;
                const p = pct(a.score, a.total);
                const isOpen = openAttempt === a.id;
                return (
                  <li key={a.id} className="rounded-xl border border-gray-200 bg-white">
                    <div className="flex items-center gap-3 px-3 py-2">
                      <button
                        type="button"
                        onClick={() => setOpenAttempt(isOpen ? null : a.id)}
                        aria-expanded={isOpen}
                        className="flex min-w-0 flex-1 items-center gap-3 text-left"
                      >
                        <span className="text-sm font-medium text-gray-900">Attempt {n}</span>
                        <span className="text-xs text-gray-500">{fmtDate(a.at)}</span>
                        <span className="ml-auto w-24"><Bar value={p} tone={tone(p)} /></span>
                        <span className="w-20 text-right text-xs tabular-nums text-gray-700">
                          {a.score}/{a.total} · {p}%
                        </span>
                      </button>
                      <button
                        type="button"
                        onClick={() => {
                          if (!window.confirm(`Delete attempt ${n}?`)) return;
                          deleteAttempt(quiz.id, a.id);
                          if (isOpen) setOpenAttempt(null);
                        }}
                        aria-label={`Delete attempt ${n}`}
                        title="Delete attempt"
                        className="rounded-lg p-1.5 text-gray-400 hover:bg-red-50 hover:text-red-600"
                      >
                        <svg viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true">
                          <path d="M4 7h16M10 11v6M14 11v6M5 7l1 12a2 2 0 002 2h8a2 2 0 002-2l1-12M9 7V4h6v3" />
                        </svg>
                      </button>
                    </div>
                    {isOpen && shown && (
                      <div className="flex flex-col gap-4 border-t border-gray-100 p-3">
                        <Analytics quiz={quiz} attempts={[shown]} />
                        <ol className="flex flex-col gap-3">
                          {quiz.questions.map((q, i) => (
                            <QuestionCard
                              key={q.id}
                              n={i + 1}
                              q={q}
                              answer={shown.answers[q.id] ?? ""}
                              onAnswer={() => {}}
                              result={shown.results[q.id]}
                              grading={false}
                              gradeError={null}
                              onSubmit={() => {}}
                              onSeek={() => {}}
                            />
                          ))}
                        </ol>
                      </div>
                    )}
                  </li>
                );
              })}
            </ul>
          )}
        </section>
      </div>
    </div>
  );
}
