"use client";

import { useEffect, useRef, useState } from "react";
import {
  createLmsSession,
  formatApiError,
  getCourseResources,
  isApiConfigured,
  setStoredSessionToken,
  type StoredSession,
} from "../lib/api";
import { devSessionFixture, mockSession } from "../lib/fixtures";

interface SessionBootstrapProps {
  courseId: string;
  onReady: (session: StoredSession, offline: boolean) => void;
}

type Status = "pending" | "loading" | "error";

function toStoredSession(res: {
  session_id: string;
  session_token: string;
}): StoredSession {
  return { session_id: res.session_id, session_token: res.session_token };
}

export default function SessionBootstrap({ courseId, onReady }: SessionBootstrapProps) {
  const [status, setStatus] = useState<Status>("pending");
  const [error, setError] = useState<string | null>(null);
  const started = useRef(false);

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
      try {
        const fetched = await getCourseResources(courseId);
        const ids = (fetched.resources ?? []).map((r) => r.id).filter(Boolean);
        if (ids.length > 0) {
          launch.resource_scope = ids;
          launch.resource_id = ids[0];
        }
      } catch {
        // Fall through with fixture scope; createLmsSession below surfaces
        // any real connectivity problem.
      }
      const res = await createLmsSession(launch);
      // Persist session_token alongside session_id for chat + history calls.
      setStoredSessionToken(res.session_token);
      setStatus("pending");
      onReady(toStoredSession(res), false);
    } catch (err) {
      setStatus("error");
      setError(formatApiError(err));
    }
  }

  useEffect(() => {
    if (!started.current) {
      started.current = true;
      void bootstrap();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [courseId]);

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
