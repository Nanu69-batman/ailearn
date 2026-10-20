/**
 * Shared, presentation-free shape for an ingest that is in flight.
 *
 * The ingest forms own the job (submit + poll); they report a snapshot of it
 * upward through `onProgress` so the subject page can keep a "Processing…"
 * card in the content list while the Add-content dialog is closed.
 */

import type { IngestJob } from "@/lib/api";

export type IngestSource = "youtube" | "file";

export interface IngestProgress {
  source: IngestSource;
  /** What is being ingested: the pasted URL or the file name. */
  label: string;
  /** YouTube id when known, so the pending card can show a thumbnail. */
  youtubeId?: string | null;
  phase: "starting" | "uploading" | "processing" | "error";
  /** 0–100 when the server reports real progress, otherwise null. */
  pct: number | null;
  /** Short human status line ("Transcribing · 1:20 elapsed"). */
  detail: string;
}

export function formatMs(ms: number): string {
  const s = Math.max(0, Math.floor(ms / 1000));
  return `${Math.floor(s / 60)}:${(s % 60).toString().padStart(2, "0")}`;
}

/** Progress from processed_through_ms/duration_ms, else windows, else null. */
export function jobProgress(
  j: IngestJob | null,
): { pct: number; text: string } | null {
  if (!j) return null;
  const win =
    j.windows_total && j.windows_total > 0
      ? `${j.windows_done ?? 0} of ${j.windows_total} windows, `
      : "";
  if (j.duration_ms && j.duration_ms > 0 && j.processed_through_ms !== undefined) {
    const pct = Math.min(
      100,
      Math.round((j.processed_through_ms / j.duration_ms) * 100),
    );
    return {
      pct,
      text: `${win}${pct}% (${formatMs(j.processed_through_ms)} of ${formatMs(j.duration_ms)})`,
    };
  }
  if (j.windows_total && j.windows_total > 0) {
    const pct = Math.min(
      100,
      Math.round(((j.windows_done ?? 0) / j.windows_total) * 100),
    );
    return { pct, text: `${win}${pct}%` };
  }
  return null;
}
