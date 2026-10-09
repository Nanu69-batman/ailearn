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

export interface IngestedVideo {
  resourceId: string;
  youtubeId: string;
  title?: string;
}

interface YouTubeIngestFormProps {
  courseId: string;
  onReady: (video: IngestedVideo) => void;
  disabled?: boolean;
  /**
   * When set (subject pages), the ingest is scoped to the subject's real
   * course server-side and the resource is auto-attached to the subject.
   */
  subjectId?: string;
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
  subjectId,
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
      const res = await ingestYouTube(courseId, url.trim(), subjectId);
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
    <div className="rounded-xl border border-gray-200 bg-white p-5 shadow-sm">
      <h2 className="text-base font-semibold text-gray-900">Load a YouTube video</h2>
      <p className="mt-1 text-sm text-gray-500">
        Paste a link — we fetch the transcript and make it askable.
      </p>
      <form className="mt-4 flex flex-col gap-3 sm:flex-row" onSubmit={(e) => void handleSubmit(e)}>
        <label className="sr-only" htmlFor="yt-url">
          YouTube video URL
        </label>
        <input
          id="yt-url"
          className="flex-1 rounded-xl border border-gray-200 bg-gray-50 px-3 py-2 text-sm text-gray-800 placeholder-gray-400 outline-none transition focus:border-violet-400 focus:bg-white focus:ring-2 focus:ring-violet-200"
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
          className="rounded-xl bg-violet-600 px-5 py-2 text-sm font-medium text-white transition hover:bg-violet-700 disabled:opacity-40"
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
        <div id="yt-url-error" className="mt-3 rounded-xl border border-red-200 bg-red-50 p-3 text-sm text-red-600" role="alert">
          Invalid YouTube URL. {clientError}
        </div>
      )}

      {busy && (
        <div className="mt-3 flex items-center gap-3 rounded-xl border border-gray-200 bg-gray-50 p-3 text-sm text-gray-500" role="status">
          <span className="inline-block h-4 w-4 animate-spin rounded-full border-2 border-violet-400 border-t-transparent" aria-hidden="true" />
          {phase === "submitting"
            ? "Starting video ingest…"
            : `Processing video${jobStatus ? ` (${jobStatus})` : ""} · ${formatElapsed(elapsedMs)} elapsed`}
          <button
            type="button"
            className="ml-auto rounded-lg border border-gray-200 bg-white px-3 py-1 text-xs text-gray-500 hover:bg-gray-50"
            onClick={() => cancel()}
          >
            Cancel
          </button>
        </div>
      )}

      {phase === "error" && jobError && (
        <div className="mt-3 rounded-xl border border-red-200 bg-red-50 p-4" role="alert">
          <p className="font-semibold text-red-600">Could not load this video.</p>
          <p className="mt-1 text-sm text-gray-600">{jobError}</p>
          <div className="mt-3 flex gap-3">
            <button
              type="button"
              className="rounded-xl bg-violet-600 px-4 py-1.5 text-sm font-medium text-white transition hover:bg-violet-700"
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
              className="rounded-xl border border-gray-200 bg-white px-4 py-1.5 text-sm text-gray-600 transition hover:bg-gray-50"
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
