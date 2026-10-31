"use client";

/**
 * Learning progress kept in this browser:
 *
 * - How far each video has been watched: the furthest point reached by
 *   playing on from the start, to the second. Skipping ahead does not count;
 *   rewinding never loses ground.
 * - Each chat's resource allowance: what that chat may read. It follows the
 *   subject's completed resources, with this chat's own ticks and unticks on
 *   top, so a chat can be changed without touching the subject.
 *
 * Both are small external stores so every view stays in sync, and both
 * survive a missing or broken localStorage (they just last for the page).
 * Signed in, both are also kept on the account (lib/activitySync.ts,
 * migration 0006): pulled once per page load and merged, pushed on change.
 */

import { useEffect, useState, useSyncExternalStore } from "react";
import { readCompletion, writeCompletion } from "@/lib/learn";
import {
  deleteAllowance,
  pullAllowances,
  pullWatch,
  pushAllowance,
  pushWatch,
} from "@/lib/activitySync";

// ---------------------------------------------------------------------------
// Tiny keyed JSON store.
// ---------------------------------------------------------------------------

function jsonStore<T>(key: string, pull?: (current: Record<string, T>) => Promise<Record<string, T> | null>) {
  let cache: Record<string, T> | null = null;
  let pulled = false;
  const listeners = new Set<() => void>();
  const EMPTY: Record<string, T> = {};

  const read = (): Record<string, T> => {
    if (cache) return cache;
    try {
      const raw = window.localStorage.getItem(key);
      const parsed: unknown = raw ? JSON.parse(raw) : {};
      cache = parsed && typeof parsed === "object" && !Array.isArray(parsed) ? (parsed as Record<string, T>) : {};
    } catch {
      cache = {};
    }
    return cache;
  };

  const write = (next: Record<string, T>): void => {
    cache = next;
    try {
      window.localStorage.setItem(key, JSON.stringify(next));
    } catch {
      // Storage unavailable -> in-memory for this page.
    }
    listeners.forEach((l) => l());
  };

  const subscribe = (l: () => void): (() => void) => {
    listeners.add(l);
    if (pull && !pulled) {
      // The account's copy, merged into this browser's, once per page load.
      pulled = true;
      void pull(read())
        .then((merged) => {
          if (merged) write(merged);
        })
        .catch(() => {});
    }
    const onStorage = (e: StorageEvent) => {
      if (e.key === key) {
        cache = null;
        l();
      }
    };
    window.addEventListener("storage", onStorage);
    return () => {
      listeners.delete(l);
      window.removeEventListener("storage", onStorage);
    };
  };

  const use = (): Record<string, T> => useSyncExternalStore(subscribe, read, () => EMPTY);

  return { read, write, use, subscribe };
}

// ---------------------------------------------------------------------------
// Video watch progress.
// ---------------------------------------------------------------------------

export interface WatchProgress {
  /** Furthest point watched continuously from the start, ms. */
  ms: number;
  /** Video length, ms (0 until the player knows it). */
  durationMs: number;
}

const watchStore = jsonStore<WatchProgress>("ailearn.watch.v1", async (local) => {
  const remote = await pullWatch();
  if (!remote) return null;
  const merged: Record<string, WatchProgress> = { ...local };
  for (const [id, r] of Object.entries(remote)) {
    const l = local[id];
    // Watching only moves forward: keep the furthest point either side has.
    if (!l || r.ms > l.ms) merged[id] = { ms: r.ms, durationMs: r.durationMs || l?.durationMs || 0 };
    else if (l.ms > r.ms) void pushWatch(id, l.ms, l.durationMs);
  }
  for (const [id, l] of Object.entries(local)) if (!remote[id] && l.ms > 0) void pushWatch(id, l.ms, l.durationMs);
  return merged;
});

/** Last push per video; watch progress goes to the account at most this often. */
const lastWatchPush = new Map<string, number>();
const WATCH_PUSH_MS = 15_000;

/** A jump bigger than this between two time reports is a seek, not playback. */
const MAX_STEP_MS = 5_000;

export function readWatch(resourceId: string): WatchProgress | null {
  return watchStore.read()[resourceId] ?? null;
}

/**
 * The player moved from `prevMs` to `ms`. Playback that carries on from (or
 * just behind) the watched frontier pushes it forward; a seek past it does not.
 */
export function recordWatch(resourceId: string, prevMs: number | null, ms: number, durationMs: number): void {
  const all = watchStore.read();
  const cur = all[resourceId] ?? { ms: 0, durationMs: 0 };
  const dur = durationMs > 0 ? Math.round(durationMs) : cur.durationMs;
  let next = cur.ms;
  if (prevMs !== null && ms > prevMs && ms - prevMs <= MAX_STEP_MS && prevMs <= cur.ms + MAX_STEP_MS) {
    next = Math.max(cur.ms, ms);
  }
  if (dur > 0) next = Math.min(next, dur);
  // The last few seconds (credits, a fade) count as the end.
  if (dur > 0 && dur - next <= 2_000 && next > 0) next = dur;
  // Whole seconds are enough: no write (and no re-render) on every timeupdate.
  if (dur === cur.durationMs && next - cur.ms < 1_000 && next !== dur) return;
  watchStore.write({ ...all, [resourceId]: { ms: next, durationMs: dur } });
  const now = Date.now();
  if (next === dur || now - (lastWatchPush.get(resourceId) ?? 0) >= WATCH_PUSH_MS) {
    lastWatchPush.set(resourceId, now);
    void pushWatch(resourceId, next, dur);
  }
}

/** Share watched, 0..1; null when the length is not known yet. */
export function watchFraction(w: WatchProgress | null | undefined): number | null {
  if (!w || w.durationMs <= 0) return null;
  return Math.min(1, Math.max(0, w.ms / w.durationMs));
}

export function useWatchProgress(): Record<string, WatchProgress> {
  return watchStore.use();
}

// ---------------------------------------------------------------------------
// Subject completion, reactive.
// ---------------------------------------------------------------------------

const COMPLETION_EVENT = "ailearn:completion";

/** Mark resources done / not done for the whole subject, and tell every view. */
export function setSubjectCompletion(subjectId: string, ids: string[], done: boolean): void {
  const cur = readCompletion(subjectId);
  const next = done ? [...new Set([...cur, ...ids])] : cur.filter((id) => !ids.includes(id));
  writeCompletion(subjectId, next);
  // Completing for the subject reaches every chat in it, even one that had
  // unticked that resource for itself.
  if (done) {
    const all = allowStore.read();
    const changed: string[] = [];
    const patched: Record<string, ChatAllowance> = {};
    for (const [chatId, a] of Object.entries(all)) {
      if (a.subjectId === subjectId && a.remove.some((id) => ids.includes(id))) {
        patched[chatId] = { ...a, remove: a.remove.filter((id) => !ids.includes(id)), updatedAt: Date.now() };
        changed.push(chatId);
      } else patched[chatId] = a;
    }
    if (changed.length) putAllowances(patched, changed);
  }
  notifyCompletion(subjectId);
}

export function notifyCompletion(subjectId: string): void {
  window.dispatchEvent(new CustomEvent(COMPLETION_EVENT, { detail: subjectId }));
}

/** The subject's completed resource ids, kept current. */
export function useSubjectCompletion(subjectId: string | null): string[] {
  const [ids, setIds] = useState<string[]>([]);
  useEffect(() => {
    if (!subjectId) {
      setIds([]);
      return;
    }
    const load = () => setIds(readCompletion(subjectId));
    load();
    const on = (e: Event) => {
      const d = (e as CustomEvent<string>).detail;
      if (!d || d === subjectId) load();
    };
    const onStorage = (e: StorageEvent) => {
      if (e.key?.startsWith("ailearn.course.")) load();
    };
    window.addEventListener(COMPLETION_EVENT, on);
    window.addEventListener("storage", onStorage);
    return () => {
      window.removeEventListener(COMPLETION_EVENT, on);
      window.removeEventListener("storage", onStorage);
    };
  }, [subjectId]);
  return ids;
}

// ---------------------------------------------------------------------------
// Per-chat resource allowance.
// ---------------------------------------------------------------------------

export interface ChatAllowance {
  subjectId: string;
  /** Ticked for this chat although not completed for the subject. */
  add: string[];
  /** Unticked for this chat although completed for the subject. */
  remove: string[];
  /** Last change, ms; the newer side wins when merging with the account. */
  updatedAt?: number;
}

const DRAFT_PREFIX = "draft:";

const allowStore = jsonStore<ChatAllowance>("ailearn.chat_allow.v1", async (local) => {
  const remote = await pullAllowances();
  if (!remote) return null;
  const merged: Record<string, ChatAllowance> = { ...local };
  for (const [id, r] of Object.entries(remote)) {
    const l = local[id];
    if (!l || r.updatedAt > (l.updatedAt ?? 0)) merged[id] = r;
    else if ((l.updatedAt ?? 0) > r.updatedAt) pushOne(id, l);
  }
  for (const [id, l] of Object.entries(local)) if (!remote[id]) pushOne(id, l);
  return merged;
});

/** Drafts (a chat not made yet) stay in this browser. */
function pushOne(chatKey: string, a: ChatAllowance | null): void {
  if (chatKey.startsWith(DRAFT_PREFIX)) return;
  if (a) void pushAllowance(chatKey, { subjectId: a.subjectId, add: a.add, remove: a.remove, updatedAt: a.updatedAt ?? Date.now() });
  else void deleteAllowance(chatKey);
}

/** Write the allowances, sending the changed (or removed) chats to the account. */
function putAllowances(next: Record<string, ChatAllowance>, changed: string[], removed: string[] = []): void {
  allowStore.write(next);
  for (const k of changed) pushOne(k, next[k] ?? null);
  for (const k of removed) pushOne(k, null);
}

/** Key for a chat not created yet (the new-chat box on a subject's page). */
export function draftAllowanceKey(subjectId: string): string {
  return `${DRAFT_PREFIX}${subjectId}`;
}

export function useChatAllowances(): Record<string, ChatAllowance> {
  return allowStore.use();
}

/** What the chat may read: the subject's completed resources, adjusted for this chat. */
export function effectiveAllowance(
  resourceIds: readonly string[],
  subjectDone: readonly string[],
  a: ChatAllowance | null | undefined,
): string[] {
  return resourceIds.filter((id) =>
    a?.remove.includes(id) ? false : subjectDone.includes(id) || Boolean(a?.add.includes(id)),
  );
}

export function readAllowance(
  chatKey: string,
  subjectId: string,
  resourceIds: readonly string[],
): string[] {
  return effectiveAllowance(resourceIds, readCompletion(subjectId), allowStore.read()[chatKey]);
}

/** Tick or untick one resource for this chat only. */
export function setChatAllowed(chatKey: string, subjectId: string, resourceId: string, allowed: boolean): void {
  const all = allowStore.read();
  const cur = all[chatKey] ?? { subjectId, add: [], remove: [] };
  const subjectDone = readCompletion(subjectId).includes(resourceId);
  const add = cur.add.filter((id) => id !== resourceId);
  const remove = cur.remove.filter((id) => id !== resourceId);
  if (allowed && !subjectDone) add.push(resourceId);
  if (!allowed && subjectDone) remove.push(resourceId);
  putAllowances({ ...all, [chatKey]: { subjectId, add, remove, updatedAt: Date.now() } }, [chatKey]);
}

/** A new chat was made from the draft: it keeps the draft's choices. */
export function adoptDraftAllowance(subjectId: string, chatId: string): void {
  const all = allowStore.read();
  const draft = all[draftAllowanceKey(subjectId)];
  if (!draft) return;
  const { [draftAllowanceKey(subjectId)]: _drop, ...rest } = all;
  putAllowances({ ...rest, [chatId]: { ...draft, updatedAt: Date.now() } }, [chatId]);
}

/** Back to following the subject. */
export function resetChatAllowance(chatKey: string): void {
  const all = allowStore.read();
  if (!all[chatKey]) return;
  const { [chatKey]: _drop, ...rest } = all;
  putAllowances(rest, [], [chatKey]);
}

// ---------------------------------------------------------------------------
// Materials named in a question.
// ---------------------------------------------------------------------------

/** Words that say nothing about which material is meant. */
const GENERIC_TITLE_WORDS = new Set([
  "pdf", "ppt", "pptx", "mp4", "mov", "mkv", "webm", "png", "jpg", "jpeg", "webp", "doc", "docx",
  "video", "lecture", "lec", "notes", "note", "slides", "slide", "chapter", "unit", "part", "lesson",
  "the", "and", "for", "with", "from", "into", "about", "final", "copy", "new", "file", "youtube",
]);

function words(text: string): string[] {
  return text
    .normalize("NFKD")
    .toLowerCase()
    .replace(/\.[a-z0-9]{2,4}$/, "")
    .split(/[^\p{L}\p{N}]+/u)
    .filter(Boolean);
}

/**
 * Ids of the materials a message names by title ("what did the Pravin Chandra
 * pdf say?"). A title matches when the message contains all of its telling
 * words, or at least two of them for a long title.
 */
export function mentionedResourceIds(
  message: string,
  resources: ReadonlyArray<{ id: string; title?: string | null }>,
): string[] {
  const said = new Set(words(message));
  const out: string[] = [];
  for (const r of resources) {
    const key = [...new Set(words(r.title ?? ""))].filter(
      (w) => !GENERIC_TITLE_WORDS.has(w) && (w.length >= 3 || /\d/.test(w)),
    );
    if (key.length === 0) continue;
    const hits = key.filter((w) => said.has(w)).length;
    if (hits === key.length || (key.length >= 3 && hits >= 2)) out.push(r.id);
  }
  return out;
}
