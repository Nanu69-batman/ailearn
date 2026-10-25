/**
 * Malai: the standalone tutor chat at /malai.
 *
 * The server owns the messages (a session's threads, apps/api/app/chat). This
 * browser owns the index of chats: which session and thread each one lives in,
 * its title, and the subject it belongs to. A chat with no subject is a
 * "recent chat"; moving a chat to a subject only changes this index, and from
 * then on its questions are narrowed to that subject's materials.
 *
 * One Malai session covers every subject (course `*`, see ANY_COURSE in
 * apps/api/app/retrieval/store.py). When the set of materials changes, the
 * next new chat gets a fresh session; older chats keep the session they were
 * created in, so their history stays readable.
 */

import { useEffect, useState, useSyncExternalStore } from "react";
import {
  createLmsSession,
  createThread,
  formatApiError,
  getSubjectResources,
  sameResourceIdSet,
  type CourseResource,
  type Subject,
} from "@/lib/api";
import { readCompletion } from "@/lib/learn";

export const MALAI_PATH = "/malai";

export function malaiChatPath(chatId: string): string {
  return `${MALAI_PATH}?c=${encodeURIComponent(chatId)}`;
}

/** A subject's Malai page: its chats, its resources, and a new-chat box. */
export function malaiSubjectPath(subjectId: string): string {
  return `${MALAI_PATH}?s=${encodeURIComponent(subjectId)}`;
}

/** A resource opened inside Malai, optionally with one of its chats. */
export function malaiLessonPath(subjectId: string, resourceId: string, chatId?: string): string {
  const base = `${MALAI_PATH}/lesson/${encodeURIComponent(subjectId)}/${encodeURIComponent(resourceId)}`;
  return chatId ? `${base}?c=${encodeURIComponent(chatId)}` : base;
}

/** Where a chat lives: a lesson chat reopens beside its resource. */
export function chatPath(chat: MalaiChat): string {
  return chat.resourceId && chat.subjectId
    ? malaiLessonPath(chat.subjectId, chat.resourceId, chat.id)
    : malaiChatPath(chat.id);
}

/** Course id of a session that spans every subject. Mirrors the API's ANY_COURSE. */
const ANY_COURSE = "*";
const CHATS_KEY = "ailearn.malai.chats";
const SESSION_KEY = "ailearn.malai.session";

export interface MalaiChat {
  id: string;
  sessionId: string;
  sessionToken: string;
  threadId: string;
  title: string;
  /** Null: not filed under a subject ("Recent chats"). */
  subjectId: string | null;
  /**
   * Set for a chat started beside a resource (the lesson page). It lives in
   * that subject's session, so it is always filed under that subject.
   */
  resourceId?: string | null;
  /** The learner's latest question, shown under the title. */
  preview: string;
  createdAt: number;
  updatedAt: number;
}

interface MalaiSession {
  session_id: string;
  session_token: string;
  scope: string[];
}

// ---------------------------------------------------------------------------
// Chat index: a tiny external store so the sidebar and the page stay in sync.
// ---------------------------------------------------------------------------

const EMPTY: MalaiChat[] = [];
let cache: MalaiChat[] | null = null;
const listeners = new Set<() => void>();

function load(): MalaiChat[] {
  if (cache) return cache;
  try {
    const raw = window.localStorage.getItem(CHATS_KEY);
    const parsed: unknown = raw ? JSON.parse(raw) : [];
    cache = Array.isArray(parsed)
      ? parsed.filter(
          (c): c is MalaiChat =>
            typeof c === "object" && c !== null && typeof c.id === "string" && typeof c.threadId === "string",
        )
      : [];
  } catch {
    // Storage unavailable or corrupt value -> start with no chats.
    cache = [];
  }
  return cache;
}

function save(next: MalaiChat[]): void {
  cache = next;
  try {
    window.localStorage.setItem(CHATS_KEY, JSON.stringify(next));
  } catch {
    // Storage unavailable (private mode / quota) -> keep in-memory only.
  }
  listeners.forEach((l) => l());
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  // Another tab changed the index.
  const onStorage = (e: StorageEvent) => {
    if (e.key === CHATS_KEY) {
      cache = null;
      listener();
    }
  };
  window.addEventListener("storage", onStorage);
  return () => {
    listeners.delete(listener);
    window.removeEventListener("storage", onStorage);
  };
}

/** Every chat, most recently active first. */
export function useMalaiChats(): MalaiChat[] {
  return useSyncExternalStore(subscribe, load, () => EMPTY);
}

export function getChat(id: string): MalaiChat | null {
  return load().find((c) => c.id === id) ?? null;
}

export function addChat(chat: MalaiChat): void {
  save([chat, ...load().filter((c) => c.id !== chat.id)]);
}

export function updateChat(id: string, patch: Partial<Omit<MalaiChat, "id">>): void {
  save(
    load()
      .map((c) => (c.id === id ? { ...c, ...patch } : c))
      .sort((a, b) => b.updatedAt - a.updatedAt),
  );
}

export function removeChat(id: string): void {
  save(load().filter((c) => c.id !== id));
}

/**
 * A question was asked on a lesson page: file that chat under its subject,
 * creating its entry on the first question. Returns the chat's id.
 */
export function recordLessonChat(args: {
  sessionId: string;
  sessionToken: string;
  threadId: string;
  subjectId: string;
  resourceId: string;
  question: string;
}): string {
  const now = Date.now();
  const existing = load().find((c) => c.sessionId === args.sessionId && c.threadId === args.threadId);
  if (existing) {
    updateChat(existing.id, { preview: titleFrom(args.question), updatedAt: now, sessionToken: args.sessionToken });
    return existing.id;
  }
  const chat: MalaiChat = {
    id: newChatId(),
    sessionId: args.sessionId,
    sessionToken: args.sessionToken,
    threadId: args.threadId,
    title: titleFrom(args.question),
    subjectId: args.subjectId,
    resourceId: args.resourceId,
    preview: titleFrom(args.question),
    createdAt: now,
    updatedAt: now,
  };
  addChat(chat);
  return chat.id;
}

/** Short title from a first question. */
export function titleFrom(text: string): string {
  const flat = text.replace(/\s+/g, " ").trim();
  return flat.length > 48 ? `${flat.slice(0, 47)}…` : flat;
}

/** "Today", "Yesterday", or "3 Oct". */
export function chatDate(ms: number, now = new Date()): string {
  const d = new Date(ms);
  const sameDay = (a: Date, b: Date) =>
    a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
  if (sameDay(d, now)) return "Today";
  const y = new Date(now);
  y.setDate(y.getDate() - 1);
  if (sameDay(d, y)) return "Yesterday";
  return d.toLocaleDateString("en-GB", { day: "numeric", month: "short" });
}

// ---------------------------------------------------------------------------
// Scope: what a chat may read.
// ---------------------------------------------------------------------------

/** Every material across the subjects, deduplicated. The API caps a grant at 256. */
export function allResourceIds(subjects: Subject[]): string[] {
  const ids = new Set<string>();
  for (const s of subjects) for (const id of s.resource_ids ?? []) ids.add(id);
  return [...ids].slice(0, 256);
}

/** Materials marked done in these subjects: readable in full, videos included. */
export function doneResourceIds(subjects: Subject[]): string[] {
  const done = new Set<string>();
  for (const s of subjects) {
    const ids = s.resource_ids ?? [];
    for (const id of readCompletion(s.id)) if (ids.includes(id)) done.add(id);
  }
  return [...done].slice(0, 100);
}

// ---------------------------------------------------------------------------
// Session + thread for a new chat.
// ---------------------------------------------------------------------------

function readSession(): MalaiSession | null {
  try {
    const raw = window.localStorage.getItem(SESSION_KEY);
    const v = raw ? (JSON.parse(raw) as Partial<MalaiSession>) : null;
    if (v && typeof v.session_id === "string" && typeof v.session_token === "string" && Array.isArray(v.scope)) {
      return v as MalaiSession;
    }
  } catch {
    // Fall through to a fresh session.
  }
  return null;
}

function writeSession(s: MalaiSession | null): void {
  try {
    if (s) window.localStorage.setItem(SESSION_KEY, JSON.stringify(s));
    else window.localStorage.removeItem(SESSION_KEY);
  } catch {
    // Storage unavailable -> a new session per page load.
  }
}

/**
 * The Malai session for `scope`, reused while the materials are unchanged.
 * `fresh` forces a new one (the stored session was lost server-side).
 */
async function malaiSession(scope: string[], fresh = false): Promise<MalaiSession> {
  const stored = fresh ? null : readSession();
  if (stored && sameResourceIdSet(stored.scope, scope)) return stored;
  const res = await createLmsSession({
    learner_id: "demo-learner",
    course_id: ANY_COURSE,
    resource_id: scope[0],
    resource_scope: scope,
    current_time_ms: 0,
    issued_at: Date.now(),
  });
  const next = { session_id: res.session_id, session_token: res.session_token, scope };
  writeSession(next);
  return next;
}

/** A new server thread for a chat, in the current Malai session. */
export async function openThread(
  scope: string[],
  fresh = false,
): Promise<{ sessionId: string; sessionToken: string; threadId: string }> {
  if (scope.length === 0) {
    throw new Error("Add materials to a subject first, so Malai has something to read.");
  }
  let session = await malaiSession(scope, fresh);
  let thread;
  try {
    thread = await createThread(session.session_id, session.session_token);
  } catch (err) {
    if (fresh) throw err;
    // The stored session is gone (server restart): mint once more.
    writeSession(null);
    session = await malaiSession(scope, true);
    thread = await createThread(session.session_id, session.session_token);
  }
  return { sessionId: session.session_id, sessionToken: session.session_token, threadId: thread.id };
}

export function newChatId(): string {
  try {
    return crypto.randomUUID();
  } catch {
    return `c_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`;
  }
}

// ---------------------------------------------------------------------------
// A subject's resources, for the sidebar flyout and the subject page.
// ---------------------------------------------------------------------------

const resourceCache = new Map<string, CourseResource[]>();

export function useSubjectResources(subjectId: string | null): {
  resources: CourseResource[];
  state: "idle" | "loading" | "ready" | "error";
  error: string | null;
} {
  const cached = subjectId ? resourceCache.get(subjectId) : undefined;
  const [state, setState] = useState<"idle" | "loading" | "ready" | "error">(cached ? "ready" : "idle");
  const [resources, setResources] = useState<CourseResource[]>(cached ?? []);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!subjectId) return;
    let cancelled = false;
    const hit = resourceCache.get(subjectId);
    setResources(hit ?? []);
    setState(hit ? "ready" : "loading");
    setError(null);
    // Refresh even on a cache hit: materials may have been added since.
    getSubjectResources(subjectId)
      .then((res) => {
        if (cancelled) return;
        resourceCache.set(subjectId, res.resources);
        setResources(res.resources);
        setState("ready");
      })
      .catch((err) => {
        if (cancelled || hit) return;
        setError(formatApiError(err));
        setState("error");
      });
    return () => {
      cancelled = true;
    };
  }, [subjectId]);

  return { resources, state, error };
}
