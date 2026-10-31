"use client";

import { useEffect, useRef, useState } from "react";
import {
  MAX_QUIZ_QUESTIONS,
  formatApiError,
  gradeQuizAnswer,
  postQuiz,
  type QuizGrade,
  type QuizQuestion,
  type QuizQuestionType,
  type QuizResponse,
  type QuizScope,
} from "../lib/api";
import CitationBadge, { type CitationBadgeProps } from "./CitationBadge";
import { pushQuizAttempt } from "@/lib/activitySync";
import { addAttempt, saveQuiz, type SavedQuiz } from "@/lib/quizStore";
import MarkdownBody from "./MarkdownBody";
import Select from "./Select";
import {
  MODEL_MODES,
  loadModelSettings,
  loadQuizModel,
  modelHeadersFor,
  saveQuizModel,
  useModelSettings,
  type QuizModel,
} from "@/lib/modelMode";

/** Settings for a quiz written right away: from /quiz, or a chat message. */
export interface QuizPreset {
  count: number;
  types: QuizQuestionType[];
  /** Instructions for the quiz ("on entropy, harder"); may be empty. */
  prompt: string;
  /** The chat message that asked for it, when it came from the chat. */
  request?: string;
  nonce: number;
}

/** A finished quiz, as reported to the chat it belongs to. */
export interface QuizOutcome {
  count: number;
  score: number;
  /** The questions not fully right, for Malai to follow up on. */
  toReview: string[];
}

interface QuizPanelProps {
  sessionId: string;
  getCurrentTimeMs: () => number;
  /** What the quiz may draw from: the asking chat's own reach. */
  scope: QuizScope;
  /** One line under the form saying what the quiz is drawn from. */
  sourceNote: string;
  onSeek: (startMs: number) => void;
  openDoc?: CitationBadgeProps["openDoc"];
  offline: boolean;
  /** Write a quiz with these settings now. A new nonce starts a new one. */
  preset?: QuizPreset | null;
  /** A quiz asked for in the chat was written. */
  onStarted?: (request: string, count: number) => void;
  /** A quiz asked for in the chat could not be written. */
  onFailed?: (request: string, message: string) => void;
  /** Every answer of the current quiz was submitted. */
  onFinished?: (outcome: QuizOutcome) => void;
  /** The subject a new quiz is saved under (Resources > Quizzes). */
  subjectId?: string | null;
  /** The Malai chat it is taken in; a quiz with no subject is listed in its menu. */
  chatId?: string | null;
  /** Re-attempt this saved quiz: no form, attempts are added to it. */
  saved?: SavedQuiz;
}

type Verdict = QuizGrade["verdict"];

export interface Result {
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

const ALL_TYPES = Object.keys(TYPE_LABEL) as QuizQuestionType[];

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

/** The question count typed in the form, or null when it is not 1..MAX. */
function parseCount(raw: string): number | null {
  const n = Number(raw.trim());
  return Number.isInteger(n) && n >= 1 && n <= MAX_QUIZ_QUESTIONS ? n : null;
}

function typesLabel(types: QuizQuestionType[]): string {
  return types.length === ALL_TYPES.length ? "Mixed" : types.map((t) => TYPE_LABEL[t]).join(", ");
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
  getCurrentTimeMs,
  scope,
  sourceNote,
  onSeek,
  openDoc,
  offline,
  preset,
  onStarted,
  onFailed,
  onFinished,
  subjectId = null,
  chatId = null,
  saved,
}: QuizPanelProps) {
  // The form. The count is kept as typed so the box can be cleared and retyped.
  const [countText, setCountText] = useState("5");
  const [types, setTypes] = useState<QuizQuestionType[]>(ALL_TYPES);
  const [prompt, setPrompt] = useState("");
  const [formOpen, setFormOpen] = useState(!saved);
  const [attempt, setAttempt] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [quiz, setQuiz] = useState<QuizResponse | null>(
    saved ? { questions: saved.questions, refused: false, current_time_ms: 0 } : null,
  );
  /** The saved quiz the shown one's attempts are recorded in. */
  const savedId = useRef<string | null>(saved?.id ?? null);
  /** Settings the shown quiz was written with, for its summary line. */
  const [written, setWritten] = useState<{ count: number; types: QuizQuestionType[]; prompt: string } | null>(null);
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [results, setResults] = useState<Record<string, Result>>({});
  const [grading, setGrading] = useState<Record<string, boolean>>({});
  const [gradeErrors, setGradeErrors] = useState<Record<string, string>>({});
  /** Which model writes the quiz; a quiz asked for in the chat uses the chat's. */
  const [quizModel, setQuizModel] = useState<QuizModel>("default");
  const modelSettings = useModelSettings();
  const hasOwnKey = Boolean(modelSettings.key && modelSettings.model);
  /** The quiz (by attempt) whose finish was already reported. */
  const reported = useRef<number | null>(null);
  /** The preset already acted on: a remount or a re-run must not write it twice. */
  const usedPreset = useRef<number | null>(null);

  const count = parseCount(countText);

  useEffect(() => setQuizModel(loadQuizModel()), []);

  function chooseModel(m: QuizModel) {
    setQuizModel(m);
    saveQuizModel(m);
  }

  function toggleType(t: QuizQuestionType) {
    setTypes((cur) =>
      cur.includes(t) ? (cur.length > 1 ? cur.filter((x) => x !== t) : cur) : [...cur, t],
    );
  }

  // A new preset (new nonce) fills the form and writes the quiz.
  useEffect(() => {
    if (!preset || usedPreset.current === preset.nonce) return;
    usedPreset.current = preset.nonce;
    setCountText(String(preset.count));
    setTypes(preset.types);
    setPrompt(preset.prompt);
    // Asked for in the chat: the model the chat is set to writes it.
    const chatModel = loadModelSettings().mode;
    setQuizModel(chatModel);
    if (offline) {
      if (preset.request) onFailed?.(preset.request, "Quizzes need the live backend, which isn't reachable right now.");
      return;
    }
    void generate({ count: preset.count, types: preset.types, prompt: preset.prompt }, preset.request, chatModel);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [preset?.nonce]);

  async function generate(
    settings: { count: number; types: QuizQuestionType[]; prompt: string },
    request?: string,
    model: QuizModel = quizModel,
  ) {
    setLoading(true);
    setError(null);
    try {
      const res = await postQuiz(
        {
          session_id: sessionId,
          current_time_ms: Math.round(getCurrentTimeMs()),
          ...scope,
          count: settings.count,
          types: settings.types,
          attempt,
          ...(settings.prompt.trim() ? { prompt: settings.prompt.trim() } : {}),
          ...(model !== "default" ? { use_chosen_model: true } : {}),
        },
        model !== "default" ? modelHeadersFor(model) : undefined,
      );
      setAttempt((a) => a + 1);
      setAnswers({});
      setResults({});
      setGradeErrors({});
      if (res.refused) {
        const message = res.message ?? "Couldn't write a quiz from this material yet.";
        setQuiz(null);
        setError(message);
        if (request) onFailed?.(request, message);
      } else {
        setQuiz(res);
        setWritten(settings);
        savedId.current = saveQuiz({
          subjectId,
          chatId,
          questions: res.questions,
          title: settings.prompt.trim() || res.questions[0]?.prompt.slice(0, 80) || "Quiz",
        });
        setFormOpen(false);
        if (request) onStarted?.(request, res.questions.length);
      }
    } catch (err) {
      const message = formatApiError(err);
      setError(message);
      if (request) onFailed?.(request, message);
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
      const grade = await gradeQuizAnswer(sessionId, q, answer);
      setResults((r) => ({ ...r, [q.id]: { verdict: grade.verdict, score: grade.score, grade } }));
    } catch (err) {
      setGradeErrors((e) => ({ ...e, [q.id]: formatApiError(err) }));
    } finally {
      setGrading((g) => ({ ...g, [q.id]: false }));
    }
  }

  const questions = quiz?.questions ?? [];
  const checked = questions.filter((q) => results[q.id]);
  const score = checked.reduce((s, q) => s + results[q.id].score, 0);
  const pending = questions.filter(
    (q) => !results[q.id] && !grading[q.id] && (answers[q.id] ?? "").trim(),
  );
  const done = questions.length > 0 && checked.length === questions.length;

  // Report a finished quiz once, so its chat can say so.
  useEffect(() => {
    if (!done || reported.current === attempt) return;
    reported.current = attempt;
    if (savedId.current) {
      addAttempt(savedId.current, {
        answers,
        results,
        score: Math.round(score * 10) / 10,
        total: questions.length,
      });
    }
    void pushQuizAttempt({
      resourceId: scope.video_id ?? null,
      subjectId: null,
      score: Math.round(score * 10) / 10,
      total: questions.length,
    });
    onFinished?.({
      count: questions.length,
      score: Math.round(score * 10) / 10,
      toReview: questions.filter((q) => results[q.id].score < 1).map((q) => q.prompt.slice(0, 280)),
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [done, attempt]);

  if (offline) {
    return (
      <div className="rounded-xl border border-amber-200 bg-amber-50 p-3 text-sm text-amber-700">
        Quizzes need the live backend, which isn&apos;t reachable right now.
      </div>
    );
  }

  const field =
    "w-full rounded-xl border border-gray-200 bg-white px-3 py-2 text-sm text-gray-900 outline-none transition placeholder:text-gray-400 focus:border-violet-400 focus:ring-4 focus:ring-violet-100 disabled:opacity-60";

  return (
    <div className="flex flex-col gap-3">
      {formOpen || !quiz ? (
        <form
          className="rounded-2xl border border-gray-200 bg-gray-50/70 p-4"
          aria-label="New quiz"
          onSubmit={(e) => {
            e.preventDefault();
            if (count !== null && !loading) void generate({ count, types, prompt });
          }}
        >
          <h3 className="text-sm font-semibold text-gray-900">New quiz</h3>
          <p className="mt-0.5 text-xs text-gray-500">{sourceNote}</p>

          <div className="mt-4 grid gap-4 sm:grid-cols-[8rem_1fr]">
            <label className="block">
              <span className="mb-1.5 block text-xs font-medium text-gray-700">Questions</span>
              <input
                type="number"
                inputMode="numeric"
                min={1}
                max={MAX_QUIZ_QUESTIONS}
                step={1}
                value={countText}
                onChange={(e) => setCountText(e.target.value)}
                onBlur={() => {
                  const n = Math.round(Number(countText));
                  if (Number.isFinite(n) && countText.trim() !== "") {
                    setCountText(String(Math.min(MAX_QUIZ_QUESTIONS, Math.max(1, n))));
                  }
                }}
                disabled={loading}
                aria-invalid={count === null}
                aria-describedby="quiz-count-help"
                className={`${field} tabular-nums ${count === null ? "border-red-300" : ""}`}
              />
              <span id="quiz-count-help" className={`mt-1 block text-[11px] ${count === null ? "text-red-600" : "text-gray-400"}`}>
                1 to {MAX_QUIZ_QUESTIONS}
              </span>
            </label>

            <fieldset>
              <legend className="mb-1.5 block text-xs font-medium text-gray-700">Question types</legend>
              <div className="flex flex-wrap gap-2">
                {ALL_TYPES.map((t) => {
                  const on = types.includes(t);
                  return (
                    <label
                      key={t}
                      className={`inline-flex cursor-pointer items-center gap-2 rounded-xl border px-3 py-2 text-sm transition ${
                        on
                          ? "border-violet-300 bg-violet-50 text-violet-800"
                          : "border-gray-200 bg-white text-gray-500 hover:border-violet-200"
                      } ${loading ? "pointer-events-none opacity-60" : ""}`}
                    >
                      <input
                        type="checkbox"
                        checked={on}
                        onChange={() => toggleType(t)}
                        disabled={loading}
                        className="h-4 w-4 rounded accent-violet-600"
                      />
                      {TYPE_LABEL[t]}
                    </label>
                  );
                })}
              </div>
            </fieldset>
          </div>

          <label className="mt-4 block">
            <span className="mb-1.5 flex items-baseline justify-between text-xs font-medium text-gray-700">
              Instructions
              <span className="font-normal text-gray-400">Optional</span>
            </span>
            <textarea
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              disabled={loading}
              rows={2}
              maxLength={500}
              placeholder="e.g. focus on the chain rule, make them exam-level…"
              className={`${field} resize-y`}
            />
          </label>

          <div className="mt-4">
            <span className="mb-1.5 block text-xs font-medium text-gray-700">Model</span>
            <Select<QuizModel>
              label="Model"
              value={quizModel === "custom" && !hasOwnKey ? "default" : quizModel}
              onChange={chooseModel}
              options={[
                { value: "default", label: "Default (fastest)" },
                ...MODEL_MODES.map((m) => ({ value: m.id as QuizModel, label: `${m.label} · ${m.hint}` })),
                ...(hasOwnKey ? [{ value: "custom" as QuizModel, label: `Own key · ${modelSettings.modelName || modelSettings.model}` }] : []),
              ]}
              className={loading ? "pointer-events-none opacity-60" : ""}
            />
          </div>

          <div className="mt-4 flex items-center justify-end gap-2">
            {quiz && (
              <button
                type="button"
                onClick={() => setFormOpen(false)}
                disabled={loading}
                className="rounded-xl px-3 py-2 text-sm font-medium text-gray-600 transition hover:bg-gray-100"
              >
                Cancel
              </button>
            )}
            <button
              type="submit"
              disabled={loading || count === null}
              className="inline-flex items-center gap-2 rounded-xl bg-violet-600 px-4 py-2 text-sm font-medium text-white shadow-sm shadow-violet-600/20 transition hover:bg-violet-700 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {loading && <span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-white border-t-transparent" aria-hidden="true" />}
              {loading ? "Writing quiz…" : "Generate quiz"}
            </button>
          </div>
        </form>
      ) : (
        <div className="flex items-center gap-3 rounded-2xl border border-gray-200 bg-gray-50/70 px-4 py-2.5">
          <p className="min-w-0 flex-1 truncate text-xs text-gray-600">
            <span className="font-medium text-gray-800">{questions.length} questions</span>
            {written ? ` · ${typesLabel(written.types)}` : ""}
            {written?.prompt.trim() ? ` · “${written.prompt.trim()}”` : ""}
          </p>
          {!saved && <button
            type="button"
            onClick={() => setFormOpen(true)}
            disabled={loading}
            className="shrink-0 rounded-lg border border-violet-200 bg-white px-3 py-1 text-xs font-medium text-violet-700 transition hover:bg-violet-50"
          >
            New quiz
          </button>}
        </div>
      )}

      {error && (
        <div className="rounded-xl border border-amber-200 bg-amber-50 p-3 text-sm text-amber-700" role="alert">
          {error}
        </div>
      )}

      {loading && (
        <div className="space-y-2" role="status" aria-label="Writing quiz">
          {[0, 1, 2].map((i) => (
            <div key={i} className="h-24 animate-pulse rounded-xl bg-gray-100" />
          ))}
        </div>
      )}

      {!loading && quiz && questions.length > 0 && (
        <>
          <div className="flex items-center justify-between gap-2 rounded-xl border border-violet-100 bg-violet-50 px-3 py-2">
            <p className="text-sm text-violet-900">
              <span className="font-semibold">
                {fmtNumber(Math.round(score * 10) / 10)} / {questions.length}
              </span>{" "}
              <span className="text-violet-700">
                · {checked.length} of {questions.length} checked
                {scope.video_id ? ` · up to ${fmtMs(quiz.current_time_ms)}` : ""}
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
              {questions.length}.{" "}
              {saved ? "Saved to this quiz's attempts." : <>Saved under Resources &gt; Quizzes. Use <span className="font-medium">New quiz</span> for a different set.</>}
            </div>
          )}
        </>
      )}

      {!quiz && !loading && !error && (
        <p className="px-1 text-xs leading-relaxed text-gray-500">
          Multiple-choice and numerical answers are checked instantly; written answers get feedback from Malai.
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

export function QuestionCard({
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
      {q.citation ? (
        <div className="mt-2 flex flex-wrap items-center gap-1.5">
          <span className="text-[11px] text-gray-500">Source</span>
          <CitationBadge citation={q.citation} onSeek={onSeek} openDoc={openDoc} />
        </div>
      ) : (
        <p className="mt-2 text-[11px] text-gray-500">General knowledge</p>
      )}
    </div>
  );
}
