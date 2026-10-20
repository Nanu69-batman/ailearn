"use client";

import { useState, type ReactNode } from "react";
import Link from "next/link";
import type { CourseResource } from "@/lib/api";
import type { IngestProgress } from "./IngestProgress";

/** Public YouTube poster frame. No API key, no cookies. */
function youTubeThumb(youtubeId: string): string {
  return `https://i.ytimg.com/vi/${encodeURIComponent(youtubeId)}/hqdefault.jpg`;
}

type Kind = "youtube" | "video" | "pdf" | "slides" | "image" | "doc";

function kindOf(r: CourseResource): Kind {
  if (r.provider === "youtube") return "youtube";
  if (r.type === "video") return "video";
  if (r.type === "pdf") return "pdf";
  if (r.type === "pptx") return "slides";
  if (r.type === "image") return "image";
  return "doc";
}

const KIND_META: Record<Kind, { label: string; tint: string; action: string }> = {
  youtube: { label: "YouTube", tint: "from-red-50 to-rose-100 text-red-500", action: "Watch" },
  video: { label: "Video", tint: "from-violet-50 to-violet-100 text-violet-500", action: "Watch" },
  pdf: { label: "PDF", tint: "from-amber-50 to-orange-100 text-orange-500", action: "Study" },
  slides: { label: "Slides", tint: "from-sky-50 to-blue-100 text-blue-500", action: "Open" },
  image: { label: "Image", tint: "from-emerald-50 to-teal-100 text-teal-500", action: "View" },
  doc: { label: "Document", tint: "from-gray-50 to-gray-100 text-gray-500", action: "Open" },
};

function KindIcon({ kind, className }: { kind: Kind; className?: string }) {
  const common = {
    viewBox: "0 0 24 24",
    className,
    fill: "none",
    stroke: "currentColor",
    strokeWidth: 1.75,
    strokeLinecap: "round" as const,
    strokeLinejoin: "round" as const,
    "aria-hidden": true,
  };
  switch (kind) {
    case "youtube":
    case "video":
      return (
        <svg {...common}>
          <rect x="2.5" y="5" width="19" height="14" rx="3" />
          <path d="m10 9.5 5 2.5-5 2.5z" fill="currentColor" />
        </svg>
      );
    case "pdf":
      return (
        <svg {...common}>
          <path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z" />
          <path d="M14 3v5h5" />
          <path d="M9 13h6M9 17h4" />
        </svg>
      );
    case "slides":
      return (
        <svg {...common}>
          <rect x="3" y="4" width="18" height="12" rx="2" />
          <path d="M12 16v4M8 20h8" />
          <path d="M7 12l3-3 2 2 4-4" />
        </svg>
      );
    case "image":
      return (
        <svg {...common}>
          <rect x="3" y="4" width="18" height="16" rx="2" />
          <circle cx="9" cy="10" r="1.75" />
          <path d="m21 16-5-5-8 9" />
        </svg>
      );
    default:
      return (
        <svg {...common}>
          <path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z" />
          <path d="M14 3v5h5" />
        </svg>
      );
  }
}

function formatClock(ms: number): string {
  const s = Math.max(0, Math.floor(ms / 1000));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = String(s % 60).padStart(2, "0");
  return h > 0 ? `${h}:${String(m).padStart(2, "0")}:${sec}` : `${m}:${sec}`;
}

function StatusBadge({ r }: { r: CourseResource }) {
  if (!r.status) return null;
  if (r.status === "done")
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-emerald-50 px-2 py-0.5 text-[11px] font-medium text-emerald-700 ring-1 ring-inset ring-emerald-200">
        <span className="h-1.5 w-1.5 rounded-full bg-emerald-500" aria-hidden="true" />
        Ready
      </span>
    );
  if (r.status === "failed")
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-red-50 px-2 py-0.5 text-[11px] font-medium text-red-700 ring-1 ring-inset ring-red-200">
        Failed
      </span>
    );
  const text =
    r.status === "partial" && typeof r.processed_through_ms === "number"
      ? `Partial · up to ${formatClock(r.processed_through_ms)}`
      : r.status;
  return (
    <span className="inline-flex items-center gap-1 rounded-full bg-amber-50 px-2 py-0.5 text-[11px] font-medium capitalize text-amber-700 ring-1 ring-inset ring-amber-200">
      <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-amber-500" aria-hidden="true" />
      {text}
    </span>
  );
}

function Thumb({
  kind,
  youtubeId,
  children,
}: {
  kind: Kind;
  youtubeId?: string | null;
  children?: ReactNode;
}) {
  const [broken, setBroken] = useState(false);
  const meta = KIND_META[kind];
  return (
    <div className="relative aspect-video w-full overflow-hidden bg-gray-100">
      {youtubeId && !broken ? (
        // A plain <img>: remote poster frames, no next/image domain config.
        // eslint-disable-next-line @next/next/no-img-element
        <img
          src={youTubeThumb(youtubeId)}
          alt=""
          loading="lazy"
          onError={() => setBroken(true)}
          className="h-full w-full object-cover transition duration-300 group-hover:scale-[1.03]"
        />
      ) : (
        <div className={`flex h-full w-full items-center justify-center bg-gradient-to-br ${meta.tint}`}>
          <KindIcon kind={kind} className="h-12 w-12 opacity-80" />
        </div>
      )}
      {children}
    </div>
  );
}

interface ContentCardProps {
  resource: CourseResource;
  title: string;
  /** Null when the resource cannot be opened (e.g. its file is gone). */
  href: string | null;
  onDelete?: () => void;
  deleting?: boolean;
  highlight?: boolean;
}

export default function ContentCard({
  resource,
  title,
  href,
  onDelete,
  deleting,
  highlight,
}: ContentCardProps) {
  const kind = kindOf(resource);
  const meta = KIND_META[kind];

  const body = (
    <>
      <Thumb kind={kind} youtubeId={resource.youtube_id}>
        <span className="absolute left-2.5 top-2.5 inline-flex items-center gap-1 rounded-md bg-black/60 px-1.5 py-0.5 text-[11px] font-medium text-white backdrop-blur-sm">
          <KindIcon kind={kind} className="h-3 w-3" />
          {meta.label}
        </span>
        {href && (kind === "youtube" || kind === "video") && (
          <span className="absolute inset-0 flex items-center justify-center opacity-0 transition group-hover:opacity-100" aria-hidden="true">
            <span className="flex h-12 w-12 items-center justify-center rounded-full bg-white/90 text-violet-600 shadow-lg">
              <svg viewBox="0 0 24 24" className="ml-0.5 h-5 w-5" fill="currentColor">
                <path d="M8 5v14l11-7z" />
              </svg>
            </span>
          </span>
        )}
      </Thumb>
      <div className="flex flex-1 flex-col gap-2 p-4">
        <h3 className="line-clamp-2 text-sm font-semibold leading-snug text-gray-900 group-hover:text-violet-700">
          {title}
        </h3>
        <div className="mt-auto flex flex-wrap items-center gap-2">
          <StatusBadge r={resource} />
          {href ? (
            <span className="ml-auto text-xs font-medium text-violet-600">
              {meta.action} →
            </span>
          ) : (
            <span className="ml-auto text-xs text-gray-400">Unavailable</span>
          )}
        </div>
      </div>
    </>
  );

  return (
    <li
      className={`group relative flex flex-col overflow-hidden rounded-2xl border bg-white shadow-sm transition hover:-translate-y-0.5 hover:shadow-md ${
        highlight ? "border-violet-300 ring-2 ring-violet-200" : "border-gray-200"
      } ${deleting ? "pointer-events-none opacity-50" : ""}`}
    >
      {href ? (
        <Link
          href={href}
          className="flex flex-1 flex-col rounded-2xl focus:outline-none focus-visible:ring-2 focus-visible:ring-violet-400"
          aria-label={`${meta.action}: ${title}`}
        >
          {body}
        </Link>
      ) : (
        <div className="flex flex-1 flex-col" title="The original file is missing — re-ingest it to restore playback.">
          {body}
        </div>
      )}
      {onDelete && (
        <button
          type="button"
          onClick={onDelete}
          disabled={deleting}
          aria-label={`Delete ${title}`}
          className="absolute right-2.5 top-2.5 flex h-8 w-8 items-center justify-center rounded-lg bg-white/90 text-gray-500 shadow-sm ring-1 ring-black/5 backdrop-blur-sm transition hover:bg-red-50 hover:text-red-600 focus:opacity-100 focus:outline-none focus-visible:ring-2 focus-visible:ring-red-300 sm:opacity-0 sm:group-hover:opacity-100"
        >
          <svg viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <path d="M3 6h18" />
            <path d="M8 6V4h8v2" />
            <path d="M19 6l-1 14H6L5 6" />
          </svg>
        </button>
      )}
    </li>
  );
}

/** Placeholder card for an ingest that is still running (or just failed). */
export function ContentPendingCard({
  progress,
  onOpen,
}: {
  progress: IngestProgress;
  onOpen: () => void;
}) {
  const failed = progress.phase === "error";
  const kind: Kind = progress.source === "youtube" ? "youtube" : "doc";
  const heading =
    progress.phase === "uploading"
      ? "Uploading…"
      : progress.phase === "starting"
        ? "Starting…"
        : failed
          ? "Could not add this"
          : progress.pct != null
            ? `Processing… ${progress.pct}%`
            : "Processing…";
  return (
    <li
      className={`flex flex-col overflow-hidden rounded-2xl border bg-white shadow-sm ${
        failed ? "border-red-200" : "border-violet-200"
      }`}
      role="status"
      aria-live="polite"
    >
      <Thumb kind={kind} youtubeId={progress.youtubeId}>
        <div className={`absolute inset-0 flex items-center justify-center ${failed ? "bg-red-900/30" : "bg-violet-900/30"} backdrop-blur-[1px]`}>
          {failed ? (
            <span className="flex h-11 w-11 items-center justify-center rounded-full bg-white text-red-600 shadow" aria-hidden="true">
              <svg viewBox="0 0 24 24" className="h-5 w-5" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round">
                <path d="M12 8v5M12 16.5v.01" />
              </svg>
            </span>
          ) : (
            <span className="inline-block h-9 w-9 animate-spin rounded-full border-[3px] border-white border-t-transparent" aria-hidden="true" />
          )}
        </div>
      </Thumb>
      <div className="flex flex-1 flex-col gap-2 p-4">
        <p className={`text-sm font-semibold ${failed ? "text-red-700" : "text-violet-700"}`}>
          {heading}
        </p>
        <p className="truncate text-xs text-gray-500" title={progress.label}>
          {progress.label}
        </p>
        {!failed && (
          <div className="h-1.5 w-full overflow-hidden rounded-full bg-violet-100">
            {progress.pct != null ? (
              <div className="h-full rounded-full bg-violet-600 transition-all" style={{ width: `${progress.pct}%` }} />
            ) : (
              <div className="h-full w-1/3 animate-pulse rounded-full bg-violet-500" />
            )}
          </div>
        )}
        <p className="line-clamp-2 text-xs text-gray-400">{progress.detail}</p>
        <button
          type="button"
          onClick={onOpen}
          className="mt-auto self-start text-xs font-medium text-violet-600 hover:text-violet-800"
        >
          {failed ? "See details" : "View progress"}
        </button>
      </div>
    </li>
  );
}
