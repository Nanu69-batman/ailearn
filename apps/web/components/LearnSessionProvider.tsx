"use client";

/**
 * One learning session per browser tab, shared by every route in the `(learn)`
 * group — /dashboard, /ai-tutor/subjects and the player included.
 *
 * Why this exists: those routes are separate page components, so anything they
 * both need has to live above them. The session is the expensive part —
 * re-running `POST /lms/session` on every navigation would mint a new session
 * and drop the chat transcript. This provider is mounted in the `(learn)`
 * layout, which Next keeps mounted across a navigation between its child
 * routes, so moving between /dashboard and the tutor routes never destroys the
 * session object and therefore never re-mints. It is also rehydrated from
 * `sessionStorage`, so a hard reload on a deep content URL rejoins the same
 * session instead of starting a second one.
 *
 * `subjectId` is only *needed* once the URL names a subject. On /dashboard and
 * on the subject list there is no subject, so no session is bootstrapped and the
 * children render directly — those pages do not use chat.
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { useParams } from "next/navigation";
import {
  clearStoredMintedResourceIds,
  clearStoredSessionToken,
  getCourseResources,
  getStoredMintedResourceIds,
  getStoredSessionToken,
  getSubject,
  getSubjectResources,
  isApiConfigured,
  sameResourceIdSet,
  setStoredMintedResourceIds,
  setStoredSessionToken,
  type StoredSession,
  type Subject,
} from "@/lib/api";
import { firstParam } from "@/lib/learn";
import SessionBootstrap from "@/components/SessionBootstrap";

/** `session_token` lives in lib/api.ts; the id is only known to this layer. */
const SESSION_ID_KEY = "ailearn.session_id";

export interface LearnSession {
  session: StoredSession | null;
  /** True when the API was unreachable and we fell back to fixtures. */
  offline: boolean;
  /**
   * False until the sessionStorage rehydration attempt has finished. The
   * bootstrap stays hidden until then, so a reload can never race a rejoin
   * against a freshly minted session.
   */
  restored: boolean;
  /**
   * The backend's record for the subject in the URL, or null when the URL
   * names no subject (/dashboard, /ai-tutor/subjects) or the lookup failed.
   * Exposed so pages can show real metadata without a second request.
   */
  subject: Subject | null;
  setSession: (session: StoredSession, offline: boolean) => void;
  /** Explicit user-initiated reset (expired-session banner). */
  reset: () => void;
}

const LearnSessionContext = createContext<LearnSession | null>(null);

export function useLearnSession(): LearnSession {
  const ctx = useContext(LearnSessionContext);
  if (!ctx) {
    throw new Error("useLearnSession must be used inside LearnSessionProvider");
  }
  return ctx;
}

function readStoredSessionId(): string | null {
  try {
    if (typeof window === "undefined" || !window.sessionStorage) return null;
    return window.sessionStorage.getItem(SESSION_ID_KEY);
  } catch {
    return null;
  }
}

function writeStoredSessionId(sessionId: string): void {
  try {
    if (typeof window === "undefined" || !window.sessionStorage) return;
    window.sessionStorage.setItem(SESSION_ID_KEY, sessionId);
  } catch {
    // Storage unavailable (private mode / quota) — in-memory state still works
    // for the lifetime of the layout.
  }
}

export default function LearnSessionProvider({
  children,
}: {
  children: ReactNode;
}) {
  const params = useParams<{ subjectId?: string | string[] }>();
  const subjectId = firstParam(params.subjectId);

  const [session, setSessionState] = useState<StoredSession | null>(null);
  const [offline, setOffline] = useState(false);
  const [restored, setRestored] = useState(false);
  const [subject, setSubject] = useState<Subject | null>(null);

  /**
   * The scope the session is bound to. The backend's `SubjectResponse` carries
   * the course the subject belongs to, and every content endpoint is
   * course-scoped, so that is what the session must launch against. When the
   * subject record cannot be read, the subject id itself is the best available
   * scope — the same value this route used before `/subjects` existed.
   */
  const [scopeId, setScopeId] = useState<string | null>(subjectId);

  useEffect(() => {
    // Only ever advance the scope. Going /subject -> /dashboard must not blank
    // it out, or a bootstrap already in flight would be torn down and the
    // session re-minted on the way back.
    if (subjectId) setScopeId(subjectId);
  }, [subjectId]);

  // Rehydration runs once, after mount: reading sessionStorage during render
  // would desynchronise the server-rendered HTML from the first client render.
  useEffect(() => {
    const token = getStoredSessionToken();
    const sessionId = readStoredSessionId();
    if (token && sessionId) {
      setSessionState({ session_id: sessionId, session_token: token });
    }
    setRestored(true);
  }, []);

  // Resolve the subject once per subject id. Not fatal: on failure the page
  // still works against the subject id as the scope, exactly as it did before
  // this lookup existed.
  useEffect(() => {
    if (!subjectId || !isApiConfigured()) {
      setSubject(null);
      return;
    }
    let cancelled = false;
    setSubject(null);
    void (async () => {
      try {
        const found = await getSubject(subjectId);
        if (cancelled) return;
        setSubject(found);
        if (found.course_id) setScopeId(found.course_id);
      } catch {
        // Unknown subject or an unreachable API. Leave the scope on the subject
        // id and let the page report what it can.
        if (!cancelled) setSubject(null);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [subjectId]);

  /**
   * Resource ids the active scope currently shows. `null` means "not resolved
   * yet" or "the listing failed" — in both cases the existing session must be
   * trusted rather than thrown away. An EMPTY array is also non-actionable: a
   * course/subject with nothing attached is not evidence that the grant is
   * stale, and re-minting on it would loop.
   */
  const [visibleResourceIds, setVisibleResourceIds] = useState<string[] | null>(null);
  /**
   * The subject those ids were read for. Ids from a previous route must never be
   * handed to a bootstrap for a different subject — that would mint a grant
   * scoped to materials the current route cannot show.
   */
  const [visibleForSubject, setVisibleForSubject] = useState<string | null>(null);
  /**
   * False until the listing for the active scope has settled (either way).
   * Holding the bootstrap back until then stops it from minting against the
   * fixture placeholder scope during the window before the real ids land.
   */
  const [resourceIdsSettled, setResourceIdsSettled] = useState(false);

  /** Read inside `setSession`, which must stay referentially stable. */
  const subjectIdRef = useRef<string | null>(subjectId);
  subjectIdRef.current = subjectId;

  const setSession = useCallback(
    (next: StoredSession, isOffline: boolean): void => {
      setStoredSessionToken(next.session_token);
      writeStoredSessionId(next.session_id);
      setStoredMintedResourceIds(next.minted_resource_ids ?? null);
      setSessionState(next);
      setOffline(isOffline);
      // A fresh grant supersedes whatever the previous one was scoped to, but
      // only if the mint actually resolved real ids. An unknown/fallback grant
      // leaves the listing-derived ids in place — those are what the route can
      // legitimately see.
      if (next.minted_resource_ids && next.minted_resource_ids.length > 0) {
        setVisibleResourceIds(next.minted_resource_ids);
        setVisibleForSubject(subjectIdRef.current);
      }
    },
    [],
  );

  const reset = useCallback((): void => {
    clearStoredSessionToken();
    clearStoredMintedResourceIds();
    try {
      if (typeof window !== "undefined" && window.sessionStorage) {
        window.sessionStorage.removeItem(SESSION_ID_KEY);
      }
    } catch {
      // Nothing to clean up if storage is unavailable.
    }
    setSessionState(null);
    // `visibleResourceIds` is deliberately NOT cleared: it describes the
    // current route's materials, not the session, and keeping it lets the
    // replacement bootstrap mint against the same set without a second fetch.
    setOffline(false);
  }, []);

  // Resolve what the active scope shows. Subject route -> subject listing (the
  // learner sees only that subject's materials); otherwise the course listing.
  // Failure is non-fatal and leaves `visibleResourceIds` null.
  useEffect(() => {
    // No subject in the URL (/dashboard, the subject list): these routes need no
    // session, and re-resolving against the whole course here would judge the
    // learner's subject-scoped grant against a wider set and drop it — losing
    // chat history on the way back. Leave the last subject's ids alone.
    if (!subjectId || !scopeId || !isApiConfigured()) {
      setResourceIdsSettled(true);
      return;
    }
    let cancelled = false;
    setResourceIdsSettled(false);
    void (async () => {
      try {
        const res = subjectId
          ? await getSubjectResources(subjectId)
          : await getCourseResources(scopeId);
        if (cancelled) return;
        const ids = (res.resources ?? []).map((r) => r.id).filter(Boolean);
        setVisibleForSubject(subjectId);
        setVisibleResourceIds(ids.length > 0 ? ids : null);
      } catch {
        // Unreachable API / unknown scope. Trust the session we already have.
        if (!cancelled) setVisibleResourceIds(null);
      } finally {
        if (!cancelled) setResourceIdsSettled(true);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [scopeId, subjectId]);

  /**
   * Revalidate a rehydrated grant against the materials on screen.
   *
   * Before this existed, `sessionStorage` was trusted unconditionally for the
   * lifetime of the tab, so a grant minted against a since-deleted resource (or
   * against a different subject) produced permanent refusals with no recovery
   * path. Now: ids missing -> re-mint; ids differ -> re-mint; ids match -> keep,
   * which is what preserves the chat history across /dashboard -> subject ->
   * player navigation.
   *
   * Re-minting only ever CLEARS the session this client owns so
   * SessionBootstrap mints a fresh, correctly-scoped grant. The replacement
   * scope is always derived from the route's own listing; nothing is widened.
   */
  const revalidatedKey = useRef<string | null>(null);
  useEffect(() => {
    if (!restored || !session || !scopeId) return;
    // Only judge a grant against the listing for the subject it belongs to.
    if (!subjectId || visibleForSubject !== subjectId) return;
    const current = visibleResourceIds;
    // Not resolved yet, failed, or genuinely empty: cannot judge the grant.
    if (!current || current.length === 0) return;
    const key = `${scopeId}|${subjectId ?? ""}|${[...current].sort().join(",")}`;
    if (revalidatedKey.current === key) return; // already judged for this set
    revalidatedKey.current = key;

    const minted = getStoredMintedResourceIds();
    if (minted && sameResourceIdSet(minted, current)) return; // still valid

    // Stale (or pre-fix, no record) grant -> drop it and let the bootstrap mint.
    reset();
  }, [
    restored,
    session,
    scopeId,
    subjectId,
    visibleForSubject,
    visibleResourceIds,
    reset,
  ]);

  const value = useMemo<LearnSession>(
    () => ({ session, offline, restored, subject, setSession, reset }),
    [session, offline, restored, subject, setSession, reset],
  );

  return (
    <LearnSessionContext.Provider value={value}>
      {/* One bootstrap for the whole group. It is rendered only when a session
          is needed and none exists, so navigating between /dashboard, the
          subject list, the subject page and the player neither re-mints nor
          re-bootstraps. Routes that name no subject (the dashboard, the subject
          list) need no session, so they render straight through. */}
      {session ? (
        children
      ) : scopeId ? (
        !restored || !resourceIdsSettled ? (
          // One listing round-trip for the active scope. Rendering nothing here
          // (rather than children) is deliberate: minting against the fixture
          // placeholder scope before the real ids land is what produced
          // permanent refusals.
          <div className="rounded-xl border border-gray-200 bg-white p-6 text-center text-sm text-gray-500 shadow-sm" role="status">
            <span className="mr-2 inline-block h-4 w-4 animate-spin rounded-full border-2 border-violet-400 border-t-transparent align-middle" />
            Creating learning session…
          </div>
        ) : (
          <SessionBootstrap
            courseId={scopeId}
            // Only hand over ids resolved for THIS route; otherwise the
            // bootstrap resolves them itself.
            subjectId={subjectId}
            resourceIds={visibleForSubject === subjectId ? visibleResourceIds : null}
            onReady={setSession}
          />
        )
      ) : (
        children
      )}
    </LearnSessionContext.Provider>
  );
}