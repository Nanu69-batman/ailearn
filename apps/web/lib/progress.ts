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
 */

import { useEffect, useState, useSyncExternalStore } from "react";
import { readCompletion, writeCompletion } from "@/lib/learn";

// ---------------------------------------------------------------------------
// Tiny keyed JSON store.
// ---------------------------------------------------------------------------

function jsonStore<T>(key: string) {
  let cache: Record<string, T> | null = null;
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

  return { read, write, use };
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

const watchStore = jsonStore<WatchProgress>("ailearn.watch.v1");

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
    let changed = false;
    const patched: Record<string, ChatAllowance> = {};
    for (const [chatId, a] of Object.entries(all)) {
      if (a.subjectId === subjectId && a.remove.some((id) => ids.includes(id))) {
        patched[chatId] = { ...a, remove: a.remove.filter((id) => !ids.includes(id)) };
        changed = true;
      } else patched[chatId] = a;
    }
    if (changed) allowStore.write(patched);
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
}

const allowStore = jsonStore<ChatAllowance>("ailearn.chat_allow.v1");

/** Key for a chat not created yet (the new-chat box on a subject's page). */
export function draftAllowanceKey(subjectId: string): string {
  return `draft:${subjectId}`;
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
  allowStore.write({ ...all, [chatKey]: { subjectId, add, remove } });
}

/** A new chat was made from the draft: it keeps the draft's choices. */
export function adoptDraftAllowance(subjectId: string, chatId: string): void {
  const all = allowStore.read();
  const draft = all[draftAllowanceKey(subjectId)];
  if (!draft) return;
  const { [draftAllowanceKey(subjectId)]: _drop, ...rest } = all;
  allowStore.write({ ...rest, [chatId]: draft });
}

/** Back to following the subject. */
export function resetChatAllowance(chatKey: string): void {
  const all = allowStore.read();
  if (!all[chatKey]) return;
  const { [chatKey]: _drop, ...rest } = all;
  allowStore.write(rest);
}
