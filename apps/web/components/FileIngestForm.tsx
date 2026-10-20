"use client";

import { useEffect, useRef, useState } from "react";
import {
  formatIngestError,
  getIngestJob,
  ingestFile,
  type IngestJob,
  type IngestMode,
} from "../lib/api";
import { jobProgress, type IngestProgress } from "./IngestProgress";

export interface IngestedFile {
  resourceId: string;
  fileName: string;
  /** Server-declared page/image number; 1 for a single-image upload. */
  imageNo?: number | null;
}

interface FileIngestFormProps {
  courseId: string;
  onReady: (file: IngestedFile) => void;
  disabled?: boolean;
  /** Subject pages scope the ingest so the server auto-attaches it. */
  subjectId?: string;
  /** Drop the card chrome + heading (used inside the Add-content dialog). */
  embedded?: boolean;
  /** Snapshot of the in-flight job, or null when idle. Must be stable. */
  onProgress?: (progress: IngestProgress | null) => void;
}

/** `submitting` is split out from `polling`: the upload and the transcode
 *  are different waits and conflating them makes the wait look frozen. */
type Phase = "idle" | "uploading" | "polling" | "error";

const POLL_INTERVAL_MS = 2000;
/**
 * File ingest transcribes audio locally and can legitimately take many
 * minutes for a long recording, so this is deliberately much longer than the
 * YouTube form's 10 minutes. The timeout exists to stop an infinite spinner,
 * not to declare failure early.
 */
const POLL_TIMEOUT_MS = 30 * 60 * 1000;

const ACCEPT = "video/*,.pdf,.pptx,.png,.jpg,.jpeg,.webp";

/** What the backend's `source_type_for` accepts, mirrored for an early,
 *  free, offline-rejectable check. Not a security boundary. */
function kindOf(file: File): "video" | "pdf" | "pptx" | "image" | null {
  const type = file.type.toLowerCase();
  const name = file.name.toLowerCase();
  if (type.startsWith("video/") || /\.(mp4|mov|m4v|webm|mkv|avi)$/.test(name)) {
    return "video";
  }
  if (type === "application/pdf" || name.endsWith(".pdf")) return "pdf";
  if (
    type === "application/vnd.openxmlformats-officedocument.presentationml.presentation" ||
    name.endsWith(".pptx")
  ) {
    return "pptx";
  }
  if (
    ["image/png", "image/jpeg", "image/webp"].includes(type) ||
    /\.(png|jpe?g|webp)$/.test(name)
  ) {
    return "image";
  }
  return null;
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function formatElapsed(ms: number): string {
  const s = Math.floor(ms / 1000);
  const m = Math.floor(s / 60);
  return `${m}:${(s % 60).toString().padStart(2, "0")}`;
}

const PROCESSING_NOTES: Array<{ afterMs: number; text: string }> = [
  { afterMs: 0, text: "Queued on the server." },
  { afterMs: 8_000, text: "Uploading finished; extracting audio and text." },
  { afterMs: 45_000, text: "Transcribing speech — this is the slow part." },
  {
    afterMs: 120_000,
    text: "Chunking and indexing. Long recordings can take several minutes.",
  },
];

function processingNote(elapsedMs: number): string {
  let note = PROCESSING_NOTES[0].text;
  for (const entry of PROCESSING_NOTES) {
    if (elapsedMs >= entry.afterMs) note = entry.text;
  }
  return note;
}

export default function FileIngestForm({
  courseId,
  onReady,
  disabled,
  subjectId,
  embedded,
  onProgress,
}: FileIngestFormProps) {
  const [file, setFile] = useState<File | null>(null);
  const [phase, setPhase] = useState<Phase>("idle");
  const [clientError, setClientError] = useState<string | null>(null);
  const [jobError, setJobError] = useState<string | null>(null);
  const [jobStatus, setJobStatus] = useState<string | null>(null);
  const [elapsedMs, setElapsedMs] = useState(0);
  const [mode, setMode] = useState<IngestMode>("full");
  const [jobInfo, setJobInfo] = useState<IngestJob | null>(null);
  const [partialNote, setPartialNote] = useState(false);
  const cancelRef = useRef(false);
  const announcedRef = useRef(false);
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

  async function pollJob(
    jobId: string,
    fileName: string,
    startedAt: number,
  ): Promise<void> {
    setPhase("polling");
    const tick = async (): Promise<void> => {
      if (cancelRef.current) return;
      if (Date.now() - startedAt >= POLL_TIMEOUT_MS) {
        cancel(
          "Timed out after 30 minutes waiting for this file to be ready. " +
            "The ingest may still be running — try again later.",
        );
        return;
      }
      setElapsedMs(Date.now() - startedAt);
      try {
        const job = await getIngestJob(jobId);
        if (cancelRef.current) return;
        setJobStatus(job.status);
        setJobInfo(job);
        // Stream mode: the backend attaches the video to its subject after
        // the first window, so it is askable then. Announce it once and
        // keep polling so the progress bar keeps moving.
        if (
          job.mode === "stream" &&
          job.status !== "failed" &&
          (job.windows_done ?? 0) >= 1 &&
          !announcedRef.current
        ) {
          announcedRef.current = true;
          setPartialNote(true);
          onReady({
            resourceId: job.resource_id,
            fileName,
            imageNo: job.image_no ?? null,
          });
        }
        if (job.status === "done") {
          clearTimers();
          setPhase("idle");
          setPartialNote(job.mode === "stream");
          if (!announcedRef.current) {
            announcedRef.current = true;
            onReady({
              resourceId: job.resource_id,
              fileName,
              imageNo: job.image_no ?? null,
            });
          }
          return;
        }
        if (job.status === "failed") {
          // Honest failure: the server indexed nothing, so say so rather
          // than falling through to the success path.
          cancel(
            job.error?.trim()
              ? `Processing failed: ${job.error.trim()}`
              : "Processing failed on the server. Nothing was indexed.",
          );
        }
      } catch {
        // Transient poll blip — keep polling; the 30-minute timeout bounds
        // the wait so one failed poll never kills the job.
      }
    };
    await tick();
    if (cancelRef.current) return;
    const id = window.setInterval(() => void tick(), POLL_INTERVAL_MS);
    timersRef.current.push(id);
  }

  async function handleSubmit(e: React.FormEvent): Promise<void> {
    e.preventDefault();
    if (phase === "uploading" || phase === "polling" || disabled) return;
    if (!file) {
      setClientError("Choose a file first.");
      return;
    }
    const kind = kindOf(file);
    if (!kind) {
      setClientError(
        `“${file.name}” is not a supported file. Upload a video, PDF, PPTX, or an image (PNG, JPG, WEBP).`,
      );
      return;
    }
    setClientError(null);
    setJobError(null);
    setJobStatus(null);
    setJobInfo(null);
    setPartialNote(false);
    setElapsedMs(0);
    cancelRef.current = false;
    announcedRef.current = false;
    setPhase("uploading");
    try {
      const res = await ingestFile(
        courseId,
        file,
        subjectId,
        kind === "video" ? mode : undefined,
      );
      if (cancelRef.current) return;
      if (!res.job_id) {
        setPhase("error");
        setJobError("Server did not return a job id. Please try again.");
        return;
      }
      await pollJob(res.job_id, file.name, Date.now());
    } catch (err) {
      if (cancelRef.current) return;
      setPhase("error");
      setJobError(formatIngestError(err, "File"));
    }
  }

  const isVideo = file !== null && kindOf(file) === "video";
  const progress = jobProgress(jobInfo);
  const busy = phase === "uploading" || phase === "polling";
  const statusText =
    phase === "uploading"
      ? `Uploading ${file?.name ?? "file"} (${formatBytes(file?.size ?? 0)})…`
      : `${jobStatus ? `${jobStatus} · ` : ""}${formatElapsed(elapsedMs)} elapsed — ${processingNote(elapsedMs)}`;

  // Report upward so the subject page can show a pending card while the
  // dialog is closed. Derived from state, never a second source of truth.
  const fileName = file?.name ?? "File";
  useEffect(() => {
    if (!onProgress) return;
    if (phase === "idle") {
      onProgress(null);
      return;
    }
    onProgress({
      source: "file",
      label: fileName,
      phase:
        phase === "error"
          ? "error"
          : phase === "uploading"
            ? "uploading"
            : "processing",
      pct: progress?.pct ?? null,
      detail:
        phase === "error" ? (jobError ?? "Could not load this file.") : statusText,
    });
  }, [onProgress, phase, fileName, progress?.pct, statusText, jobError]);

  return (
    <div
      className={
        embedded ? "" : "rounded-2xl border border-gray-200 bg-white p-5 shadow-sm"
      }
    >
      {!embedded && (
        <>
          <h2 className="text-base font-semibold text-gray-900">
            Upload a file
          </h2>
          <p className="mt-1 text-sm text-gray-500">
            Drop in a video, PDF, PPTX, or an image — we extract the text and
            make it askable.
          </p>
        </>
      )}
      <form
        className={`${embedded ? "" : "mt-4 "}flex flex-col gap-3`}
        onSubmit={(e) => void handleSubmit(e)}
      >
        <label
          htmlFor="file-upload"
          className={`flex flex-col items-center justify-center gap-2 rounded-2xl border-2 border-dashed px-4 py-6 text-center transition focus-within:border-violet-400 focus-within:ring-2 focus-within:ring-violet-200 ${
            busy || disabled
              ? "cursor-not-allowed border-gray-200 bg-gray-50 opacity-60"
              : file
                ? "cursor-pointer border-violet-300 bg-violet-50/50"
                : "cursor-pointer border-gray-200 bg-gray-50 hover:border-violet-300 hover:bg-violet-50/40"
          }`}
        >
          <span className="flex h-10 w-10 items-center justify-center rounded-xl bg-white text-violet-600 shadow-sm" aria-hidden="true">
            <svg viewBox="0 0 24 24" className="h-5 w-5" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M12 16V4" />
              <path d="m7 9 5-5 5 5" />
              <path d="M20 16v3a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1v-3" />
            </svg>
          </span>
          {file ? (
            <span className="text-sm">
              <span className="font-medium text-gray-900">{file.name}</span>
              <span className="text-gray-500"> · {formatBytes(file.size)}</span>
            </span>
          ) : (
            <span className="text-sm text-gray-600">
              <span className="font-medium text-violet-700">Choose a file</span>{" "}
              to upload
            </span>
          )}
          <span className="text-xs text-gray-400">
            Video, PDF, PPTX, PNG, JPG or WEBP
          </span>
          <span className="sr-only">Course file</span>
          <input
            id="file-upload"
            className="sr-only"
            type="file"
            accept={ACCEPT}
            disabled={busy || disabled}
            aria-invalid={clientError ? true : undefined}
            aria-describedby={clientError ? "file-upload-error" : undefined}
            onChange={(e) => {
              setFile(e.target.files?.[0] ?? null);
              if (clientError) setClientError(null);
            }}
          />
        </label>

        {isVideo && (
          <fieldset className="flex flex-col gap-1.5 rounded-xl border border-gray-100 bg-gray-50 p-3 text-sm text-gray-700" disabled={busy || disabled}>
            <legend className="sr-only">Video processing</legend>
            <span className="text-xs font-medium text-gray-500" aria-hidden="true">Video processing</span>
            <label className="flex items-center gap-2">
              <input type="radio" name="ingest-mode" className="accent-violet-600" checked={mode === "full"} onChange={() => setMode("full")} />
              Process everything now
            </label>
            <label className="flex items-center gap-2">
              <input type="radio" name="ingest-mode" className="accent-violet-600" checked={mode === "stream"} onChange={() => setMode("stream")} />
              Stream: first ~6 minutes now, the rest as you watch
            </label>
          </fieldset>
        )}

        <button
          type="submit"
          className="self-end rounded-xl bg-violet-600 px-5 py-2.5 text-sm font-medium text-white shadow-sm transition hover:bg-violet-700 disabled:opacity-40"
          disabled={busy || disabled || file === null}
        >
          {phase === "uploading"
            ? "Uploading…"
            : phase === "polling"
              ? "Processing…"
              : "Upload file"}
        </button>
      </form>

      {clientError && (
        <div id="file-upload-error" className="mt-3 rounded-xl border border-red-200 bg-red-50 p-3 text-sm text-red-600" role="alert">
          {clientError}
        </div>
      )}

      {busy && (
        <div className="mt-3 rounded-xl border border-violet-100 bg-violet-50/60 p-3 text-sm text-gray-600" role="status">
          <div className="flex items-center gap-3">
            <span className="inline-block h-4 w-4 shrink-0 animate-spin rounded-full border-2 border-violet-400 border-t-transparent" aria-hidden="true" />
            <span className="min-w-0 flex-1">
              {phase === "polling" && (
                <span className="font-medium text-gray-800">Processing {file?.name} · </span>
              )}
              {statusText}
            </span>
            <button
              type="button"
              className="rounded-lg border border-gray-200 bg-white px-3 py-1 text-xs text-gray-500 hover:bg-gray-50"
              onClick={() => cancel()}
            >
              Cancel
            </button>
          </div>
          {phase === "polling" && progress && (
            <div className="mt-2 flex items-center gap-2 text-xs text-gray-500">
              <div
                className="h-1.5 flex-1 overflow-hidden rounded-full bg-violet-100"
                role="progressbar"
                aria-valuenow={progress.pct}
                aria-valuemin={0}
                aria-valuemax={100}
              >
                <div className="h-full rounded-full bg-violet-600 transition-all" style={{ width: `${progress.pct}%` }} />
              </div>
              {progress.text}
            </div>
          )}
        </div>
      )}

      {partialNote && phase === "idle" && (
        <p className="mt-3 text-sm text-gray-600" role="status">
          First part ready. The rest processes as you watch.
        </p>
      )}

      {phase === "error" && jobError && (
        <div className="mt-3 rounded-xl border border-red-200 bg-red-50 p-4" role="alert">
          <p className="font-semibold text-red-600">Could not load this file.</p>
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
