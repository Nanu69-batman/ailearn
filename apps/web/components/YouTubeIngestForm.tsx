"use client";

import { useEffect, useRef, useState } from "react";
import {
  formatIngestError,
  getIngestJob,
  ingestYouTube,
} from "../lib/api";
import {
  YOUTUBE_URL_HINT,
  extractYouTubeId,
} from "../lib/youtube";
import styles from "./YouTubeIngestForm.module.css";

export interface IngestedVideo {
  resourceId: string;
  youtubeId: string;
  title?: string;
}

interface YouTubeIngestFormProps {
  courseId: string;
  onReady: (video: IngestedVideo) => void;
  disabled?: boolean;
}

type Phase = "idle" | "submitting" | "polling" | "error";

const POLL_INTERVAL_MS = 2000;
const POLL_TIMEOUT_MS = 10 * 60 * 1000; // 10 minutes

function formatElapsed(ms: number): string {
  const s = Math.floor(ms / 1000);
  const m = Math.floor(s / 60);
  return `${m}:${(s % 60).toString().padStart(2, "0")}`;
}

export default function YouTubeIngestForm({
  courseId,
  onReady,
  disabled,
}: YouTubeIngestFormProps) {
  const [url, setUrl] = useState("");
  const [phase, setPhase] = useState<Phase>("idle");
  const [clientError, setClientError] = useState<string | null>(null);
  const [jobError, setJobError] = useState<string | null>(null);
  const [jobStatus, setJobStatus] = useState<string | null>(null);
  const [elapsedMs, setElapsedMs] = useState(0);
  const cancelRef = useRef(false);
  const timersRef = useRef<number[]>([]);

  function clearTimers(): void {
    for (const id of timersRef.current) window.clearInterval(id);
    timersRef.current = [];
  }

  useEffect(() => clearTimers, []);

  function cancel(message?: string): void {
    cancelRef.current = true;
    clearTimers();
    setPhase(message ? "error" : "idle");
    if (message) setJobError(message);
  }

  async function pollJob(jobId: string, startedAt: number): Promise<void> {
    setPhase("polling");
    const tick = async (): Promise<void> => {
      if (cancelRef.current) return;
      if (Date.now() - startedAt >= POLL_TIMEOUT_MS) {
        cancel(
          "Timed out after 10 minutes waiting for the video to be ready. " +
            "The ingest may still be running — try again later or load a different video.",
        );
        return;
      }
      setElapsedMs(Date.now() - startedAt);
      try {
        const job = await getIngestJob(jobId);
        if (cancelRef.current) return;
        setJobStatus(job.status);
        if (job.status === "done") {
          clearTimers();
          setPhase("idle");
          const youtubeId = extractYouTubeId(url) ?? job.resource_id;
          onReady({ resourceId: job.resource_id, youtubeId });
          return;
        }
        if (job.status === "failed") {
          cancel(
            job.error?.trim()
              ? `Video processing failed: ${job.error.trim()}`
              : "Video processing failed on the server. Try a different video.",
          );
        }
      } catch {
        // Transient poll blip — keep polling; the 10-minute timeout
        // bounds the wait so one failed poll never kills the job.
      }
    };
    await tick();
    if (cancelRef.current) return;
    const id = window.setInterval(() => void tick(), POLL_INTERVAL_MS);
    timersRef.current.push(id);
  }

  async function handleSubmit(e: React.FormEvent): Promise<void> {
    e.preventDefault();
    if (phase === "submitting" || phase === "polling" || disabled) return;
    const youtubeId = extractYouTubeId(url);
    if (!youtubeId) {
      setClientError(
        `That doesn't look like a YouTube link. ${YOUTUBE_URL_HINT}`,
      );
      return;
    }
    setClientError(null);
    setJobError(null);
    setJobStatus(null);
    setElapsedMs(0);
    cancelRef.current = false;
    setPhase("submitting");
    try {
      const res = await ingestYouTube(courseId, url.trim());
      if (cancelRef.current) return;
      if (!res.job_id) {
        setPhase("error");
        setJobError("Server did not return a job id. Please try again.");
        return;
      }
      await pollJob(res.job_id, Date.now());
    } catch (err) {
      if (cancelRef.current) return;
      setPhase("error");
      setJobError(formatIngestError(err));
    }
  }

  const busy = phase === "submitting" || phase === "polling";

  return (
    <div className={styles.box}>
      <h2 className={styles.title}>Load a YouTube video</h2>
      <p className={styles.sub}>
        Paste a link — we fetch the transcript and make it askable.
      </p>
      <form className={styles.form} onSubmit={(e) => void handleSubmit(e)}>
        <label className={styles.srOnly} htmlFor="yt-url">
          YouTube video URL
        </label>
        <input
          id="yt-url"
          className={styles.input}
          type="url"
          inputMode="url"
          value={url}
          onChange={(e) => {
            setUrl(e.target.value);
            if (clientError) setClientError(null);
          }}
          placeholder="https://www.youtube.com/watch?v=…"
          disabled={busy || disabled}
          autoComplete="off"
          aria-invalid={clientError ? true : undefined}
          aria-describedby={clientError ? "yt-url-error" : undefined}
        />
        <button
          type="submit"
          className={styles.primary}
          disabled={busy || disabled || url.trim().length === 0}
        >
          {phase === "submitting"
            ? "Starting…"
            : phase === "polling"
              ? "Loading…"
              : "Load video"}
        </button>
      </form>

      {clientError && (
        <div id="yt-url-error" className={styles.error} role="alert">
          Invalid YouTube URL. {clientError}
        </div>
      )}

      {busy && (
        <div className={styles.status} role="status">
          <span className={styles.spinner} aria-hidden="true" />
          {phase === "submitting"
            ? "Starting video ingest…"
            : `Processing video${jobStatus ? ` (${jobStatus})` : ""} · ${formatElapsed(elapsedMs)} elapsed`}
          <button
            type="button"
            className={styles.secondary}
            onClick={() => cancel()}
          >
            Cancel
          </button>
        </div>
      )}

      {phase === "error" && jobError && (
        <div className={styles.error} role="alert">
          <p className={styles.errTitle}>Could not load this video.</p>
          <p className={styles.errDetail}>{jobError}</p>
          <div className={styles.actions}>
            <button
              type="button"
              className={styles.primary}
              onClick={() => {
                setPhase("idle");
                setJobError(null);
                cancelRef.current = false;
              }}
            >
              Try again
            </button>
            <button
              type="button"
              className={styles.secondary}
              onClick={() => cancel()}
            >
              Dismiss
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
