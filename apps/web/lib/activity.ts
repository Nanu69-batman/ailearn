/**
 * Learner activity, kept in this browser's localStorage.
 *
 * The dashboard's streak, heatmap, "continue learning" and per-subject
 * progress all come from here. Nothing is invented: a day only counts once
 * the learner actually spent time on a lesson, and progress is the share of a
 * subject's resources the learner marked as done. A fresh browser therefore
 * shows an empty heatmap and 0%, which is the truth for that browser.
 */

import type { Subject } from "@/lib/api";
import { readCompletion } from "@/lib/learn";

/** { "YYYY-MM-DD": seconds studied that local day } */
const DAYS_KEY = "ailearn.activity.days";
/** The last lesson opened, for "Continue learning". */
const LAST_KEY = "ailearn.activity.last";
/** { subjectId: epoch ms of the last visit } */
const ACCESSED_KEY = "ailearn.activity.accessed";
/** Subject ids the learner archived. */
const ARCHIVED_KEY = "ailearn.subjects.archived";

export interface LastVisit {
  subjectId: string;
  subjectName: string | null;
  resourceId: string;
  title: string;
  at: number;
}

function readJson<T>(key: string, fallback: T): T {
  try {
    const raw = window.localStorage.getItem(key);
    return raw ? (JSON.parse(raw) as T) : fallback;
  } catch {
    // Storage unavailable or corrupt value -> behave as if nothing is stored.
    return fallback;
  }
}

function writeJson(key: string, value: unknown): void {
  try {
    window.localStorage.setItem(key, JSON.stringify(value));
  } catch {
    // Storage unavailable (private mode / quota) -> drop the write.
  }
}

/** Local-time calendar day, so "today" matches the learner's clock. */
export function dayKey(date: Date): string {
  const m = String(date.getMonth() + 1).padStart(2, "0");
  const d = String(date.getDate()).padStart(2, "0");
  return `${date.getFullYear()}-${m}-${d}`;
}

export function readStudyDays(): Record<string, number> {
  const days = readJson<Record<string, unknown>>(DAYS_KEY, {});
  const clean: Record<string, number> = {};
  for (const [k, v] of Object.entries(days)) {
    if (typeof v === "number" && v > 0) clean[k] = v;
  }
  return clean;
}

export function addStudySeconds(seconds: number): void {
  const days = readStudyDays();
  const today = dayKey(new Date());
  days[today] = (days[today] ?? 0) + seconds;
  writeJson(DAYS_KEY, days);
}

/** Consecutive study days ending today (or yesterday, if today is still empty). */
export function currentStreak(days: Record<string, number>, today = new Date()): number {
  const cursor = new Date(today);
  if (!days[dayKey(cursor)]) cursor.setDate(cursor.getDate() - 1);
  let streak = 0;
  while (days[dayKey(cursor)]) {
    streak += 1;
    cursor.setDate(cursor.getDate() - 1);
  }
  return streak;
}

export function recordVisit(visit: Omit<LastVisit, "at">): void {
  const at = Date.now();
  writeJson(LAST_KEY, { ...visit, at });
  const accessed = readAccessed();
  accessed[visit.subjectId] = at;
  writeJson(ACCESSED_KEY, accessed);
}

export function readLastVisit(): LastVisit | null {
  const v = readJson<Partial<LastVisit> | null>(LAST_KEY, null);
  if (!v || typeof v.subjectId !== "string" || typeof v.resourceId !== "string") {
    return null;
  }
  return {
    subjectId: v.subjectId,
    subjectName: typeof v.subjectName === "string" ? v.subjectName : null,
    resourceId: v.resourceId,
    title: typeof v.title === "string" ? v.title : v.resourceId,
    at: typeof v.at === "number" ? v.at : 0,
  };
}

export function readAccessed(): Record<string, number> {
  return readJson<Record<string, number>>(ACCESSED_KEY, {});
}

export function readArchived(): string[] {
  const v = readJson<unknown>(ARCHIVED_KEY, []);
  return Array.isArray(v) ? v.filter((x): x is string => typeof x === "string") : [];
}

export function writeArchived(ids: string[]): void {
  writeJson(ARCHIVED_KEY, ids);
}

/** Whole-number percent of the subject's resources marked as done. */
export function subjectProgress(subject: Subject): number {
  const total = subject.resource_count ?? 0;
  if (total === 0) return 0;
  const done = readCompletion(subject.id);
  const ids = subject.resource_ids;
  const count = ids ? done.filter((id) => ids.includes(id)).length : done.length;
  return Math.min(100, Math.round((count / total) * 100));
}

/** "24h 30m", "45m", "0m". */
export function formatStudyTime(seconds: number): string {
  const totalMin = Math.floor(seconds / 60);
  const h = Math.floor(totalMin / 60);
  const m = totalMin % 60;
  if (h === 0) return `${m}m`;
  return m === 0 ? `${h}h` : `${h}h ${m}m`;
}
