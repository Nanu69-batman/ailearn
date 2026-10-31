/**
 * Learner activity, kept in this browser's localStorage.
 *
 * The dashboard's streak, heatmap, "continue learning" and per-subject
 * progress all come from here. Nothing is invented: a day only counts once
 * the learner actually spent time on a lesson, and progress is the share of a
 * subject's resources the learner marked as done. A fresh browser therefore
 * shows an empty heatmap and 0%, which is the truth for that browser.
 *
 * Signed in, every write is mirrored to the account (lib/activitySync.ts) and
 * `syncActivityFromAccount()` replaces these records with the account's, so
 * the numbers follow the learner across browsers.
 */

import type { Subject } from "@/lib/api";
import { pullActivity, pushStudySeconds, pushVisit } from "@/lib/activitySync";
import { readCompletion, writeCompletionLocal } from "@/lib/learn";
import { readWatch, watchFraction } from "@/lib/progress";

/** { "YYYY-MM-DD": seconds studied that local day } */
const DAYS_KEY = "ailearn.activity.days";
/** The last lesson opened, for "Continue learning". */
const LAST_KEY = "ailearn.activity.last";
/** { subjectId: epoch ms of the last visit } */
const ACCESSED_KEY = "ailearn.activity.accessed";
/** Subject ids the learner archived. */
const ARCHIVED_KEY = "ailearn.subjects.archived";
/** The account the records above were last synced from. */
const OWNER_KEY = "ailearn.activity.owner";

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
  void pushStudySeconds(today, seconds);
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
  void pushVisit(visit);
}

/**
 * Replace this browser's records with the signed-in account's. Returns false
 * (and leaves everything untouched) when signed out or the pull fails.
 * Replacing rather than merging means a second account on the same browser
 * never inherits the first one's streak.
 */
export async function syncActivityFromAccount(): Promise<boolean> {
  const remote = await pullActivity();
  if (!remote) return false;
  claimActivityOwner(remote.userId);
  writeJson(DAYS_KEY, remote.days);

  const accessed: Record<string, number> = {};
  const completion: Record<string, string[]> = {};
  let last: (typeof remote.progress)[number] | null = null;
  for (const p of remote.progress) {
    if (!p.subjectId) continue;
    accessed[p.subjectId] = Math.max(accessed[p.subjectId] ?? 0, p.lastOpenedAt);
    if (p.completed) (completion[p.subjectId] ??= []).push(p.resourceId);
    if (!last || p.lastOpenedAt > last.lastOpenedAt) last = p;
  }
  writeJson(ACCESSED_KEY, accessed);
  for (const subjectId of Object.keys({ ...accessed, ...completion })) {
    writeCompletionLocal(subjectId, completion[subjectId] ?? []);
  }
  const previous = readLastVisit();
  if (last?.subjectId) {
    writeJson(LAST_KEY, {
      subjectId: last.subjectId,
      // The account has no subject name; keep the local one if it is the same subject.
      subjectName: previous?.subjectId === last.subjectId ? previous.subjectName : null,
      resourceId: last.resourceId,
      title: last.title ?? last.resourceId,
      at: last.lastOpenedAt,
    });
  } else {
    try {
      window.localStorage.removeItem(LAST_KEY);
    } catch {
      // Storage unavailable -> nothing to clear.
    }
  }
  return true;
}

/** Forget the previous account's local records when a different one signs in. */
function claimActivityOwner(userId: string): void {
  const owner = readJson<string | null>(OWNER_KEY, null);
  if (owner === userId) return;
  try {
    const ls = window.localStorage;
    for (const key of Object.keys(ls)) {
      if (
        key === DAYS_KEY ||
        key === LAST_KEY ||
        key === ACCESSED_KEY ||
        key === ARCHIVED_KEY ||
        key === "ailearn.watch.v1" ||
        key === "ailearn.chat_allow.v1" ||
        (key.startsWith("ailearn.course.") && key.endsWith(".completion"))
      ) {
        ls.removeItem(key);
      }
    }
  } catch {
    // Storage unavailable -> nothing stored to leak.
  }
  writeJson(OWNER_KEY, userId);
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

/**
 * Whole-number percent of the subject done: resources marked done count in
 * full, an unfinished video by how far it has been watched.
 */
export function subjectProgress(subject: Subject): number {
  const total = subject.resource_count ?? 0;
  if (total === 0) return 0;
  const done = readCompletion(subject.id);
  const ids = subject.resource_ids;
  let count = ids ? done.filter((id) => ids.includes(id)).length : done.length;
  for (const id of ids ?? []) {
    if (!done.includes(id)) count += watchFraction(readWatch(id)) ?? 0;
  }
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
