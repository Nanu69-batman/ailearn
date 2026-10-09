/**
 * Typed API client for the AI Learning Assistant backend.
 * Contract (IMPLEMENTATION_PLAN.md §3):
 *   POST /lms/session -> { session_id, session_token, learner_id, course_id }
 *   POST /chat        -> { answer, citations[], refused } (needs session_token:
 *                        body field + X-Session-Token header; 401 = expired)
 *   GET  /sessions/{id}/messages (needs X-Session-Token header; 401 = expired)
 *
 * All requests carry an `x-request-id` header for log correlation.
 * Backend may still be in progress — callers fall back to local
 * fixtures (lib/fixtures.ts) when the API is unreachable.
 */

export type ChatMode =
  | "qa"
  | "explain_simple"
  | "analogy"
  | "summarize"
  | "compare"
  | "quiz";

/**
 * Backend source of truth: apps/api/app/chat/schemas.py `ChatMode`.
 * The UI label for "qa" stays "Ask" (see ModeSelector) — only the
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
}

export type CitationSourceType =
  | "video"
  | "video_transcript"
  | "pdf"
  | "pptx"
  | "note"
  | "code";

export interface Citation {
  chunk_id: string;
  source_type?: CitationSourceType | null;
  video_id?: string | null;
  start_ms?: number | null;
  end_ms?: number | null;
  page_no?: number | null;
  slide_no?: number | null;
}

export interface ChatResponse {
  answer: string;
  citations: Citation[];
  refused: boolean;
}

export type MessageRole = "user" | "assistant";

export interface ChatMessage {
  id: string;
  role: MessageRole;
  content: string;
  citations: Citation[];
  created_at: string;
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
    throw new ApiError(res.status, detail, requestId);
  }
  return (await res.json()) as T;
}

export function createLmsSession(
  req: LmsSessionRequest,
): Promise<LmsSessionResponse> {
  return apiFetch<LmsSessionResponse>("/lms/session", {
    method: "POST",
    body: JSON.stringify(req),
  });
}

export function postChat(req: ChatRequest): Promise<ChatResponse> {
  // Header X-Session-Token preferred; body field kept for back-compat since
  // apps/api/app/chat/router.py verifies body.session_token on POST /chat.
  const token = req.session_token ?? getStoredSessionToken() ?? undefined;
  return apiFetch<ChatResponse>("/chat", {
    method: "POST",
    body: JSON.stringify(token ? { ...req, session_token: token } : req),
    headers: token ? { "X-Session-Token": token } : undefined,
  });
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
): Promise<ChatMessage[]> {
  // Header X-Session-Token preferred (router also accepts ?session_token=).
  const token = sessionToken ?? getStoredSessionToken() ?? undefined;
  const envelope = await apiFetch<MessagesEnvelope>(
    `/sessions/${encodeURIComponent(sessionId)}/messages`,
    token ? { headers: { "X-Session-Token": token } } : undefined,
  );
  return envelope.messages.map((m, i) => ({ ...m, id: `${sessionId}#${i}` }));
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
// YouTube ingestion (frozen contract — backend built in parallel).
//   POST /courses/{courseId}/ingest-youtube {url}
//     -> { job_id, resource_id, status }
//   GET  /ingestion/jobs/{jobId}
//     -> { job_id, resource_id, status: pending|processing|done|failed, error? }
//   GET  /courses/{courseId}/resources
//     -> { resources: [{ id, type, provider: "youtube"|"file",
//                        youtube_id?, title?, status }] }
// ---------------------------------------------------------------------------

export interface IngestYouTubeResponse {
  job_id: string;
  resource_id: string;
  status: string;
}

export type IngestJobStatus = "pending" | "processing" | "done" | "failed";

export interface IngestJob {
  job_id: string;
  resource_id: string;
  status: IngestJobStatus;
  error?: string | null;
}

export type ResourceProvider = "youtube" | "file";

export interface CourseResource {
  id: string;
  type: string;
  provider: ResourceProvider;
  youtube_id?: string | null;
  title?: string | null;
  status?: string | null;
  /** Streamed by the API for this exact resource; null when no file on disk. */
  asset_name?: string | null;
}

/** Per-resource media URL. Never share one demo URL across resources. */
export function resourceMediaUrl(resourceId: string): string {
  const base = (process.env.NEXT_PUBLIC_API_URL ?? "").replace(/\/$/, "");
  return `${base}/resources/${encodeURIComponent(resourceId)}/media`;
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

export function getIngestJob(jobId: string): Promise<IngestJob> {
  return apiFetch<IngestJob>(`/ingestion/jobs/${encodeURIComponent(jobId)}`);
}

export function getCourseResources(
  courseId: string,
): Promise<CourseResourcesResponse> {
  return apiFetch<CourseResourcesResponse>(
    `/courses/${encodeURIComponent(courseId)}/resources`,
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
export function formatIngestError(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.status === 422) {
      return `Video rejected (422): ${err.message}. Check the link is a public, watchable video.`;
    }
    if (err.status === 429) {
      return `Ingest quota exceeded (429): ${err.message}. Try again later.`;
    }
    return formatApiError(err);
  }
  return err instanceof Error ? err.message : "Unknown error";
}
