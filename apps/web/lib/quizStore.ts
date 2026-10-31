"use client";

/**
 * Saved quizzes and their attempts. Each quiz is filed under its subject
 * (Resources > Quizzes in Malai's sidebar) or, in a chat with no subject,
 * under that chat's menu, and has its own page with analytics, a re-attempt,
 * and every past attempt.
 *
 * Kept in Supabase (`saved_quizzes`, supabase/migrations/0004) for a
 * signed-in learner, mirrored to localStorage so the UI reads synchronously
 * and a signed-out browser still keeps its own.
 */
import { useEffect, useSyncExternalStore } from "react";
import type { QuizGrade, QuizQuestion } from "@/lib/api";
import { getSupabaseBrowserClient } from "@/lib/supabase/client";

export interface QuizAttemptResult {
  verdict: QuizGrade["verdict"];
  score: number;
  grade?: QuizGrade;
}

export interface QuizAttempt {
  id: string;
  at: number;
  answers: Record<string, string>;
  results: Record<string, QuizAttemptResult>;
  score: number;
  total: number;
}

export interface SavedQuiz {
  id: string;
  subjectId: string | null;
  /** The Malai chat it was taken in; lists it in that chat's menu. */
  chatId: string | null;
  title: string;
  createdAt: number;
  questions: QuizQuestion[];
  attempts: QuizAttempt[];
}

const KEY = "malai.quizzes.v1";
const listeners = new Set<() => void>();
let cache: SavedQuiz[] | null = null;
const EMPTY: SavedQuiz[] = [];
let pulled = false;

function read(): SavedQuiz[] {
  if (cache) return cache;
  try {
    cache = JSON.parse(localStorage.getItem(KEY) ?? "[]") as SavedQuiz[];
  } catch {
    cache = [];
  }
  return cache;
}

function write(next: SavedQuiz[]): void {
  cache = next;
  try {
    localStorage.setItem(KEY, JSON.stringify(next));
  } catch {
    // Full or blocked storage: keep this tab's copy.
  }
  listeners.forEach((l) => l());
}

async function remote() {
  const supabase = getSupabaseBrowserClient();
  if (!supabase) return null;
  try {
    const { data } = await supabase.auth.getSession();
    const userId = data.session?.user.id;
    return userId ? { supabase, userId } : null;
  } catch {
    return null;
  }
}

async function push(quiz: SavedQuiz): Promise<void> {
  const s = await remote();
  if (!s) return;
  const { error } = await s.supabase.from("saved_quizzes").upsert(
    {
      id: quiz.id,
      owner_id: s.userId,
      subject_id: quiz.subjectId,
      chat_id: quiz.chatId,
      title: quiz.title,
      questions: quiz.questions,
      attempts: quiz.attempts,
      created_ms: quiz.createdAt,
    },
    { onConflict: "owner_id,id" },
  );
  if (error) console.warn("quiz sync failed", error);
}

async function remove(quizId: string): Promise<void> {
  const s = await remote();
  if (!s) return;
  const { error } = await s.supabase.from("saved_quizzes").delete().eq("owner_id", s.userId).eq("id", quizId);
  if (error) console.warn("quiz delete failed", error);
}

/** The account's quizzes replace the local copy; local-only ones are uploaded. */
async function pull(): Promise<void> {
  const s = await remote();
  if (!s) return;
  const { data, error } = await s.supabase
    .from("saved_quizzes")
    .select("id, subject_id, chat_id, title, questions, attempts, created_ms")
    .order("created_ms", { ascending: false });
  if (error || !data) {
    if (error) console.warn("quiz pull failed", error);
    return;
  }
  const fromAccount: SavedQuiz[] = data.map((r) => ({
    id: r.id,
    subjectId: r.subject_id,
    chatId: r.chat_id,
    title: r.title,
    questions: r.questions,
    attempts: r.attempts,
    createdAt: Number(r.created_ms),
  }));
  const ids = new Set(fromAccount.map((q) => q.id));
  const localOnly = read().filter((q) => !ids.has(q.id));
  localOnly.forEach((q) => void push(q));
  write([...localOnly, ...fromAccount].sort((a, b) => b.createdAt - a.createdAt));
}

function subscribe(l: () => void): () => void {
  listeners.add(l);
  const onStorage = (e: StorageEvent) => {
    if (e.key === KEY) {
      cache = null;
      l();
    }
  };
  window.addEventListener("storage", onStorage);
  return () => {
    listeners.delete(l);
    window.removeEventListener("storage", onStorage);
  };
}

export function useSavedQuizzes(): SavedQuiz[] {
  useEffect(() => {
    if (pulled) return;
    pulled = true;
    void pull();
  }, []);
  return useSyncExternalStore(subscribe, read, () => EMPTY);
}

export function newId(prefix: string): string {
  return `${prefix}_${Date.now().toString(36)}${Math.random().toString(36).slice(2, 7)}`;
}

function update(quizId: string, fn: (q: SavedQuiz) => SavedQuiz): void {
  let changed: SavedQuiz | null = null;
  write(read().map((q) => (q.id === quizId ? (changed = fn(q)) : q)));
  if (changed) void push(changed);
}

export function saveQuiz(quiz: Omit<SavedQuiz, "id" | "createdAt" | "attempts">): string {
  const saved: SavedQuiz = { ...quiz, id: newId("quiz"), createdAt: Date.now(), attempts: [] };
  write([saved, ...read()]);
  void push(saved);
  return saved.id;
}

export function addAttempt(quizId: string, attempt: Omit<QuizAttempt, "id" | "at">): void {
  update(quizId, (q) => ({ ...q, attempts: [...q.attempts, { ...attempt, id: newId("att"), at: Date.now() }] }));
}

export function deleteAttempt(quizId: string, attemptId: string): void {
  update(quizId, (q) => ({ ...q, attempts: q.attempts.filter((a) => a.id !== attemptId) }));
}

export function deleteQuiz(quizId: string): void {
  write(read().filter((q) => q.id !== quizId));
  void remove(quizId);
}

export function quizPath(quizId: string): string {
  return `/malai/quiz/${encodeURIComponent(quizId)}`;
}
