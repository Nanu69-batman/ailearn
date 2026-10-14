"use client";

import type { Citation } from "../lib/api";

interface CitationBadgeProps {
  citation: Citation;
  onSeek: (startMs: number) => void;
}

/**
 * Short, quiet label for how the evidence was produced.
 *
 * `source_type: "video"` covers BOTH spoken audio and sampled frames, so this
 * is the only thing that tells the two apart. Deliberately terse: it is
 * supporting information on a chip, not a label. Unknown/absent modality
 * returns null and the caller renders exactly what it always has.
 */
function modalityHint(modality: Citation["modality"]): string | null {
  if (modality === "visual") return "frame";
  if (modality === "transcript") return "audio";
  return null;
}

function formatStamp(ms: number): string {
  const s = Math.floor(ms / 1000);
  const m = Math.floor(s / 60);
  const r = s % 60;
  return `${m}:${r.toString().padStart(2, "0")}`;
}

function formatRange(startMs: number, endMs: number): string {
  return `${formatStamp(startMs)}–${formatStamp(endMs)}`;
}

export default function CitationBadge({ citation, onSeek }: CitationBadgeProps) {
  // IMAGES FIRST, ALWAYS. An image citation carries no time range at all —
  // the backend sets start_ms/end_ms/page_no/slide_no/video_id to null and
  // marks modality "visual". Reaching the time formatter first would print
  // "0:00–0:00" (or a literal null) on every image citation, so this branch
  // must stay above every other one.
  if (citation.image_no != null) {
    const label = `Image ${citation.image_no}`;
    return (
      <span
        className="rounded-full border border-gray-200 bg-gray-50 px-2.5 py-0.5 text-xs text-gray-500"
        title={`${label} · ${citation.chunk_id}`}
      >
        {label}
      </span>
    );
  }
  // LIVE NEXT, AND BEFORE ANY RANGE FORMATTING — and deliberately NOT inside
  // the `isVideoSource && hasRange` guard below. The live-frame endpoint
  // returns a citation with `source_type: null` (the backend Citation schema
  // defaults `live` to false but leaves every other field, source_type
  // included, nullable), so a live citation fails `isVideoSource` and walks
  // straight past the live chip to the empty grey fallback. `live` is the one
  // field that must never be gated on anything else.
  //
  // A live capture is an INSTANT at T, so `formatRange` would print
  // "8:04–8:04" — visually identical to a one-second sampled frame and,
  // worse, indistinguishable from an ordinary span. The chip drops the range
  // entirely and says so in words. Every non-live citation below is untouched.
  //
  // Only a citation that actually carries a finite timestamp takes this
  // branch; a malformed live citation still falls through to the existing
  // paths rather than rendering "NaN".
  const liveAt = citation.live
    ? (citation.start_ms ?? citation.end_ms)
    : null;
  if (liveAt != null && Number.isFinite(liveAt)) {
    const stamp = formatStamp(liveAt);
    return (
      <button
        type="button"
        className="rounded-full border border-violet-300 bg-violet-50 px-2.5 py-0.5 text-xs font-medium text-violet-700 transition hover:bg-violet-100"
        onClick={() => onSeek(liveAt)}
        title={`This exact frame, captured from your screen at ${stamp} · ${citation.chunk_id}`}
        aria-label={`Live frame citation at ${stamp}, seek to ${stamp}`}
      >
        ▶ {stamp}
        <span className="ml-1.5 font-normal text-violet-600/80">this frame</span>
      </button>
    );
  }
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
  // Absent/unknown modality => null => chip renders byte-identically to before.
  const hint = modalityHint(citation.modality);
  if (isVideoSource && hasRange) {
    const startMs = citation.start_ms as number;
    const endMs = citation.end_ms as number;
    // Live citations were already handled above — a citation reaching this
    // point has `live` falsy, so the range below is always a real span.
    const range = formatRange(startMs, endMs);
    const spoken = hint === "frame" ? "Video frame" : "Audio";
    return (
      <button
        type="button"
        className="rounded-full border border-cyan-200 bg-cyan-50 px-2.5 py-0.5 text-xs font-medium text-cyan-700 transition hover:bg-cyan-100"
        onClick={() => onSeek(startMs)}
        title={`Seek to ${range}${hint ? ` · from ${hint === "frame" ? "a sampled frame" : "spoken audio"}` : ""}`}
        aria-label={`${hint ? `${spoken} citation, ` : "Video citation, "}seek to ${range}`}
      >
        ▶ {range}
        {/* Quiet, low-contrast qualifier: legible, never competing with the timestamp. */}
        {hint && (
          <span className="ml-1.5 font-normal text-cyan-600/70">{hint}</span>
        )}
      </button>
    );
  }
  if (isVideoSource) {
    return (
      <span className="rounded-full border border-gray-200 bg-gray-50 px-2.5 py-0.5 text-xs text-gray-500" title={citation.chunk_id}>
        Video ref
        {hint && <span className="ml-1.5 text-gray-400">{hint}</span>}
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
