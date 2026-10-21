"use client";

import { useState } from "react";
import {
  formatApiError,
  gradeQuizAnswer,
  postQuiz,
  type QuizGrade,
  type QuizQuestion,
  type QuizQuestionType,
  type QuizResponse,
} from "../lib/api";
import CitationBadge, { type CitationBadgeProps } from "./CitationBadge";
import MarkdownBody from "./MarkdownBody";

interface QuizPanelProps {
  sessionId: string;
  sessionToken: string;
  videoId: string;
  getCurrentTimeMs: () => number;
  completedResourceIds: string[];
  onSeek: (startMs: number) => void;
  openDoc?: CitationBadgeProps["openDoc"];
  offline: boolean;
}

type Verdict = QuizGrade["verdict"];

interface Result {
  verdict: Verdict;
  /** 0..1; partial credit only for subjective answers. */
  score: number;
  grade?: QuizGrade;
}

const TYPE_LABEL: Record<QuizQuestionType, string> = {
  mcq: "Multiple choice",
  numerical: "Numerical",
  subjective: "Written",
};

const COUNTS = [3, 5, 10];

function fmtMs(ms: number): string {
  const s = Math.max(0, Math.floor(ms / 1000));
  return `${Math.floor(s / 60)}:${(s % 60).toString().padStart(2, "0")}`;
}

/** "4", "-0.5", "1,200", "3e-2" or a simple fraction "1/2". */
export function parseNumber(raw: string): number | null {
  const s = raw.replace(/,/g, "").trim();
  if (!s) return null;
  const frac = s.match(/^(-?\d+(?:\.\d+)?)\s*\/\s*(\d+(?:\.\d+)?)$/);
  const x = frac ? Number(frac[1]) / Number(frac[2]) : Number(s);
  return Number.isFinite(x) ? x : null;
}

function fmtNumber(x: number): string {
  return Number.isInteger(x) ? String(x) : String(Number(x.toPrecision(6)));
}

/** MCQ and numerical are checked here: no request, no LLM call. */
function checkLocally(q: QuizQuestion, answer: string): Result | null {
  if (q.type === "mcq") {
    const ok = Number(answer) === q.answer_index;
    return { verdict: ok ? "correct" : "incorrect", score: ok ? 1 : 0 };
  }
  if (q.type === "numerical" && q.answer != null) {
    const x = parseNumber(answer);
    const ok = x != null && Math.abs(x - q.answer) <= (q.tolerance ?? 0);
    return { verdict: ok ? "correct" : "incorrect", score: ok ? 1 : 0 };
  }
  return null;
}

const VERDICT_STYLE: Record<Verdict, { box: string; label: string }> = {
  correct: { box: "border-emerald-200 bg-emerald-50 text-emerald-800", label: "Correct" },
  partial: { box: "border-amber-200 bg-amber-50 text-amber-800", label: "Partly right" },
  incorrect: { box: "border-red-200 bg-red-50 text-red-700", label: "Not quite" },
};

export default function QuizPanel({
  sessionId,
  sessionToken,
  videoId,
  getCurrentTimeMs,
  completedResourceIds,
  onSeek,
  openDoc,
  offline,
}: QuizPanelProps) {
  const [count, setCount] = useState(5);
  const [types, setTypes] = useState<QuizQuestionType[]>(["mcq", "numerical", "subjective"]);
  const [attempt, setAttempt] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [quiz, setQuiz] = useState<QuizResponse | null>(null);
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [results, setResults] = useState<Record<string, Result>>({});
  const [grading, setGrading] = useState<Record<string, boolean>>({});
  const [gradeErrors, setGradeErrors] = useState<Record<string, string>>({});

  function toggleType(t: QuizQuestionType) {
    setTypes((cur) =>
      cur.includes(t) ? (cur.length > 1 ? cur.filter((x) => x !== t) : cur) : [...cur, t],
    );
  }

  async function generate() {
    setLoading(true);
    setError(null);
    try {
      const res = await postQuiz(
        {
          session_id: sessionId,
          video_id: videoId,
          current_time_ms: Math.round(getCurrentTimeMs()),
          completed_resource_ids: completedResourceIds,
          count,
          types,
          attempt,
        },
        sessionToken,
      );
      setAttempt((a) => a + 1);
      setAnswers({});
      setResults({});
      setGradeErrors({});
      if (res.refused) {
        setQuiz(null);
        setError(res.message ?? "Couldn't write a quiz from this material yet.");
      } else {
        setQuiz(res);
      }
    } catch (err) {
      setError(formatApiError(err));
    } finally {
      setLoading(false);
    }
  }

  async function submit(q: QuizQuestion) {
    const answer = (answers[q.id] ?? "").trim();
    if (!answer || results[q.id] || grading[q.id]) return;
    const local = checkLocally(q, answer);
    if (local) {
      setResults((r) => ({ ...r, [q.id]: local }));
      return;
    }
    setGrading((g) => ({ ...g, [q.id]: true }));
    setGradeErrors((e) => ({ ...e, [q.id]: "" }));
    try {
      const grade = await gradeQuizAnswer(sessionId, q, answer, sessionToken);
      setResults((r) => ({ ...r, [q.id]: { verdict: grade.verdict, score: grade.score, grade } }));
    } catch (err) {
      setGradeErrors((e) => ({ ...e, [q.id]: formatApiError(err) }));
    } finally {
      setGrading((g) => ({ ...g, [q.id]: false }));
    }
  }

  if (offline) {
    return (
      <div className="rounded-xl border border-amber-200 bg-amber-50 p-3 text-sm text-amber-700">
        Quizzes need the live backend, which isn&apos;t reachable right now.
      </div>
    );
  }

  const questions = quiz?.questions ?? [];
  const checked = questions.filter((q) => results[q.id]);
  const score = checked.reduce((s, q) => s + results[q.id].score, 0);
  const pending = questions.filter(
    (q) => !results[q.id] && !grading[q.id] && (answers[q.id] ?? "").trim(),
  );
  const done = questions.length > 0 && checked.length === questions.length;
  const extra = completedResourceIds.length;

  return (
    <div className="flex flex-col gap-3">
      {/* Setup: always visible, so "New quiz" is one click away. */}
      <div className="rounded-xl border border-gray-200 bg-gray-50 p-3">
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
          <div className="flex items-center gap-1.5" role="group" aria-label="Number of questions">
            <span className="text-xs text-gray-500">Questions</span>
            {COUNTS.map((n) => (
              <button
                key={n}
                type="button"
                aria-pressed={count === n}
                onClick={() => setCount(n)}
                disabled={loading}
                className={`rounded-full border px-2.5 py-0.5 text-xs font-medium transition ${
                  count === n
                    ? "border-violet-600 bg-violet-600 text-white"
                    : "border-gray-200 bg-white text-gray-600 hover:border-violet-200 hover:text-violet-700"
                }`}
              >
                {n}
              </button>
            ))}
          </div>
          <div className="flex flex-wrap items-center gap-1.5" role="group" aria-label="Question types">
            <span className="text-xs text-gray-500">Types</span>
            {(Object.keys(TYPE_LABEL) as QuizQuestionType[]).map((t) => (
              <button
                key={t}
                type="button"
                aria-pressed={types.includes(t)}
                onClick={() => toggleType(t)}
                disabled={loading}
                className={`rounded-full border px-2.5 py-0.5 text-xs font-medium transition ${
                  types.includes(t)
                    ? "border-violet-300 bg-violet-100 text-violet-700"
                    : "border-gray-200 bg-white text-gray-400 hover:text-gray-600"
                }`}
              >
                {types.includes(t) ? "✓ " : ""}
                {TYPE_LABEL[t]}
              </button>
            ))}
          </div>
        </div>
        <div className="mt-3 flex items-center justify-between gap-2">
          <p className="text-[11px] leading-snug text-gray-500">
            From this material up to where you are
            {extra > 0 ? ` + ${extra} completed material${extra === 1 ? "" : "s"}` : ""}.
          </p>
          <button
            type="button"
            onClick={() => void generate()}
            disabled={loading}
            className="shrink-0 rounded-xl bg-violet-600 px-4 py-1.5 text-sm font-medium text-white transition hover:bg-violet-700 disabled:opacity-50"
          >
            {loading ? "Writing quiz…" : quiz ? "New quiz" : "Start quiz"}
          </button>
        </div>
      </div>

      {error && (
        <div className="rounded-xl border border-amber-200 bg-amber-50 p-3 text-sm text-amber-700" role="alert">
          {error}
        </div>
      )}

      {loading && !quiz && (
        <div className="space-y-2" role="status" aria-label="Writing quiz">
          {[0, 1, 2].map((i) => (
            <div key={i} className="h-24 animate-pulse rounded-xl bg-gray-100" />
          ))}
        </div>
      )}

      {quiz && questions.length > 0 && (
        <>
          <div className="flex items-center justify-between gap-2 rounded-xl border border-violet-100 bg-violet-50 px-3 py-2">
            <p className="text-sm text-violet-900">
              <span className="font-semibold">
                {fmtNumber(Math.round(score * 10) / 10)} / {questions.length}
              </span>{" "}
              <span className="text-violet-700">
                · {checked.length} of {questions.length} checked · up to {fmtMs(quiz.current_time_ms)}
              </span>
            </p>
            {!done && pending.length > 0 && (
              <button
                type="button"
                onClick={() => pending.forEach((q) => void submit(q))}
                className="rounded-lg border border-violet-300 bg-white px-2.5 py-1 text-xs font-medium text-violet-700 hover:bg-violet-100"
              >
                Submit all ({pending.length})
              </button>
            )}
          </div>

          <ol className="flex flex-col gap-3">
            {questions.map((q, i) => (
              <QuestionCard
                key={`${attempt}-${q.id}`}
                n={i + 1}
                q={q}
                answer={answers[q.id] ?? ""}
                onAnswer={(v) => setAnswers((a) => ({ ...a, [q.id]: v }))}
                result={results[q.id]}
                grading={!!grading[q.id]}
                gradeError={gradeErrors[q.id] || null}
                onSubmit={() => void submit(q)}
                onSeek={onSeek}
                openDoc={openDoc}
              />
            ))}
          </ol>

          {done && (
            <div className="rounded-xl border border-gray-200 bg-white p-3 text-center text-sm text-gray-700">
              Finished: <span className="font-semibold">{fmtNumber(Math.round(score * 10) / 10)}</span> out of{" "}
              {questions.length}. Use <span className="font-medium">New quiz</span> for a different set.
            </div>
          )}
        </>
      )}

      {!quiz && !loading && !error && (
        <p className="px-1 text-sm text-gray-500">
          Test yourself on what you&apos;ve covered. Multiple-choice and numerical answers are checked
          instantly; written answers get feedback from Malai.
        </p>
      )}
    </div>
  );
}

interface QuestionCardProps {
  n: number;
  q: QuizQuestion;
  answer: string;
  onAnswer: (v: string) => void;
  result?: Result;
  grading: boolean;
  gradeError: string | null;
  onSubmit: () => void;
  onSeek: (startMs: number) => void;
  openDoc?: CitationBadgeProps["openDoc"];
}

function QuestionCard({
  n,
  q,
  answer,
  onAnswer,
  result,
  grading,
  gradeError,
  onSubmit,
  onSeek,
  openDoc,
}: QuestionCardProps) {
  const locked = !!result || grading;
  const numberInvalid = q.type === "numerical" && answer.trim() !== "" && parseNumber(answer) == null;
  const canSubmit = answer.trim() !== "" && !locked && !numberInvalid;

  return (
    <li className="rounded-xl border border-gray-200 bg-white p-3 shadow-sm">
      <div className="mb-1.5 flex items-center gap-2">
        <span className="flex h-5 w-5 items-center justify-center rounded-full bg-gray-100 text-[11px] font-semibold text-gray-600">
          {n}
        </span>
        <span className="text-[11px] font-medium uppercase tracking-wide text-gray-400">{TYPE_LABEL[q.type]}</span>
      </div>
      <MarkdownBody text={q.prompt} className="text-sm text-gray-900" />

      {q.type === "mcq" && q.options && (
        <div className="mt-2 flex flex-col gap-1.5" role="radiogroup" aria-label={`Question ${n} options`}>
          {q.options.map((opt, i) => {
            const chosen = answer === String(i);
            const isAnswer = i === q.answer_index;
            let style = chosen
              ? "border-violet-400 bg-violet-50"
              : "border-gray-200 bg-white hover:border-violet-200 hover:bg-violet-50/50";
            if (result) {
              style = isAnswer
                ? "border-emerald-300 bg-emerald-50"
                : chosen
                  ? "border-red-300 bg-red-50"
                  : "border-gray-200 bg-white opacity-70";
            }
            return (
              <label
                key={i}
                className={`flex cursor-pointer items-start gap-2 rounded-lg border px-2.5 py-1.5 text-sm transition ${style} ${
                  locked ? "cursor-default" : ""
                }`}
              >
                <input
                  type="radio"
                  name={`quiz-${q.id}`}
                  className="mt-1 accent-violet-600"
                  checked={chosen}
                  disabled={locked}
                  onChange={() => onAnswer(String(i))}
                />
                <span className="mt-0.5 w-4 shrink-0 text-xs font-semibold text-gray-400">
                  {String.fromCharCode(65 + i)}
                </span>
                <MarkdownBody text={opt} className="min-w-0 flex-1 text-gray-800" />
                {result && isAnswer && <span className="text-xs font-medium text-emerald-700">✓</span>}
              </label>
            );
          })}
        </div>
      )}

      {q.type === "numerical" && (
        <div className="mt-2 flex items-center gap-2">
          <input
            type="text"
            inputMode="decimal"
            value={answer}
            onChange={(e) => onAnswer(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && canSubmit) onSubmit();
            }}
            disabled={locked}
            placeholder="Your answer"
            aria-label={`Answer to question ${n}`}
            className={`w-40 rounded-lg border bg-gray-50 px-2.5 py-1.5 text-sm outline-none focus:bg-white focus:ring-2 focus:ring-violet-200 ${
              numberInvalid ? "border-red-300" : "border-gray-200 focus:border-violet-400"
            }`}
          />
          {q.unit && <span className="text-sm text-gray-500">{q.unit}</span>}
          {numberInvalid && <span className="text-xs text-red-600">Enter a number (e.g. 4, -0.5, 1/2)</span>}
        </div>
      )}

      {q.type === "subjective" && (
        <textarea
          value={answer}
          onChange={(e) => onAnswer(e.target.value)}
          disabled={locked}
          rows={3}
          maxLength={4000}
          placeholder="Write your answer in a few sentences…"
          aria-label={`Answer to question ${n}`}
          className="mt-2 w-full resize-y rounded-lg border border-gray-200 bg-gray-50 px-2.5 py-1.5 text-sm outline-none focus:border-violet-400 focus:bg-white focus:ring-2 focus:ring-violet-200"
        />
      )}

      {!result && (
        <div className="mt-2 flex items-center justify-end gap-2">
          {gradeError && <span className="text-xs text-red-600">{gradeError}</span>}
          <button
            type="button"
            onClick={onSubmit}
            disabled={!canSubmit}
            className="rounded-lg bg-violet-600 px-3 py-1 text-xs font-medium text-white transition hover:bg-violet-700 disabled:opacity-40"
          >
            {grading ? "Checking…" : gradeError ? "Try again" : "Submit"}
          </button>
        </div>
      )}

      {result && <Reveal q={q} result={result} onSeek={onSeek} openDoc={openDoc} />}
    </li>
  );
}

function Reveal({
  q,
  result,
  onSeek,
  openDoc,
}: {
  q: QuizQuestion;
  result: Result;
  onSeek: (startMs: number) => void;
  openDoc?: CitationBadgeProps["openDoc"];
}) {
  const style = VERDICT_STYLE[result.verdict];
  return (
    <div className={`mt-2 rounded-lg border p-2.5 text-sm ${style.box}`}>
      <p className="font-semibold">
        {style.label}
        {q.type === "subjective" && result.grade ? ` · ${Math.round(result.grade.score * 100)}%` : ""}
      </p>
      {q.type === "numerical" && q.answer != null && (
        <p className="mt-1">
          Answer: <span className="font-mono">{fmtNumber(q.answer)}</span>
          {q.unit ? ` ${q.unit}` : ""}
          {q.tolerance ? <span className="text-xs opacity-70"> (± {fmtNumber(q.tolerance)})</span> : null}
        </p>
      )}
      {result.grade && <p className="mt-1">{result.grade.feedback}</p>}
      {result.grade && result.grade.missing.length > 0 && (
        <ul className="mt-1 list-disc pl-5 text-xs">
          {result.grade.missing.map((m) => (
            <li key={m}>Missing: {m}</li>
          ))}
        </ul>
      )}
      {q.type === "subjective" && q.model_answer && (
        <div className="mt-2 rounded-md bg-white/70 p-2 text-gray-800">
          <p className="mb-0.5 text-[11px] font-medium uppercase tracking-wide text-gray-500">Model answer</p>
          <MarkdownBody text={q.model_answer} />
        </div>
      )}
      {q.explanation && <MarkdownBody text={q.explanation} className="mt-1 text-gray-700" />}
      <div className="mt-2 flex flex-wrap items-center gap-1.5">
        <span className="text-[11px] text-gray-500">Source</span>
        <CitationBadge citation={q.citation} onSeek={onSeek} openDoc={openDoc} />
      </div>
    </div>
  );
}
