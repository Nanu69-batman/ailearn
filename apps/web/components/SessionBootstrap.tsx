"use client";

import { useEffect, useRef, useState } from "react";
import {
  createLmsSession,
  formatApiError,
  getCourseResources,
  getSubjectResources,
  isApiConfigured,
  setStoredSessionToken,
  type StoredSession,
} from "../lib/api";
import { devSessionFixture, mockSession } from "../lib/fixtures";

interface SessionBootstrapProps {
  /** Course the session launches against (every content endpoint is course-scoped). */
  courseId: string;
  /**
   * Subject named by the route, when there is one. On a subject route the grant
   * is built from THAT subject's materials rather than the whole course: the
   * learner can only see the subject listing, so anything outside it would be a
   * grant the route cannot justify. Falls back to the course listing when absent.
   */
  subjectId?: string | null;
  /**
   * Resource ids the provider already resolved for the active scope, or null
   * when that lookup failed / returned nothing. When non-empty these are used
   * verbatim (no second request). When null the bootstrap re-resolves them
   * itself and, if that also fails, falls back to the fixture scope exactly as
   * before.
   */
  resourceIds?: string[] | null;
  onReady: (session: StoredSession, offline: boolean) => void;
}

type Status = "pending" | "loading" | "error";

function toStoredSession(res: {
  session_id: string;
  session_token: string;
  minted_resource_ids?: string[];
}): StoredSession {
  return {
    session_id: res.session_id,
    session_token: res.session_token,
    minted_resource_ids: res.minted_resource_ids,
  };
}

function idSetKey(ids: string[] | null | undefined): string {
  return ids && ids.length > 0 ? [...ids].sort().join(",") : "unknown";
}

export default function SessionBootstrap({
  courseId,
  subjectId,
  resourceIds,
  onReady,
}: SessionBootstrapProps) {
  const [status, setStatus] = useState<Status>("pending");
  const [error, setError] = useState<string | null>(null);
  /**
   * Last key we actually minted for, not a plain "have I started" boolean. A
   * plain boolean meant a re-ingest that changed the visible resource set could
   * never re-mint; keying on (course, subject, ids) keeps the original
   * no-double-mint intent while letting a genuinely different material set mint
   * a new grant.
   */
  const startedKey = useRef<string | null>(null);

  async function bootstrap(): Promise<void> {
    setStatus("loading");
    setError(null);
    if (!isApiConfigured()) {
      // Mock/offline mode synthesizes a "mock-token" value so downstream
      // postChat/getMessages carry a token even without a live backend.
      setStoredSessionToken(mockSession.session_token);
      setStatus("pending");
      onReady(toStoredSession(mockSession), true);
      return;
    }
    try {
      // Scope the session to resources that actually exist. The fixture's
      // placeholder IDs match nothing in the vector store, so retrieval
      // returns zero passages and every answer is refused — even after the
      // video has been watched. Real IDs fix chat without touching the
      // backend's scope enforcement.
      const launch = devSessionFixture(courseId);
      let ids = resourceIds ?? null;
      if (!ids || ids.length === 0) {
        // No ids handed down: resolve them here, subject-first so the grant
        // never exceeds what the current route shows.
        try {
          const fetched = subjectId
            ? await getSubjectResources(subjectId)
            : await getCourseResources(courseId);
          const found = (fetched.resources ?? []).map((r) => r.id).filter(Boolean);
          if (found.length > 0) ids = found;
        } catch {
          // Fall through with fixture scope; createLmsSession below surfaces
          // any real connectivity problem.
        }
      }
      if (ids && ids.length > 0) {
        launch.resource_scope = ids;
        launch.resource_id = ids[0];
      }
      const res = await createLmsSession(launch);
      // Persist session_token alongside session_id for chat + history calls.
      setStoredSessionToken(res.session_token);
      setStatus("pending");
      // Record the ids this grant was minted against so a later revalidation
      // can tell a still-valid grant from a stale one.
      onReady(
        { ...toStoredSession(res), minted_resource_ids: ids && ids.length > 0 ? ids : undefined },
        false,
      );
    } catch (err) {
      setStatus("error");
      setError(formatApiError(err));
    }
  }

  useEffect(() => {
    const key = `${courseId}|${subjectId ?? ""}|${idSetKey(resourceIds)}`;
    if (startedKey.current === key) return;
    startedKey.current = key;
    void bootstrap();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [courseId, subjectId, resourceIds]);

  if (status === "loading") {
    return (
      <div className="rounded-xl border border-gray-200 bg-white p-6 text-center text-sm text-gray-500 shadow-sm" role="status">
        <span className="mr-2 inline-block h-4 w-4 animate-spin rounded-full border-2 border-violet-400 border-t-transparent align-middle" />
        Creating learning session…
      </div>
    );
  }

  if (status === "error") {
    return (
      <div className="rounded-xl border border-red-200 bg-red-50 p-6 shadow-sm" role="alert">
        <p className="font-semibold text-red-600">Could not reach the API.</p>
        <p className="mt-1 text-sm text-gray-600">{error}</p>
        <div className="mt-4 flex gap-3">
          <button type="button" className="rounded-xl bg-violet-600 px-4 py-2 text-sm font-medium text-white transition hover:bg-violet-700" onClick={() => void bootstrap()}>
            Retry
          </button>
          <button
            type="button"
            className="rounded-xl border border-gray-200 bg-white px-4 py-2 text-sm text-gray-600 transition hover:bg-gray-50"
            onClick={() => {
              setStoredSessionToken(mockSession.session_token);
              onReady(toStoredSession(mockSession), true);
            }}
          >
            Continue offline (mock)
          </button>
        </div>
      </div>
    );
  }

  return null;
}