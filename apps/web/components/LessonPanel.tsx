"use client";

import { useEffect, useState, type ReactNode } from "react";
import type { ResourceChapter } from "@/lib/api";
import type { ChaptersState } from "./useChapters";

export type LessonTab = "chapters" | "transcript" | "resources";

const TAB_LABEL: Record<LessonTab, string> = {
  chapters: "Chapters",
  transcript: "Transcript",
  resources: "Resources",
};

function TabIcon({ tab, className = "h-4 w-4" }: { tab: LessonTab; className?: string }) {
  const common = {
    viewBox: "0 0 20 20",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: 1.7,
    strokeLinecap: "round" as const,
    strokeLinejoin: "round" as const,
    className,
    "aria-hidden": true,
  };
  if (tab === "chapters") {
    return (
      <svg {...common}>
        <path d="M7.5 5h9M7.5 10h9M7.5 15h9" />
        <circle cx="3.75" cy="5" r=".9" fill="currentColor" />
        <circle cx="3.75" cy="10" r=".9" fill="currentColor" />
        <circle cx="3.75" cy="15" r=".9" fill="currentColor" />
      </svg>
    );
  }
  if (tab === "transcript") {
    return (
      <svg {...common}>
        <rect x="3.5" y="2.75" width="13" height="14.5" rx="2" />
        <path d="M6.5 7h7M6.5 10h7M6.5 13h4" />
      </svg>
    );
  }
  return (
    <svg {...common}>
      <path d="M2.75 6.25a1.5 1.5 0 0 1 1.5-1.5h3.5l1.75 2h6.25a1.5 1.5 0 0 1 1.5 1.5v6.5a1.5 1.5 0 0 1-1.5 1.5H4.25a1.5 1.5 0 0 1-1.5-1.5Z" />
    </svg>
  );
}

function Chevron({ dir, className = "h-4 w-4" }: { dir: "up" | "down" | "left" | "right"; className?: string }) {
  const d = { up: "M5 12.5 10 7.5l5 5", down: "M5 7.5l5 5 5-5", left: "M12.5 5 7.5 10l5 5", right: "M7.5 5l5 5-5 5" }[dir];
  return (
    <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className={className} aria-hidden="true">
      <path d={d} />
    </svg>
  );
}

function clock(ms: number): string {
  const s = Math.max(0, Math.floor(ms / 1000));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const ss = (s % 60).toString().padStart(2, "0");
  return h > 0 ? `${h}:${m.toString().padStart(2, "0")}:${ss}` : `${m}:${ss}`;
}

function EmptyNote({ tab, title, children }: { tab: LessonTab; title: string; children: ReactNode }) {
  return (
    <div className="flex flex-col items-center gap-2 px-6 py-10 text-center">
      <span className="flex h-10 w-10 items-center justify-center rounded-xl bg-violet-50 text-violet-500">
        <TabIcon tab={tab} className="h-5 w-5" />
      </span>
      <p className="text-sm font-semibold text-gray-800">{title}</p>
      <p className="max-w-xs text-xs leading-relaxed text-gray-500">{children}</p>
    </div>
  );
}

function ChapterNote({ state, isMedia }: { state: ChaptersState; isMedia: boolean }) {
  const what = isMedia ? "video" : "document";
  if (state === "loading" || state === "idle") {
    return (
      <div role="status" className="flex items-center justify-center gap-2 px-6 py-10 text-sm text-gray-500">
        <span className="h-4 w-4 animate-spin rounded-full border-2 border-violet-400 border-t-transparent" aria-hidden="true" />
        Loading chapters…
      </div>
    );
  }
  if (state === "pending") {
    return (
      <EmptyNote tab="chapters" title="Writing chapters…">
        Malai is splitting this {what} into chapters. They&apos;ll appear here in a minute.
      </EmptyNote>
    );
  }
  if (state === "error") {
    return (
      <EmptyNote tab="chapters" title="Couldn't load chapters">
        The server didn&apos;t answer. Reopen this {what} to try again.
      </EmptyNote>
    );
  }
  return (
    <EmptyNote tab="chapters" title="No chapters yet">
      This {what} hasn&apos;t been processed yet. Once it is, its chapters appear here
      {isMedia ? " and you can jump between them" : ""}.
    </EmptyNote>
  );
}

function ChapterList({
  chapters,
  state,
  isMedia,
  playheadMs,
  onSeek,
  onOpenPage,
}: {
  chapters: ResourceChapter[];
  state: ChaptersState;
  isMedia: boolean;
  playheadMs: number;
  onSeek: (ms: number) => void;
  onOpenPage?: (page: number) => void;
}) {
  const timed = chapters.filter((c) => c.start_ms != null && c.end_ms != null);
  const paged = chapters.filter((c) => c.page_start != null || c.slide_start != null);
  if (isMedia ? timed.length === 0 : paged.length === 0) return <ChapterNote state={state} isMedia={isMedia} />;

  const row = "flex w-full items-center gap-3 rounded-xl px-3 py-2.5 text-left text-sm transition";
  if (!isMedia) {
    return (
      <ol className="flex flex-col gap-0.5 p-2">
        {paged.map((c, i) => {
          const slides = c.page_start == null;
          const from = (slides ? c.slide_start : c.page_start) as number;
          const to = (slides ? c.slide_end : c.page_end) ?? from;
          const span = to > from ? `${from}–${to}` : `${from}`;
          const where = slides ? `Slide${to > from ? "s" : ""} ${span}` : `${to > from ? "pp." : "p."} ${span}`;
          const body = (
            <>
              <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md bg-gray-100 text-[11px] font-semibold text-gray-500">
                {i + 1}
              </span>
              <span className="min-w-0 flex-1 truncate font-medium">{c.title}</span>
              <span className="shrink-0 tabular-nums text-xs text-gray-400">{where}</span>
            </>
          );
          return (
            <li key={`${from}-${c.title}`}>
              {onOpenPage && !slides ? (
                <button
                  type="button"
                  onClick={() => onOpenPage(from)}
                  title={`Go to page ${from}`}
                  className={`${row} text-gray-700 hover:bg-gray-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-violet-300`}
                >
                  {body}
                </button>
              ) : (
                <div className={`${row} text-gray-700`}>{body}</div>
              )}
            </li>
          );
        })}
      </ol>
    );
  }

  return (
    <ol className="flex flex-col gap-0.5 p-2">
      {timed.map((c) => {
        const startMs = c.start_ms as number;
        const endMs = c.end_ms as number;
        const current = playheadMs >= startMs && playheadMs < endMs;
        const watched = playheadMs >= endMs;
        return (
          <li key={`${startMs}-${c.title}`}>
            <button
              type="button"
              onClick={() => onSeek(startMs)}
              aria-current={current ? "true" : undefined}
              className={`${row} focus:outline-none focus-visible:ring-2 focus-visible:ring-violet-300 ${
                current ? "bg-violet-50 text-violet-900" : "text-gray-700 hover:bg-gray-50"
              }`}
            >
              <span
                className={`flex h-4 w-4 shrink-0 items-center justify-center rounded ${
                  current
                    ? "bg-violet-600 text-white"
                    : watched
                      ? "bg-violet-100 text-violet-600"
                      : "border border-gray-300"
                }`}
                aria-hidden="true"
              >
                {current ? (
                  <svg viewBox="0 0 10 10" fill="currentColor" className="h-2 w-2">
                    <path d="M2.5 1.5v7l6-3.5z" />
                  </svg>
                ) : watched ? (
                  <svg viewBox="0 0 10 10" fill="none" stroke="currentColor" strokeWidth="1.8" className="h-2.5 w-2.5">
                    <path d="m2 5.2 2 2 4-4.4" />
                  </svg>
                ) : null}
              </span>
              <span className="w-12 shrink-0 tabular-nums text-xs text-gray-500">{clock(startMs)}</span>
              <span className="min-w-0 flex-1 truncate font-medium" title={c.title}>{c.title}</span>
              <span className="shrink-0 tabular-nums text-xs text-gray-400">{clock(endMs - startMs)}</span>
            </button>
          </li>
        );
      })}
      {state === "pending" && (
        <li className="px-3 py-2 text-xs text-gray-400" role="status">Updating chapters as more of the video is processed…</li>
      )}
    </ol>
  );
}

interface LessonPanelProps {
  /** Under the player (side-by-side layout) or beside it (stacked layout). */
  placement: "below" | "side";
  /** A video gets Chapters and Transcript; a document Chapters and Resources. */
  isMedia: boolean;
  chapters: ResourceChapter[];
  chaptersState: ChaptersState;
  playheadMs: number;
  onSeek: (ms: number) => void;
  /** A PDF: open this page in the viewer. Slides cannot be jumped to. */
  onOpenPage?: (page: number) => void;
  /** The subject's materials list. */
  resources: ReactNode;
  collapsed: boolean;
  onCollapsedChange: (collapsed: boolean) => void;
}

/**
 * The tabbed panel that goes with the lesson: chapters, transcript and the
 * subject's resources. It collapses to its tab bar (under the player) or to a
 * thin rail of icons (beside it), giving the space back to the video.
 */
export default function LessonPanel({
  placement,
  isMedia,
  chapters,
  chaptersState,
  playheadMs,
  onSeek,
  onOpenPage,
  resources,
  collapsed,
  onCollapsedChange,
}: LessonPanelProps) {
  const tabs: LessonTab[] = isMedia ? ["chapters", "transcript", "resources"] : ["chapters", "resources"];
  const [tab, setTab] = useState<LessonTab>(tabs[0]);
  // A document has no transcript tab to stay on.
  useEffect(() => {
    if (!tabs.includes(tab)) setTab(tabs[0]);
  }, [isMedia]); // eslint-disable-line react-hooks/exhaustive-deps

  const side = placement === "side";

  if (side && collapsed) {
    return (
      <nav
        aria-label="Lesson panel (collapsed)"
        className="flex w-12 shrink-0 flex-col items-center gap-1 rounded-2xl border border-gray-200 bg-white py-2 shadow-sm"
      >
        <button
          type="button"
          onClick={() => onCollapsedChange(false)}
          aria-label="Expand the panel"
          title="Expand"
          className="mb-1 flex h-9 w-9 items-center justify-center rounded-lg text-gray-500 transition hover:bg-gray-100 hover:text-gray-800 focus:outline-none focus-visible:ring-2 focus-visible:ring-violet-400"
        >
          <Chevron dir="left" />
        </button>
        {tabs.map((t) => (
          <button
            key={t}
            type="button"
            onClick={() => {
              setTab(t);
              onCollapsedChange(false);
            }}
            aria-label={TAB_LABEL[t]}
            title={TAB_LABEL[t]}
            className={`flex h-9 w-9 items-center justify-center rounded-lg transition focus:outline-none focus-visible:ring-2 focus-visible:ring-violet-400 ${
              tab === t ? "bg-violet-50 text-violet-700" : "text-gray-500 hover:bg-gray-100 hover:text-gray-800"
            }`}
          >
            <TabIcon tab={t} />
          </button>
        ))}
      </nav>
    );
  }

  return (
    <section
      aria-label="Lesson panel"
      className={`flex min-w-0 flex-col rounded-2xl border border-gray-200 bg-white shadow-sm ${
        side ? "w-full shrink-0 lg:h-full lg:w-[clamp(260px,34%,400px)]" : ""
      }`}
    >
      <div className="flex items-center gap-1 border-b border-gray-100 p-1.5">
        <div className="flex min-w-0 flex-1 gap-1 overflow-x-auto" role="tablist" aria-label="Lesson panel">
          {tabs.map((t) => (
            <button
              key={t}
              type="button"
              role="tab"
              aria-selected={tab === t}
              onClick={() => {
                setTab(t);
                if (collapsed) onCollapsedChange(false);
              }}
              className={`flex shrink-0 items-center gap-2 rounded-xl px-3 py-2 text-sm font-medium transition focus:outline-none focus-visible:ring-2 focus-visible:ring-violet-400 ${
                tab === t && !collapsed
                  ? "bg-violet-50 text-violet-700 shadow-[inset_0_-2px_0] shadow-violet-500"
                  : "text-gray-500 hover:bg-gray-50 hover:text-gray-800"
              }`}
            >
              <TabIcon tab={t} className="hidden h-4 w-4 sm:block" />
              {TAB_LABEL[t]}
            </button>
          ))}
        </div>
        <button
          type="button"
          onClick={() => onCollapsedChange(!collapsed)}
          aria-expanded={!collapsed}
          aria-label={collapsed ? "Expand the panel" : "Collapse the panel"}
          title={collapsed ? "Expand" : "Collapse"}
          className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg text-gray-400 transition hover:bg-gray-100 hover:text-gray-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-violet-400"
        >
          <Chevron dir={side ? "right" : collapsed ? "down" : "up"} />
        </button>
      </div>

      {!collapsed && (
        <div className={`min-h-0 ${side ? "flex-1 overflow-y-auto" : ""}`} role="tabpanel" aria-label={TAB_LABEL[tab]}>
          {tab === "chapters" && (
            <ChapterList
              chapters={chapters}
              state={chaptersState}
              isMedia={isMedia}
              playheadMs={playheadMs}
              onSeek={onSeek}
              onOpenPage={onOpenPage}
            />
          )}
          {tab === "transcript" && (
            <EmptyNote tab="transcript" title="Transcript view is on the way">
              Malai already reads the transcript to answer your questions. A
              readable, clickable copy will appear here.
            </EmptyNote>
          )}
          {tab === "resources" && <div className="p-3">{resources}</div>}
        </div>
      )}
    </section>
  );
}
