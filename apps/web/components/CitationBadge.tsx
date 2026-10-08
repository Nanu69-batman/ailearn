"use client";

import type { Citation } from "../lib/api";

interface CitationBadgeProps {
  citation: Citation;
  onSeek: (startMs: number) => void;
}

function formatRange(startMs: number, endMs: number): string {
  const fmt = (ms: number): string => {
    const s = Math.floor(ms / 1000);
    const m = Math.floor(s / 60);
    const r = s % 60;
    return `${m}:${r.toString().padStart(2, "0")}`;
  };
  return `${fmt(startMs)}–${fmt(endMs)}`;
}

export default function CitationBadge({ citation, onSeek }: CitationBadgeProps) {
  // Video badge only when we have a real timestamp range; otherwise fall
  // back to a doc-style badge so we never seek to NaN (backend start_ms /
  // end_ms are nullable — see apps/api/app/chat/schemas.py Citation).
  const hasRange =
    citation.start_ms != null &&
    citation.end_ms != null &&
    Number.isFinite(citation.start_ms) &&
    Number.isFinite(citation.end_ms);
  const isVideoSource =
    citation.source_type === "video" ||
    citation.source_type === "video_transcript";
  if (isVideoSource && hasRange) {
    const startMs = citation.start_ms as number;
    const endMs = citation.end_ms as number;
    return (
      <button
        type="button"
        className="rounded-full border border-cyan-200 bg-cyan-50 px-2.5 py-0.5 text-xs font-medium text-cyan-700 transition hover:bg-cyan-100"
        onClick={() => onSeek(startMs)}
        title={`Seek to ${formatRange(startMs, endMs)}`}
        aria-label={`Video citation, seek to ${formatRange(startMs, endMs)}`}
      >
        ▶ {formatRange(startMs, endMs)}
      </button>
    );
  }
  if (isVideoSource) {
    return (
      <span className="rounded-full border border-gray-200 bg-gray-50 px-2.5 py-0.5 text-xs text-gray-500" title={citation.chunk_id}>
        Video ref
      </span>
    );
  }
  if (citation.source_type === "pdf") {
    return (
      <span className="rounded-full border border-gray-200 bg-gray-50 px-2.5 py-0.5 text-xs text-gray-500" title={`PDF page ${citation.page_no ?? "?"}`}>
        PDF p.{citation.page_no ?? "?"}
      </span>
    );
  }
  if (citation.source_type === "pptx") {
    return (
      <span className="rounded-full border border-gray-200 bg-gray-50 px-2.5 py-0.5 text-xs text-gray-500" title={`Slide ${citation.slide_no ?? "?"}`}>
        Slide {citation.slide_no ?? "?"}
      </span>
    );
  }
  return (
    <span className="rounded-full border border-gray-200 bg-gray-50 px-2.5 py-0.5 text-xs text-gray-500" title={citation.chunk_id}>
      {citation.source_type}
    </span>
  );
}
