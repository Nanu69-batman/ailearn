"use client";

/**
 * Saved quizzes and their attempts, kept in this browser. Each quiz is filed
 * under its subject (Resources > Quizzes in Malai's sidebar) and has its own
 * page with analytics, a re-attempt, and every past attempt.
 */
import { useSyncExternalStore } from "react";
import type { QuizGrade, QuizQuestion } from "@/lib/api";

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
  title: string;
  createdAt: number;
  sessionId: string;
  sessionToken: string;
  questions: QuizQuestion[];
  attempts: QuizAttempt[];
}

const KEY = "malai.quizzes.v1";
const listeners = new Set<() => void>();
let cache: SavedQuiz[] | null = null;
const EMPTY: SavedQuiz[] = [];

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
  return useSyncExternalStore(subscribe, read, () => EMPTY);
}

export function newId(prefix: string): string {
  return `${prefix}_${Date.now().toString(36)}${Math.random().toString(36).slice(2, 7)}`;
}

export function saveQuiz(quiz: Omit<SavedQuiz, "id" | "createdAt" | "attempts">): string {
  const id = newId("quiz");
  write([{ ...quiz, id, createdAt: Date.now(), attempts: [] }, ...read()]);
  return id;
}

export function addAttempt(quizId: string, attempt: Omit<QuizAttempt, "id" | "at">): void {
  write(
    read().map((q) =>
      q.id === quizId ? { ...q, attempts: [...q.attempts, { ...attempt, id: newId("att"), at: Date.now() }] } : q,
    ),
  );
}

export function deleteAttempt(quizId: string, attemptId: string): void {
  write(read().map((q) => (q.id === quizId ? { ...q, attempts: q.attempts.filter((a) => a.id !== attemptId) } : q)));
}

export function deleteQuiz(quizId: string): void {
  write(read().filter((q) => q.id !== quizId));
}

export function quizPath(quizId: string): string {
  return `/malai/quiz/${encodeURIComponent(quizId)}`;
}
