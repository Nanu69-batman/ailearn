"use client";

import { useEffect, useRef, useState } from "react";
import {
  createLmsSession,
  formatApiError,
  isApiConfigured,
  setStoredSessionToken,
  type StoredSession,
} from "../lib/api";
import { devSessionFixture, mockSession } from "../lib/fixtures";
import styles from "./SessionBootstrap.module.css";

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
      const res = await createLmsSession(devSessionFixture(courseId));
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
      <div className={styles.box} role="status">
        Creating learning session…
      </div>
    );
  }

  if (status === "error") {
    return (
      <div className={styles.box} role="alert">
        <p className={styles.errTitle}>Could not reach the API.</p>
        <p className={styles.errDetail}>{error}</p>
        <div className={styles.actions}>
          <button type="button" className={styles.primary} onClick={() => void bootstrap()}>
            Retry
          </button>
          <button
            type="button"
            className={styles.secondary}
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
