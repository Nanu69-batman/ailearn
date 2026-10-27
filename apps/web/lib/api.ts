/**
 * Typed API client for the AI Learning Assistant backend.
 * Contract (docs/api.md):
 *   POST /lms/session -> { session_id, session_token, learner_id, course_id }
 *   POST /chat        -> { answer, citations[], refused } (needs session_token:
 *                        body field + X-Session-Token header; 401 = expired)
 *   GET  /sessions/{id}/messages (needs X-Session-Token header; 401 = expired)
 *
 * All requests carry an `x-request-id` header for log correlation.
 * Backend may still be in progress — callers fall back to local
 * fixtures (lib/fixtures.ts) when the API is unreachable.
 */

import { personalizationForRequest } from "@/lib/personalization";
import { getAccessToken } from "@/lib/supabase/client";

export type ChatMode =
  | "qa"
  | "explain_simple"
  | "analogy"
  | "summarize"
  | "compare"
  | "quiz";

/**
 * Backend source of truth: apps/api/app/chat/schemas.py `ChatMode`.
 * "qa" is a plain question (no slash command, see SlashCommands) — only the
 * wire value was renamed (was "ask", which the backend rejects with 422).
 */
export const CHAT_MODES: readonly ChatMode[] = [
  "qa",
  "explain_simple",
  "analogy",
  "summarize",
  "compare",
  "quiz",
];

export interface LmsSessionRequest {
  learner_id: string;
  course_id: string;
  resource_id: string;
  lesson_id?: string;
  /** resource IDs, not types (e.g. ["res_video", "res_pdf", "res_ppt"]). */
  resource_scope: string[];
  current_time_ms: number;
  /**
   * HMAC signature over the canonical launch payload (see
   * apps/api/app/chat/launch.py `sign_launch`). OPTIONAL in dev: omit it
   * entirely when the backend runs with the dev secret — a present-but-wrong
   * value (e.g. "dev") is rejected with 401. Production LMS launches MUST
   * attach a real signature via `sign_launch`.
   */
  signature?: string;
  /** Epoch ms (or epoch seconds) when the launch was issued. */
  issued_at: number;
}

export interface LmsSessionResponse {
  session_id: string;
  session_token: string;
  learner_id?: string;
  course_id?: string;
}

/**
 * Session binding persisted alongside session_id after POST /lms/session.
 * Backend (apps/api/app/chat/router.py) issues `session_token` and requires
 * it on POST /chat (body `session_token` + `X-Session-Token` header) and
 * GET /sessions/{id}/messages (`X-Session-Token` header preferred).
 * Mock/offline mode synthesizes the "mock-token" value (see lib/fixtures.ts).
 */
export interface StoredSession {
  session_id: string;
  session_token: string;
  /**
   * Resource ids the grant was minted against, recorded at mint time. Purely a
   * client-side bookkeeping field: it is never sent to the backend and never
   * widens the grant. It exists so a rehydrated session can be compared against
   * the materials currently on screen — a grant minted against a resource that
   * has since been deleted, or against a different subject, must be re-minted
   * rather than left to refuse forever.
   */
  minted_resource_ids?: string[];
}

const SESSION_TOKEN_KEY = "ailearn.session_token";
/** Sits next to the token/id keys; same error-tolerant persistence style. */
const SESSION_RESOURCE_IDS_KEY = "ailearn.session_resource_ids";

let inMemorySessionToken: string | null = null;
let inMemoryMintedResourceIds: string[] | null = null;

export function setStoredSessionToken(token: string): void {
  inMemorySessionToken = token;
  try {
    if (typeof window !== "undefined" && window.sessionStorage) {
      window.sessionStorage.setItem(SESSION_TOKEN_KEY, token);
    }
  } catch {
    // Storage unavailable (SSR/private mode) — in-memory copy still works.
  }
}

export function getStoredSessionToken(): string | null {
  if (inMemorySessionToken) return inMemorySessionToken;
  try {
    if (typeof window !== "undefined" && window.sessionStorage) {
      const v = window.sessionStorage.getItem(SESSION_TOKEN_KEY);
      if (v) {
        inMemorySessionToken = v;
        return v;
      }
    }
  } catch {
    // Ignore storage errors.
  }
  return null;
}

export function clearStoredSessionToken(): void {
  inMemorySessionToken = null;
  try {
    if (typeof window !== "undefined" && window.sessionStorage) {
      window.sessionStorage.removeItem(SESSION_TOKEN_KEY);
    }
  } catch {
    // Ignore storage errors.
  }
}

/**
 * Record the resource-id set a grant was minted against. `null`/omitted means
 * "unknown" (e.g. the mint fell back to the fixture scope), which callers must
 * treat as needing a re-mint rather than as an empty set.
 */
export function setStoredMintedResourceIds(ids: string[] | null): void {
  inMemoryMintedResourceIds = ids;
  try {
    if (typeof window !== "undefined" && window.sessionStorage) {
      if (ids && ids.length > 0) {
        window.sessionStorage.setItem(SESSION_RESOURCE_IDS_KEY, JSON.stringify(ids));
      } else {
        window.sessionStorage.removeItem(SESSION_RESOURCE_IDS_KEY);
      }
    }
  } catch {
    // Storage unavailable — the in-memory copy still serves this tab.
  }
}

/** `null` when never recorded (pre-fix session) or when storage is unavailable. */
export function getStoredMintedResourceIds(): string[] | null {
  if (inMemoryMintedResourceIds) return inMemoryMintedResourceIds;
  try {
    if (typeof window !== "undefined" && window.sessionStorage) {
      const raw = window.sessionStorage.getItem(SESSION_RESOURCE_IDS_KEY);
      if (raw) {
        const parsed: unknown = JSON.parse(raw);
        if (Array.isArray(parsed) && parsed.every((v) => typeof v === "string")) {
          inMemoryMintedResourceIds = parsed as string[];
          return inMemoryMintedResourceIds;
        }
      }
    }
  } catch {
    // Ignore storage/parse errors — treated as "unknown".
  }
  return null;
}

export function clearStoredMintedResourceIds(): void {
  inMemoryMintedResourceIds = null;
  try {
    if (typeof window !== "undefined" && window.sessionStorage) {
      window.sessionStorage.removeItem(SESSION_RESOURCE_IDS_KEY);
    }
  } catch {
    // Ignore storage errors.
  }
}

/** Order-insensitive set equality — the backend does not promise an order. */
export function sameResourceIdSet(
  a: string[] | null | undefined,
  b: string[] | null | undefined,
): boolean {
  if (!a || !b) return false;
  if (a.length !== b.length) return false;
  const sa = [...a].sort();
  const sb = [...b].sort();
  return sa.every((v, i) => v === sb[i]);
}

/** True when the backend rejected the session binding (missing/wrong token). */
export function isSessionExpiredError(err: unknown): boolean {
  return err instanceof ApiError && err.status === 401;
}

export interface ChatRequest {
  session_id: string;
  /** HMAC session binding issued at /lms/session; verified per request (401). */
  session_token?: string;
  video_id?: string;
  current_time_ms: number;
  message: string;
  mode?: ChatMode;
  /** Which of the session's chats; omitted = the original one ("main"). */
  thread_id?: string;
  /**
   * Materials marked done in the course checklist. Besides the open one
   * (`video_id`), the chat may read these in full; the server intersects them
   * with the session grant, so this can never reach an ungranted material.
   */
  completed_resource_ids?: string[];
  /**
   * Narrows the session grant to these materials (a subject's), for a chat
   * not tied to one open material. Intersected server-side: never widens it.
   */
  scope_resource_ids?: string[];
  /**
   * A chat in no subject (Malai's general chat): answered from general
   * knowledge unless the message explicitly asks about the learner's materials.
   */
  general?: boolean;
}

export type CitationSourceType =
  | "video"
  | "video_transcript"
  | "pdf"
  | "pptx"
  | "image"
  | "note"
  | "code";

/**
 * How the evidence behind a citation was produced.
 *
 * A transcript chunk (Whisper/captions) and a sampled-frame chunk (vision)
 * both carry `source_type: "video"`, so `modality` is the only signal telling
 * a learner which one they are looking at. `null`/absent means the backend
 * did not record it -- NOT "transcript".
 */
export type CitationModality = "transcript" | "visual";

export interface Citation {
  chunk_id: string;
  source_type?: CitationSourceType | null;
  /** Absent/null on older or partial data; the chip then renders as before. */
  modality?: CitationModality | null;
  video_id?: string | null;
  start_ms?: number | null;
  end_ms?: number | null;
  page_no?: number | null;
  slide_no?: number | null;
  /**
   * 1-based page/image number for image citations.
   *
   * An image citation carries NO timestamp: the backend sets `start_ms`,
   * `end_ms`, `page_no`, `slide_no` and `video_id` all to null and marks
   * `modality: "visual"`. Renderers must branch on `image_no` BEFORE any
   * time formatting, or the badge degrades to `0:00–0:00` / `null`.
   */
  image_no?: number | null;
  /**
   * The cited passage's own text: highlighted on its PDF page, or shown under
   * the video. Absent on answers saved before the field existed.
   */
  text?: string | null;
  /**
   * True ONLY for a live capture: the exact frame the player's video element
   * was showing at `current_time_ms`, captured on demand and described by the
   * backend (`POST /chat/live-frame`).
   *
   * A live citation is an INSTANT, not a span: `start_ms === end_ms === T`.
   * That is what lets it satisfy the backend's strict-T gate (`end_ms <= T`)
   * with equality. Every pre-indexed citation — including a sampled visual
   * frame — has `live` absent/false, so an older payload reads as "not live".
   */
  live?: boolean;
}

export interface ChatResponse {
  answer: string;
  citations: Citation[];
  refused: boolean;
  /**
   * Where the answer came from: "grounded" (retrieved lesson passages),
   * "supplemental" (general knowledge, not from the video) or
   * "conversational" (small talk) or "general" (a chat in no subject,
   * answered from general knowledge). Optional: older backends and mocks omit it.
   */
  mode?: AnswerMode;
  /** True when the answer is backed by retrieved lesson passages. */
  grounded?: boolean;
  /** Streamed answers only: the name the server gave the chat after its first question. */
  thread_title?: string;
  /**
   * What the live-frame path did, or absent when no frame was involved.
   *
   * "live" — the learner's captured frame was described and injected as
   * high-priority evidence alongside the retrieved passages.
   * "document" — a frame was asked for but this material has none (pdf/pptx/
   * image): nothing was captured and nothing invented; the answer says so and
   * falls back to the document's own page/slide text.
   */
  live_frame?: LiveFrameState | null;
  /** Read defensively; older backends omit it. See scope_empty in ChatPanel. */
  scope_empty?: boolean;
  /** True when the question is past the processed part of a stream-mode video. */
  processing_pending?: boolean;
  processed_through_ms?: number | null;
  /** True when on-screen (frame) descriptions lag behind the transcript. */
  visual_pending?: boolean;
  visual_through_ms?: number | null;
}

export type LiveFrameState = "live" | "document";

/**
 * One captured frame, frozen at the playhead position it was painted from.
 *
 * `atMs` and `blob` are produced together, in one synchronous tick, by the
 * player's capture routine. They must travel together: a frame blob paired with
 * a timestamp read at a different moment is a stale frame described as current.
 */
export interface LiveFrameCapture {
  blob: Blob;
  /** The `video.currentTime` this blob was painted from, in ms. */
  atMs: number;
}

export type MessageRole = "user" | "assistant";

export type AnswerMode = "grounded" | "supplemental" | "conversational" | "general";

/** Client-side provenance of an assistant message; absent for loaded history. */
export type MessageKind = AnswerMode | "refused";

export interface ChatMessage {
  id: string;
  role: MessageRole;
  content: string;
  citations: Citation[];
  created_at: string;
  kind?: MessageKind;
}

export class ApiError extends Error {
  readonly status: number;
  readonly requestId: string;

  constructor(status: number, message: string, requestId: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.requestId = requestId;
  }
}

function baseUrl(): string {
  return (process.env.NEXT_PUBLIC_API_URL ?? "").replace(/\/$/, "");
}

export function isApiConfigured(): boolean {
  return baseUrl().length > 0;
}

function newRequestId(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }
  return `req-${Date.now()}-${Math.floor(Math.random() * 1e6)}`;
}

/** Raise the typed error the API layer already uses, decoding `detail`. */
async function apiErrorFromResponse(
  res: Response,
  requestId: string,
): Promise<ApiError> {
  let detail = res.statusText;
  try {
    const body: unknown = await res.json();
    if (
      typeof body === "object" &&
      body !== null &&
      "detail" in body &&
      typeof (body as { detail: unknown }).detail === "string"
    ) {
      detail = (body as { detail: string }).detail;
    }
  } catch {
    // Keep statusText when body is not JSON.
  }
  return new ApiError(res.status, detail, requestId);
}

/**
 * `Authorization: Bearer <access_token>` when a Supabase session exists
 * (docs/SUPABASE_PLAN.md, Auth contract). Empty when signed out or when
 * Supabase is not configured, so requests are unchanged in that case.
 */
async function authHeaders(): Promise<Record<string, string>> {
  const token = await getAccessToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const base = baseUrl();
  if (!base) {
    throw new ApiError(0, "NEXT_PUBLIC_API_URL is not configured", "n/a");
  }
  const requestId = newRequestId();
  let res: Response;
  try {
    res = await fetch(`${base}${path}`, {
      ...init,
      headers: {
        "Content-Type": "application/json",
        "x-request-id": requestId,
        ...(await authHeaders()),
        ...(init?.headers ?? {}),
      },
    });
  } catch (err) {
    throw new ApiError(
      0,
      err instanceof Error ? `Network error: ${err.message}` : "Network error",
      requestId,
    );
  }
  if (!res.ok) {
    throw await apiErrorFromResponse(res, requestId);
  }
  return (await res.json()) as T;
}

/**
 * Ask this app's own server (app/api/launch/route.ts, the demo LMS stand-in)
 * to sign the launch. 404 means no secret is configured (local dev): send the
 * launch unsigned, which the API accepts only with its dev secret.
 */
async function signLaunch(req: LmsSessionRequest): Promise<LmsSessionRequest> {
  let res: Response;
  try {
    res = await fetch("/api/launch", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(req),
    });
  } catch {
    return req;
  }
  if (res.status === 404) return req;
  if (!res.ok) {
    throw new ApiError(res.status, `Launch signing failed (${res.status})`, "");
  }
  return (await res.json()) as LmsSessionRequest;
}

export async function createLmsSession(
  req: LmsSessionRequest,
): Promise<LmsSessionResponse> {
  const launch = await signLaunch(req);
  return apiFetch<LmsSessionResponse>("/lms/session", {
    method: "POST",
    body: JSON.stringify(launch),
  });
}

/** The chat request body: the session token and the learner's personalization added. */
function chatBody(req: ChatRequest, token: string | undefined): string {
  const personalization = personalizationForRequest();
  return JSON.stringify({
    ...req,
    ...(token ? { session_token: token } : {}),
    ...(personalization ? { personalization } : {}),
  });
}

export function postChat(req: ChatRequest): Promise<ChatResponse> {
  // Header X-Session-Token preferred; body field kept for back-compat since
  // apps/api/app/chat/router.py verifies body.session_token on POST /chat.
  const token = req.session_token ?? getStoredSessionToken() ?? undefined;
  return apiFetch<ChatResponse>("/chat", {
    method: "POST",
    body: chatBody(req, token),
    headers: token ? { "X-Session-Token": token } : undefined,
  });
}

export interface ChatStreamHandlers {
  /** What the tutor is doing right now ("Searching the lesson"). */
  onStatus?: (text: string) => void;
  /** The answer so far, markers already stripped. "" means start over. */
  onText?: (text: string) => void;
}

/**
 * POST /chat/stream (Server-Sent Events). Resolves with the same validated
 * ChatResponse as postChat; the streamed text is only a preview of it.
 * Falls back to postChat when the backend has no streaming route.
 */
export async function streamChat(
  req: ChatRequest,
  handlers: ChatStreamHandlers,
): Promise<ChatResponse> {
  const base = baseUrl();
  if (!base) {
    throw new ApiError(0, "NEXT_PUBLIC_API_URL is not configured", "n/a");
  }
  const token = req.session_token ?? getStoredSessionToken() ?? undefined;
  const requestId = newRequestId();
  let res: Response;
  try {
    res = await fetch(`${base}/chat/stream`, {
      method: "POST",
      body: chatBody(req, token),
      headers: {
        "Content-Type": "application/json",
        Accept: "text/event-stream",
        "x-request-id": requestId,
        ...(await authHeaders()),
        ...(token ? { "X-Session-Token": token } : {}),
      },
    });
  } catch (err) {
    throw new ApiError(
      0,
      err instanceof Error ? `Network error: ${err.message}` : "Network error",
      requestId,
    );
  }
  if (res.status === 404 || res.status === 405) {
    // An older backend without the route (a gone session is also 404, and
    // postChat reports that one properly).
    return postChat(req);
  }
  if (!res.ok) {
    throw await apiErrorFromResponse(res, requestId);
  }
  if (!res.body) return (await res.json()) as ChatResponse;

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let cut: number;
    while ((cut = buffer.indexOf("\n\n")) !== -1) {
      const block = buffer.slice(0, cut);
      buffer = buffer.slice(cut + 2);
      let event = "message";
      let data = "";
      for (const line of block.split("\n")) {
        if (line.startsWith("event: ")) event = line.slice(7);
        else if (line.startsWith("data: ")) data += line.slice(6);
      }
      if (!data) continue;
      const payload = JSON.parse(data);
      if (event === "status") handlers.onStatus?.(payload.text);
      else if (event === "text") handlers.onText?.(payload.text);
      else if (event === "done") return payload as ChatResponse;
      else if (event === "error") {
        throw new ApiError(payload.status ?? 500, String(payload.detail ?? "error"), requestId);
      }
    }
  }
  throw new ApiError(0, "The answer stream ended early", requestId);
}

/**
 * Backend returns an envelope, not a bare array:
 * apps/api/app/chat/router.py -> MessagesResponse{session_id, messages[]}.
 * Note: backend MessageOut has no `id`, so getMessages synthesizes one.
 */
export interface MessagesEnvelope {
  session_id: string;
  messages: Array<Omit<ChatMessage, "id">>;
}

export async function getMessages(
  sessionId: string,
  sessionToken?: string,
  threadId?: string,
): Promise<ChatMessage[]> {
  // Header X-Session-Token preferred (router also accepts ?session_token=).
  const token = sessionToken ?? getStoredSessionToken() ?? undefined;
  const query = threadId ? `?thread_id=${encodeURIComponent(threadId)}` : "";
  const envelope = await apiFetch<MessagesEnvelope>(
    `/sessions/${encodeURIComponent(sessionId)}/messages${query}`,
    token ? { headers: { "X-Session-Token": token } } : undefined,
  );
  return envelope.messages.map((m, i) => ({
    ...m,
    id: `${sessionId}#${threadId ?? "main"}#${i}`,
  }));
}

// ---------------------------------------------------------------------------
// Chats (threads) within a session — apps/api/app/chat/router.py
//   GET    /sessions/{id}/threads              -> { threads: ChatThread[] }
//   POST   /sessions/{id}/threads              -> ChatThread
//   PATCH  /sessions/{id}/threads/{thread_id}  { title } -> ChatThread
//   DELETE /sessions/{id}/threads/{thread_id}  (the "main" chat is emptied)
// ---------------------------------------------------------------------------

export const MAIN_THREAD_ID = "main";

export interface ChatThread {
  id: string;
  /** Learner's name for it, else the first question asked, else null. */
  title: string | null;
  renamed: boolean;
  created_at: string;
  updated_at: string;
  message_count: number;
}

function threadsPath(sessionId: string, threadId?: string): string {
  const base = `/sessions/${encodeURIComponent(sessionId)}/threads`;
  return threadId ? `${base}/${encodeURIComponent(threadId)}` : base;
}

function tokenHeaders(sessionToken?: string): Record<string, string> | undefined {
  const token = sessionToken ?? getStoredSessionToken() ?? undefined;
  return token ? { "X-Session-Token": token } : undefined;
}

export async function listThreads(
  sessionId: string,
  sessionToken?: string,
): Promise<ChatThread[]> {
  const res = await apiFetch<{ threads: ChatThread[] }>(threadsPath(sessionId), {
    headers: tokenHeaders(sessionToken),
  });
  return res.threads;
}

export function createThread(sessionId: string, sessionToken?: string): Promise<ChatThread> {
  return apiFetch<ChatThread>(threadsPath(sessionId), {
    method: "POST",
    headers: tokenHeaders(sessionToken),
  });
}

export function renameThread(
  sessionId: string,
  threadId: string,
  title: string,
  sessionToken?: string,
): Promise<ChatThread> {
  return apiFetch<ChatThread>(threadsPath(sessionId, threadId), {
    method: "PATCH",
    body: JSON.stringify({ title }),
    headers: tokenHeaders(sessionToken),
  });
}

export async function deleteThread(
  sessionId: string,
  threadId: string,
  sessionToken?: string,
): Promise<void> {
  await apiFetch<unknown>(threadsPath(sessionId, threadId), {
    method: "DELETE",
    headers: tokenHeaders(sessionToken),
  });
}

// ---------------------------------------------------------------------------
// The Malai chat list, per signed-in user (GET/PUT/DELETE /malai/chats).
// ---------------------------------------------------------------------------

export interface MalaiChatRow {
  id: string;
  session_id: string;
  thread_id: string;
  title: string;
  subject_id: string | null;
  resource_id: string | null;
  preview: string;
  created_ms: number;
  updated_ms: number;
  /** Minted for the caller's live session; empty when the session is gone. */
  session_token?: string;
}

/** True when a Supabase login exists, so the chat list can live on the server. */
export async function isSignedIn(): Promise<boolean> {
  return Boolean(await getAccessToken());
}

export async function listMalaiChats(): Promise<MalaiChatRow[]> {
  const res = await apiFetch<{ chats: MalaiChatRow[] }>("/malai/chats");
  return Array.isArray(res.chats) ? res.chats : [];
}

export async function putMalaiChat(row: Omit<MalaiChatRow, "session_token">): Promise<void> {
  const { id, ...body } = row;
  await apiFetch<unknown>(`/malai/chats/${encodeURIComponent(id)}`, { method: "PUT", body: JSON.stringify(body) });
}

export async function deleteMalaiChat(id: string): Promise<void> {
  const base = baseUrl();
  if (!base) return;
  const requestId = newRequestId();
  // 204, no body: not through apiFetch, which parses JSON.
  const res = await fetch(`${base}/malai/chats/${encodeURIComponent(id)}`, {
    method: "DELETE",
    headers: { "x-request-id": requestId, ...(await authHeaders()) },
  });
  if (!res.ok) throw await apiErrorFromResponse(res, requestId);
}

// ---------------------------------------------------------------------------
// Live frame capture.
//
//   POST /chat/live-frame (multipart/form-data)
//     frame            image/jpeg  — the captured frame (Blob)
//     session_id       string
//     session_token    string?
//     current_time_ms  number      — read from video.currentTime in the SAME
//                                    synchronous tick as the canvas draw
//     message          string
//     mode             ChatMode?
//     video_id         string?
//     force_frame      boolean     — the explicit player button, which turns a
//                                    capture on regardless of the wording
//     -> ChatResponse (live_frame: "live" | "document")
//
// `atMs` is taken by the CALLER at capture time and sent verbatim; the backend
// validates it against its own container-header duration and rejects anything
// outside [0, duration] rather than clamping. Deliberately multipart rather
// than JSON, and deliberately NOT routed through `apiFetch`: that helper pins
// `Content-Type: application/json`, which strips the multipart boundary (the
// exact defect `ingestFile` documents for file upload).
// ---------------------------------------------------------------------------

export interface LiveFrameRequest {
  session_id: string;
  session_token?: string;
  video_id?: string;
  /** The playhead position the frame was captured at, in ms. */
  current_time_ms: number;
  message: string;
  mode?: ChatMode;
  /** True when the learner pressed "about this frame", overriding the wording. */
  force_frame?: boolean;
  thread_id?: string;
  completed_resource_ids?: string[];
}

export async function postChatWithFrame(
  frame: Blob,
  req: LiveFrameRequest,
): Promise<ChatResponse> {
  const base = baseUrl();
  if (!base) {
    throw new ApiError(0, "NEXT_PUBLIC_API_URL is not configured", "n/a");
  }
  const requestId = newRequestId();
  const token = req.session_token ?? getStoredSessionToken() ?? undefined;
  const form = new FormData();
  form.append("frame", frame, "frame.jpg");
  form.append("session_id", req.session_id);
  form.append("current_time_ms", String(Math.max(0, Math.floor(req.current_time_ms))));
  form.append("message", req.message);
  if (req.mode) form.append("mode", req.mode);
  if (req.video_id) form.append("video_id", req.video_id);
  if (token) form.append("session_token", token);
  form.append("force_frame", req.force_frame ? "true" : "false");
  if (req.thread_id) form.append("thread_id", req.thread_id);
  for (const id of req.completed_resource_ids ?? []) {
    form.append("completed_resource_ids", id);
  }

  let res: Response;
  try {
    res = await fetch(`${base}/chat/live-frame`, {
      method: "POST",
      body: form,
      headers: {
        "X-Session-Token": token ?? "",
        "x-request-id": requestId,
        ...(await authHeaders()),
      },
    });
  } catch (err) {
    throw new ApiError(
      0,
      err instanceof Error ? `Network error: ${err.message}` : "Network error",
      requestId,
    );
  }
  if (!res.ok) {
    throw await apiErrorFromResponse(res, requestId);
  }
  return (await res.json()) as ChatResponse;
}

/**
 * Phrases that mean "answer about the frame on my screen right now".
 *
 * A deliberate MIRROR of the backend's `app.retrieval.recency.is_frame_question`
 * — same intent, same job. The backend is the authority: it re-runs its own
 * classifier and refuses a capture that matches neither trigger, so a drift
 * between these two lists surfaces as a loud 422 instead of a silently ignored
 * frame. This copy exists only so the question trigger costs a capture without
 * the learner pressing anything.
 *
 * Kept shorter than the backend list on purpose: a false positive here spends a
 * vision call, and the backend's check is the backstop.
 */
const FRAME_QUESTION_PATTERNS: readonly RegExp[] = [
  /\bwhat am i looking at\b/i,
  /\bwhat'?s (?:on|at|in) (?:my |this |the )?(?:screen|frame|slide|page)\b/i,
  /\b(?:describe|explain) (?:this|the|current) (?:frame|screen|slide|scene|still|page)\b/i,
  /\bthis (?:frame|screen|slide|still)\b/i,
  /\bcurrent(?:ly)? (?:shown|on screen|displayed) (?:frame|slide)\b/i,
  /\b(?:what|which) (?:does|do|is|are)? ?(?:it|this|the (?:screen|frame|slide|board)) (?:say|says|read|reads|show|shows|contain|contains|display|displays)\b/i,
  /\bread (?:the|this) (?:screen|frame|slide|board|display)\b/i,
  /\b(?:what|which) (?:text|equation|formula|word|words|title|label|heading)(?:s)? (?:is|are)? ?(?:on|in) (?:the|this) (?:screen|frame|slide|board)\b/i,
  /\bon[- ]screen\b/i,
  /\b(?:on|in) (?:the|this) (?:screen|frame|slide)\b/i,
  // A reference to a DIFFERENT moment wins, exactly as on the backend.
  /\b(?:in|at|during) the (?:first|second|third|last|final|opening|beginning|start|end)\b/i,
  /\b(?:earlier|previously|before|at the start|at the beginning|already)\b/i,
];

const PAST_SCOPE_PATTERNS: readonly RegExp[] = FRAME_QUESTION_PATTERNS.slice(-2);

/** True when this question is about the frame currently on screen. */
export function isFrameQuestion(message: string): boolean {
  if (!message) return false;
  if (PAST_SCOPE_PATTERNS.some((re) => re.test(message))) return false;
  return FRAME_QUESTION_PATTERNS.slice(0, -2).some((re) => re.test(message));
}

export function formatApiError(err: unknown): string {
  if (err instanceof ApiError) {
    return err.status === 0
      ? `${err.message} (request ${err.requestId})`
      : `Request failed (${err.status}, request ${err.requestId}): ${err.message}`;
  }
  return err instanceof Error ? err.message : "Unknown error";
}

// ---------------------------------------------------------------------------
// YouTube ingestion (frozen contract - backend built in parallel).
//   POST /courses/{courseId}/ingest-youtube {url}
//     -> { job_id, resource_id, status }
//   GET  /ingestion/jobs/{jobId}
//     -> { job_id, resource_id, status: pending|processing|done|failed, error? }
//   GET  /courses/{courseId}/resources
//     -> { resources: [{ id, type, provider: "youtube"|"file",
//                        youtube_id?, title?, status }] }
//
// File ingestion (multipart; same job envelope):
//   POST /courses/{courseId}/ingest   multipart/form-data
//     file: <binary>  video|pdf|pptx|png|jpg|jpeg|webp
//     subject_id?: string  — when present the server derives the real course
//                            from the subject and auto-attaches on success.
//     -> { job_id, resource_id, status }
// ---------------------------------------------------------------------------

export interface IngestYouTubeResponse {
  job_id: string;
  resource_id: string;
  status: string;
}

/** File ingest returns the identical envelope; kept as a named alias. */
export type IngestFileResponse = IngestYouTubeResponse;

export type IngestJobStatus = "pending" | "processing" | "done" | "failed";

export interface IngestJob {
  job_id: string;
  resource_id: string;
  status: IngestJobStatus;
  error?: string | null;
  /** Present once an image ingest completes (1-based). */
  image_no?: number | null;
  mode?: "full" | "stream";
  stage?: string | null;
  processed_through_ms?: number;
  duration_ms?: number;
  windows_done?: number;
  windows_total?: number;
}

export type IngestMode = "full" | "stream";

export type ResourceProvider = "youtube" | "file";

export interface CourseResource {
  id: string;
  type: string;
  provider: ResourceProvider;
  youtube_id?: string | null;
  title?: string | null;
  status?: string | null;
  /** Present for partially processed videos (status "partial"). */
  processed_through_ms?: number | null;
  /** Streamed by the API for this exact resource; null when no file on disk. */
  asset_name?: string | null;
}

/** Per-resource media URL. Never share one demo URL across resources. */
/**
 * `fileName` adds a cosmetic trailing segment (same asset, resolved from the
 * id alone) for viewers that pick the format from the URL's extension.
 */
export function resourceMediaUrl(resourceId: string, fileName?: string): string {
  const base = (process.env.NEXT_PUBLIC_API_URL ?? "").replace(/\/$/, "");
  const url = `${base}/resources/${encodeURIComponent(resourceId)}/media`;
  return fileName ? `${url}/${encodeURIComponent(fileName)}` : url;
}

export interface CourseResourcesResponse {
  resources: CourseResource[];
}

export function ingestYouTube(
  courseId: string,
  url: string,
  subjectId?: string,
): Promise<IngestYouTubeResponse> {
  return apiFetch<IngestYouTubeResponse>(
    `/courses/${encodeURIComponent(courseId)}/ingest-youtube`,
    {
      method: "POST",
      body: JSON.stringify(
        subjectId ? { url, subject_id: subjectId } : { url },
      ),
    },
  );
}

/**
 * Upload a video / PDF / PPTX / image into a course, optionally scoped to a
 * subject.
 *
 * Deliberately NOT routed through `apiFetch`: that helper hardcodes
 * `Content-Type: application/json`, which on a FormData body strips the
 * multipart boundary and the backend then parses an empty form. Here the
 * Content-Type is left unset so the browser generates
 * `multipart/form-data; boundary=...` itself.
 */
export async function ingestFile(
  courseId: string,
  file: File,
  subjectId?: string,
  mode?: IngestMode,
): Promise<IngestFileResponse> {
  const base = baseUrl();
  if (!base) {
    throw new ApiError(0, "NEXT_PUBLIC_API_URL is not configured", "n/a");
  }
  const form = new FormData();
  form.append("file", file, file.name);
  if (subjectId) form.append("subject_id", subjectId);
  if (mode) form.append("mode", mode);

  const requestId = newRequestId();
  let res: Response;
  try {
    res = await fetch(`${base}/courses/${encodeURIComponent(courseId)}/ingest`, {
      method: "POST",
      body: form,
      headers: { "x-request-id": requestId, ...(await authHeaders()) },
    });
  } catch (err) {
    throw new ApiError(
      0,
      err instanceof Error ? `Network error: ${err.message}` : "Network error",
      requestId,
    );
  }
  if (!res.ok) {
    throw await apiErrorFromResponse(res, requestId);
  }
  return (await res.json()) as IngestFileResponse;
}

export function getIngestJob(jobId: string): Promise<IngestJob> {
  return apiFetch<IngestJob>(`/ingestion/jobs/${encodeURIComponent(jobId)}`);
}

export interface PositionResponse {
  status: "queued" | "running" | "idle" | "ready" | "failed";
  job_id?: string;
  /** Set with status "failed", e.g. a spent daily API quota. */
  error?: string;
  processed_through_ms: number;
  visual_through_ms?: number | null;
  duration_ms: number;
}

/** POST /courses/{cid}/resources/{rid}/position; keeps processing ahead of the student. */
export function reportPosition(
  courseId: string,
  resourceId: string,
  tMs: number,
): Promise<PositionResponse> {
  return apiFetch<PositionResponse>(
    `/courses/${encodeURIComponent(courseId)}/resources/${encodeURIComponent(resourceId)}/position`,
    { method: "POST", body: JSON.stringify({ t_ms: Math.max(0, Math.floor(tMs)) }) },
  );
}

export function getCourseResources(
  courseId: string,
): Promise<CourseResourcesResponse> {
  return apiFetch<CourseResourcesResponse>(
    `/courses/${encodeURIComponent(courseId)}/resources`,
  );
}

export interface DeleteResourceResponse {
  resource_id: string;
  deleted: boolean;
  detached_from: string[];
  asset_removed: boolean;
}

/** DELETE /courses/{course_id}/resources/{resource_id}; 404 if not owned. */
export function deleteResource(
  courseId: string,
  resourceId: string,
): Promise<DeleteResourceResponse> {
  return apiFetch<DeleteResourceResponse>(
    `/courses/${encodeURIComponent(courseId)}/resources/${encodeURIComponent(resourceId)}`,
    { method: "DELETE" },
  );
}

// ---------------------------------------------------------------------------
// Subjects (apps/api/app/subjects/schemas.py).
//   GET /subjects -> { subjects: [{ id, name, description, course_id,
//                                 resource_count, resource_ids, created_at }] }
// The id is opaque and comes from the backend — never a UI literal.
// ---------------------------------------------------------------------------

export interface Subject {
  id: string;
  name: string;
  description: string;
  course_id?: string | null;
  resource_count: number;
  resource_ids?: string[];
  created_at?: string;
}

export interface SubjectListResponse {
  subjects: Subject[];
}

export function getSubjects(): Promise<SubjectListResponse> {
  return apiFetch<SubjectListResponse>("/subjects");
}

export function getSubject(subjectId: string): Promise<Subject> {
  return apiFetch<Subject>(`/subjects/${encodeURIComponent(subjectId)}`);
}

export interface CreateSubjectRequest {
  name: string;
  description?: string;
}

/** POST /subjects -> 201 with the created Subject. */
export function createSubject(body: CreateSubjectRequest): Promise<Subject> {
  return apiFetch<Subject>("/subjects", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export interface DeleteSubjectResponse {
  deleted: string;
}

/**
 * DELETE /subjects/{id}. Removes the grouping only; the indexed resources
 * stay indexed.
 */
export function deleteSubject(subjectId: string): Promise<DeleteSubjectResponse> {
  return apiFetch<DeleteSubjectResponse>(
    `/subjects/${encodeURIComponent(subjectId)}`,
    { method: "DELETE" },
  );
}

/**
 * The materials attached to ONE subject -> `{ resources: [...] }`.
 *
 * Deliberately not `getCourseResources(subject.course_id)`: a subject and a
 * course are different things, and two subjects on the same course would then
 * render identical lists. Nor is it `getCourseResources(subjectId)` — that
 * path treats the subject id as a course id, matches nothing, and is why a
 * populated subject used to render "no videos".
 *
 * Same `CourseResource[]` shape as the course listing, so titles resolve with
 * no second lookup. 404 for an unknown subject; `{ resources: [] }` for a
 * subject with nothing attached — the two must not be collapsed.
 */
export function getSubjectResources(
  subjectId: string,
): Promise<CourseResourcesResponse> {
  return apiFetch<CourseResourcesResponse>(
    `/subjects/${encodeURIComponent(subjectId)}/resources`,
  );
}

/**
 * Friendly copy for ingest failures. Surfaces the backend `detail`
 * (quota / 422 validation messages) instead of a bare status code.
 */
export function formatIngestError(err: unknown, label = "Video"): string {
  if (err instanceof ApiError) {
    if (err.status === 422) {
      const hint =
        label === "Video"
          ? "Check the link is a public, watchable video."
          : "Check the file is a video, PDF, PPTX, or an image.";
      return `${label} rejected (422): ${err.message}. ${hint}`;
    }
    if (err.status === 429) {
      return `Ingest quota exceeded (429): ${err.message}. Try again later.`;
    }
    return formatApiError(err);
  }
  return err instanceof Error ? err.message : "Unknown error";
}

// Practice quizzes — apps/api/app/chat/quiz.py
//   POST /quiz        -> QuizResponse (MCQ/numerical answers included, shown
//                        only after the learner submits; graded here, no LLM)
//   POST /quiz/grade  -> QuizGrade    (subjective answers only)

export type QuizQuestionType = "mcq" | "numerical" | "subjective";

export interface QuizQuestion {
  id: string;
  type: QuizQuestionType;
  prompt: string;
  options?: string[] | null;
  answer_index?: number | null;
  answer?: number | null;
  tolerance?: number | null;
  unit?: string | null;
  model_answer?: string | null;
  key_points?: string[] | null;
  explanation: string;
  /** Null only in a general quiz (a Malai chat in no subject). */
  citation: Citation | null;
}

/** Most questions a quiz may have. */
export const MAX_QUIZ_QUESTIONS = 20;

export interface QuizRequest {
  session_id: string;
  video_id?: string;
  current_time_ms: number;
  completed_resource_ids?: string[];
  count?: number;
  types?: QuizQuestionType[];
  attempt?: number;
  /** Optional learner instructions: a topic, a focus, a difficulty. */
  prompt?: string;
  /** A Malai chat in a subject: only that subject's materials. */
  scope_resource_ids?: string[];
  /** A Malai chat in no subject: general knowledge, no materials. */
  general?: boolean;
  /** The chat the quiz was asked from (a general quiz reads it for a topic). */
  thread_id?: string;
}

/** Where a quiz may draw from: the chat that asked for it decides. */
export type QuizScope = Pick<
  QuizRequest,
  "video_id" | "completed_resource_ids" | "scope_resource_ids" | "general" | "thread_id"
>;

export interface QuizNote {
  session_id: string;
  thread_id?: string;
  event: "started" | "completed";
  /** started: the learner's message that asked for the quiz. */
  request?: string;
  count: number;
  /** completed: total score, and the questions not fully right. */
  score?: number;
  to_review?: string[];
}

/** Record a quiz in its chat; the server writes the lines and returns them. */
export async function postQuizNote(note: QuizNote, sessionToken?: string): Promise<ChatMessage[]> {
  const res = await apiFetch<MessagesEnvelope>("/quiz/note", {
    method: "POST",
    body: JSON.stringify(note),
    headers: tokenHeader(sessionToken),
  });
  const stamp = `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 8)}`;
  return res.messages.map((m, i) => ({ ...m, id: `note-${stamp}-${i}` }));
}

export interface QuizResponse {
  questions: QuizQuestion[];
  refused: boolean;
  message?: string | null;
  current_time_ms: number;
}

export interface QuizGrade {
  score: number;
  verdict: "correct" | "partial" | "incorrect";
  feedback: string;
  missing: string[];
}

function tokenHeader(sessionToken?: string): Record<string, string> | undefined {
  const token = sessionToken ?? getStoredSessionToken() ?? undefined;
  return token ? { "X-Session-Token": token } : undefined;
}

export function postQuiz(req: QuizRequest, sessionToken?: string): Promise<QuizResponse> {
  return apiFetch<QuizResponse>("/quiz", {
    method: "POST",
    body: JSON.stringify(req),
    headers: tokenHeader(sessionToken),
  });
}

export function gradeQuizAnswer(
  sessionId: string,
  question: QuizQuestion,
  answer: string,
  sessionToken?: string,
): Promise<QuizGrade> {
  return apiFetch<QuizGrade>("/quiz/grade", {
    method: "POST",
    body: JSON.stringify({
      session_id: sessionId,
      prompt: question.prompt,
      model_answer: question.model_answer ?? "",
      key_points: question.key_points ?? [],
      answer,
    }),
    headers: tokenHeader(sessionToken),
  });
}

// Chapters — apps/api/app/ingestion/chapters.py
//   GET /resources/{id}/chapters -> ChaptersResponse
// Written automatically after ingestion: a video's from its transcript, a
// PDF's by page, slides by slide. "pending" while they are being written.

export interface ResourceChapter {
  title: string;
  /** Videos. */
  start_ms?: number | null;
  end_ms?: number | null;
  /** PDFs (1-based pages). */
  page_start?: number | null;
  page_end?: number | null;
  /** Slides (1-based). */
  slide_start?: number | null;
  slide_end?: number | null;
}

export interface ChaptersResponse {
  resource_id: string;
  status: "ready" | "pending" | "unavailable";
  chapters: ResourceChapter[];
}

export function getChapters(resourceId: string): Promise<ChaptersResponse> {
  return apiFetch<ChaptersResponse>(`/resources/${encodeURIComponent(resourceId)}/chapters`);
}
