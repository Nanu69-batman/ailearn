"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useParams } from "next/navigation";
import VideoPlayer from "@/components/VideoPlayer";
import YouTubePlayer, {
  type YouTubePlayerHandle,
} from "@/components/YouTubePlayer";
import YouTubeIngestForm, {
  type IngestedVideo,
} from "@/components/YouTubeIngestForm";
import ChatPanel from "@/components/ChatPanel";
import SessionBootstrap from "@/components/SessionBootstrap";
import {
  formatApiError,
  getCourseResources,
  isApiConfigured,
  resourceMediaUrl,
  type CourseResource,
  type StoredSession,
} from "@/lib/api";

const FALLBACK_VIDEO =
  "https://interactive-examples.mdn.mozilla.net/media/cc0-videos/flower.mp4";

type Selected =
  | { kind: "file"; resourceId: string; src: string; title: string }
  | { kind: "pdf"; resourceId: string; title: string }
  | { kind: "youtube"; resourceId: string; youtubeId: string; title: string };

function toSelected(resource: CourseResource): Selected | null {
  const title = resource.title ?? resource.id;
  if (resource.provider === "youtube") {
    if (!resource.youtube_id) return null;
    return {
      kind: "youtube",
      resourceId: resource.id,
      youtubeId: resource.youtube_id,
      title,
    };
  }
  if (resource.type === "pdf") {
    return { kind: "pdf", resourceId: resource.id, title };
  }
  // Only an asset that still exists on disk gets a real stream URL. Without
  // this check we would point the player at a 404 and the browser would show
  // the last playable source (i.e. the wrong video).
  if (!resource.asset_name) return null;
  return {
    kind: "file",
    resourceId: resource.id,
    src: resourceMediaUrl(resource.id),
    title,
  };
}

const NAV_ITEMS = [
  { label: "Dashboard", icon: "M3 12l9-9 9 9M5 10v10h5v-6h4v6h5V10" },
  { label: "My Courses", icon: "M4 19.5A2.5 2.5 0 016.5 17H20V4H6.5A2.5 2.5 0 004 6.5v13zM4 19.5A2.5 2.5 0 006.5 22H20v-5" },
  { label: "AI Tutor", icon: "M12 2a7 7 0 00-7 7c0 2.4 1.2 4.5 3 5.7V17a2 2 0 002 2h4a2 2 0 002-2v-2.3c1.8-1.2 3-3.3 3-5.7a7 7 0 00-7-7zM9 22h6", active: true },
  { label: "Practice", icon: "M9 11l3 3L22 4M21 12v7a2 2 0 01-2 2H5a2 2 0 01-2-2V5a2 2 0 012-2h11" },
  { label: "Progress", icon: "M3 3v18h18M7 14l4-4 4 4 5-6" },
  { label: "Resources", icon: "M4 4h16v16H4zM4 9h16M9 4v5" },
];

const SIDEBAR_BOTTOM = [
  { label: "Calendar", icon: "M8 2v4M16 2v4M3 9h18M5 4h14a2 2 0 012 2v14a2 2 0 01-2 2H5a2 2 0 01-2-2V6a2 2 0 012-2z" },
  { label: "Notifications", icon: "M18 8a6 6 0 10-12 0c0 7-3 9-3 9h18s-3-2-3-9M13.7 21a2 2 0 01-3.4 0" },
  { label: "Settings", icon: "M12 15a3 3 0 100-6 3 3 0 000 6zM19.4 15a1.65 1.65 0 00.33 1.82l.06.06a2 2 0 11-2.83 2.83l-.06-.06a1.65 1.65 0 00-1.82-.33 1.65 1.65 0 00-1 1.51V21a2 2 0 11-4 0v-.09a1.65 1.65 0 00-1-1.51 1.65 1.65 0 00-1.82.33l-.06.06a2 2 0 11-2.83-2.83l.06-.06a1.65 1.65 0 00.33-1.82 1.65 1.65 0 00-1.51-1H3a2 2 0 110-4h.09a1.65 1.65 0 001.51-1 1.65 1.65 0 00-.33-1.82l-.06-.06a2 2 0 112.83-2.83l.06.06a1.65 1.65 0 001.82.33h.01a1.65 1.65 0 001-1.51V3a2 2 0 114 0v.09a1.65 1.65 0 001 1.51h.01a1.65 1.65 0 001.82-.33l.06-.06a2 2 0 112.83 2.83l-.06.06a1.65 1.65 0 00-.33 1.82v.01a1.65 1.65 0 001.51 1H21a2 2 0 110 4h-.09a1.65 1.65 0 00-1.51 1z" },
];

function SideIcon({ d }: { d: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" className="h-5 w-5" aria-hidden="true">
      <path d={d} />
    </svg>
  );
}

const COURSE_TREE: Array<{ unit: string; items: Array<{ label: string; done?: boolean; current?: boolean }> }> = [
  {
    unit: "Unit 1 · Differential Calculus",
    items: [
      { label: "Limits & Continuity", done: true },
      { label: "Derivatives", done: true },
      { label: "Chain Rule", done: true },
    ],
  },
  {
    unit: "Unit 2 · Partial Derivatives",
    items: [
      { label: "Functions of Two Variables", done: true },
      { label: "Partial Derivatives", current: true },
      { label: "Gradient & Level Curves" },
    ],
  },
  {
    unit: "Unit 3 · Multiple Integrals",
    items: [
      { label: "Double Integrals" },
      { label: "Change of Variables" },
    ],
  },
  {
    unit: "Unit 4 · Vector Calculus",
    items: [
      { label: "Line Integrals" },
      { label: "Green's Theorem" },
    ],
  },
];

export default function LearnPage() {
  const params = useParams<{ courseId: string }>();
  const courseId = Array.isArray(params.courseId)
    ? params.courseId[0]
    : (params.courseId ?? "demo-course");
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const ytHandleRef = useRef<YouTubePlayerHandle | null>(null);
  const [session, setSession] = useState<StoredSession | null>(null);
  const [offline, setOffline] = useState(false);
  const [pausedAtMs, setPausedAtMs] = useState(0);
  const [selected, setSelected] = useState<Selected | null>(null);
  const [resources, setResources] = useState<CourseResource[]>([]);
  const [resourcesState, setResourcesState] = useState<
    "idle" | "loading" | "ready" | "error"
  >("idle");
  const [resourcesError, setResourcesError] = useState<string | null>(null);

  const fileSrc = process.env.NEXT_PUBLIC_VIDEO_URL || FALLBACK_VIDEO;
  const apiLive = isApiConfigured() && !offline;

  useEffect(() => {
    if (!session || !apiLive) return;
    let cancelled = false;
    async function load(): Promise<void> {
      setResourcesState("loading");
      setResourcesError(null);
      try {
        const res = await getCourseResources(courseId);
        if (!cancelled) {
          setResources(res.resources ?? []);
          setResourcesState("ready");
        }
      } catch (err) {
        if (!cancelled) {
          setResourcesError(formatApiError(err));
          setResourcesState("error");
        }
      }
    }
    void load();
    return () => {
      cancelled = true;
    };
  }, [session, apiLive, courseId]);

  const getCurrentTimeMs = useCallback((): number => {
    if (selected?.kind === "youtube") {
      return ytHandleRef.current?.getCurrentTimeMs() ?? pausedAtMs;
    }
    const el = videoRef.current;
    if (!el) return pausedAtMs;
    return Math.max(0, Math.floor(el.currentTime * 1000));
  }, [pausedAtMs, selected]);

  const handleSeek = useCallback(
    (startMs: number): void => {
      if (!Number.isFinite(startMs)) return;
      if (selected?.kind === "youtube") {
        ytHandleRef.current?.seekTo(startMs);
        return;
      }
      const el = videoRef.current;
      if (!el) return;
      el.currentTime = Math.max(0, startMs / 1000);
      el.focus();
    },
    [selected],
  );

  const handleReady = useCallback((s: StoredSession, isOffline: boolean): void => {
    setSession(s);
    setOffline(isOffline);
  }, []);

  const handleRejoin = useCallback((): void => {
    setSession(null);
    setOffline(false);
    setSelected(null);
  }, []);

  const handleIngested = useCallback((video: IngestedVideo): void => {
    setSelected({
      kind: "youtube",
      resourceId: video.resourceId,
      youtubeId: video.youtubeId,
      title: video.title ?? video.youtubeId,
    });
  }, []);

  return (
    <div className="min-h-screen bg-gray-50 text-gray-800">
      <div className="flex">
        {/* Left sidebar */}
        <aside className="hidden min-h-screen w-60 shrink-0 flex-col bg-[#1a1f3a] text-gray-300 md:flex">
          <div className="flex items-center gap-3 px-5 py-6">
            <span className="flex h-10 w-10 items-center justify-center rounded-xl bg-gradient-to-br from-indigo-500 to-violet-500 text-lg font-bold text-white">
              M
            </span>
            <div>
              <p className="text-sm font-bold leading-tight text-white">Malai Pioneer</p>
              <p className="text-[11px] text-gray-400">Learning</p>
            </div>
          </div>
          <nav className="flex flex-col gap-1 px-3" aria-label="Primary">
            {NAV_ITEMS.map((item) => (
              <a
                key={item.label}
                href="#"
                onClick={(e) => e.preventDefault()}
                className={
                  item.active
                    ? "flex items-center gap-3 rounded-xl bg-violet-600/90 px-3 py-2.5 text-sm font-medium text-white shadow"
                    : "flex items-center gap-3 rounded-xl px-3 py-2.5 text-sm text-gray-400 transition hover:bg-white/5 hover:text-white"
                }
              >
                <SideIcon d={item.icon} />
                {item.label}
              </a>
            ))}
          </nav>
          <div className="mx-5 my-4 border-t border-white/10" />
          <nav className="flex flex-col gap-1 px-3" aria-label="Secondary">
            {SIDEBAR_BOTTOM.map((item) => (
              <a
                key={item.label}
                href="#"
                onClick={(e) => e.preventDefault()}
                className="flex items-center gap-3 rounded-xl px-3 py-2.5 text-sm text-gray-400 transition hover:bg-white/5 hover:text-white"
              >
                <SideIcon d={item.icon} />
                {item.label}
              </a>
            ))}
          </nav>
        </aside>

        <div className="flex min-w-0 flex-1 flex-col">
          {/* Top bar */}
          <header className="sticky top-0 z-10 flex items-center justify-between gap-4 border-b border-gray-200 bg-white px-4 py-3 sm:px-6">
            <div className="flex items-center gap-3">
              <span className="flex h-9 w-9 items-center justify-center rounded-xl bg-gradient-to-br from-indigo-500 to-violet-500 text-base font-bold text-white md:hidden">
                M
              </span>
              <h1 className="text-lg font-bold text-gray-900">Malai Pioneer</h1>
            </div>
            <div className="hidden flex-1 justify-center sm:flex">
              <label className="sr-only" htmlFor="top-search">Search</label>
              <input
                id="top-search"
                type="search"
                placeholder="Search courses, topics, notes…"
                className="w-full max-w-md rounded-full border border-gray-200 bg-gray-100 px-4 py-2 text-sm text-gray-800 placeholder-gray-400 outline-none transition focus:border-violet-400 focus:bg-white focus:ring-2 focus:ring-violet-200"
              />
            </div>
            <div className="flex items-center gap-4">
              <button type="button" aria-label="Notifications" className="relative rounded-full p-2 text-gray-500 transition hover:bg-gray-100 hover:text-gray-800">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" className="h-5 w-5">
                  <path d="M18 8a6 6 0 10-12 0c0 7-3 9-3 9h18s-3-2-3-9M13.7 21a2 2 0 01-3.4 0" />
                </svg>
                <span className="absolute right-1.5 top-1.5 h-2 w-2 rounded-full bg-violet-500" />
              </button>
              <div className="flex items-center gap-2">
                <span className="flex h-9 w-9 items-center justify-center rounded-full bg-violet-100 text-sm font-bold text-violet-700">N</span>
                <span className="hidden text-sm font-medium text-gray-800 sm:inline">Nimish</span>
              </div>
            </div>
          </header>

          <main className="flex-1 px-4 py-6 sm:px-6">
            {!session ? (
              <SessionBootstrap courseId={courseId} onReady={handleReady} />
            ) : !selected ? (
              <div className="flex flex-col gap-6">
                <YouTubeIngestForm
                  courseId={courseId}
                  onReady={handleIngested}
                  disabled={!apiLive}
                />
                {!apiLive && (
                  <p className="text-sm text-amber-600" role="note">
                    YouTube ingest needs the live API — offline demo uses the file
                    video below.
                  </p>
                )}

                <section aria-label="Course videos">
                  <h2 className="mb-4 text-lg font-semibold text-gray-900">Course videos</h2>
                  {resourcesState === "loading" && (
                    <div className="rounded-xl border border-gray-200 bg-white p-4 text-sm text-gray-500 shadow-sm" role="status">
                      Loading course videos…
                    </div>
                  )}
                  {resourcesState === "error" && (
                    <div className="rounded-xl border border-red-200 bg-red-50 p-4 text-sm text-red-600 shadow-sm" role="alert">
                      Could not load course videos: {resourcesError}
                    </div>
                  )}
                  {(resourcesState === "ready" || resourcesState === "idle") &&
                    resources.length === 0 &&
                    resourcesState === "ready" && (
                      <div className="rounded-xl border border-gray-200 bg-white p-4 text-sm text-gray-500 shadow-sm" role="status">
                        No videos in this course yet — paste a YouTube link above to
                        add the first one.
                      </div>
                    )}
                  {resources.length > 0 && (
                    <ul className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
                      {resources.map((r) => {
                        const s = toSelected(r);
                        if (!s) return null;
                        const icon =
                          r.provider === "youtube"
                            ? "📺"
                            : r.type === "pdf"
                              ? "📄"
                              : "▶";
                        const statusStyle =
                          r.status === "done"
                            ? "bg-emerald-50 text-emerald-600 border-emerald-200"
                            : r.status === "failed"
                              ? "bg-red-50 text-red-600 border-red-200"
                              : r.status
                                ? "bg-amber-50 text-amber-600 border-amber-200"
                                : "";
                        return (
                          <li
                            key={r.id}
                            className="group flex flex-col gap-3 rounded-xl border border-gray-200 bg-white p-4 shadow-sm transition hover:border-violet-300 hover:shadow"
                          >
                            <div className="flex items-center gap-3">
                              <span className="flex h-11 w-11 items-center justify-center rounded-xl bg-violet-50 text-xl">
                                {icon}
                              </span>
                              <div className="min-w-0 flex-1">
                                <div className="truncate text-sm font-semibold text-gray-900">
                                  {s.title}
                                </div>
                                <div className="text-xs text-gray-400">
                                  {r.provider === "youtube" ? "YouTube" : "File"}
                                </div>
                              </div>
                            </div>
                            <div className="flex items-center justify-between">
                              {r.status ? (
                                <span
                                  className={`rounded-full border px-2.5 py-0.5 text-xs font-medium ${statusStyle}`}
                                >
                                  {r.status}
                                </span>
                              ) : (
                                <span />
                              )}
                              <button
                                type="button"
                                className="rounded-lg bg-violet-600 px-4 py-1.5 text-sm font-medium text-white transition hover:bg-violet-700"
                                onClick={() => setSelected(s)}
                              >
                                {r.type === "pdf" ? "Study" : "Watch"}
                              </button>
                            </div>
                          </li>
                        );
                      })}
                    </ul>
                  )}
                  <div className="mt-6 flex flex-col gap-3">
                    <button
                      type="button"
                      className="self-start rounded-xl border border-gray-200 bg-white px-4 py-2 text-sm text-gray-600 shadow-sm transition hover:bg-gray-50"
                      onClick={() =>
                        setSelected({
                          kind: "file",
                          resourceId: "demo-resource",
                          src: fileSrc,
                          title: "Demo file video (sample clip)",
                        })
                      }
                    >
                      ▶ Play sample clip (not course content)
                    </button>
                    {resources.some((r) => !toSelected(r)) && (
                      <p className="text-sm text-gray-400" role="note">
                        Some ingested files can no longer be played because their
                        original file is missing — re-ingest them from the course
                        folder to restore playback.
                      </p>
                    )}
                  </div>
                </section>
              </div>
            ) : (
              <div className="grid grid-cols-1 gap-6 lg:grid-cols-[250px_minmax(0,1fr)_230px]">
                {/* Course content tree */}
                <aside className="rounded-xl border border-gray-200 bg-white p-4 shadow-sm" aria-label="Course content">
                  <h2 className="mb-4 text-sm font-semibold uppercase tracking-wide text-gray-500">Course Content</h2>
                  <div className="flex flex-col gap-4">
                    {COURSE_TREE.map((group) => (
                      <div key={group.unit}>
                        <p className="text-xs font-semibold text-gray-900">{group.unit}</p>
                        <ul className="mt-1.5 flex flex-col gap-1">
                          {group.items.map((item) => (
                            <li key={item.label}>
                              <span
                                className={
                                  item.current
                                    ? "flex items-center gap-2 rounded-lg bg-violet-50 px-2 py-1.5 text-xs font-medium text-violet-700"
                                    : "flex items-center gap-2 rounded-lg px-2 py-1.5 text-xs text-gray-500"
                                }
                              >
                                {item.done ? (
                                  <span className="text-emerald-500" aria-label="Completed">✓</span>
                                ) : (
                                  <span className="h-3 w-3 rounded-full border border-gray-300" aria-hidden="true" />
                                )}
                                {item.label}
                              </span>
                            </li>
                          ))}
                        </ul>
                      </div>
                    ))}
                  </div>
                  <button
                    type="button"
                    className="mt-4 w-full rounded-lg border border-gray-200 px-3 py-1.5 text-xs text-gray-500 transition hover:bg-gray-50"
                    onClick={() => setSelected(null)}
                  >
                    ← All videos
                  </button>
                </aside>

                {/* Center: video + chat */}
                <div className="flex min-w-0 flex-col gap-6">
                  {selected.kind === "youtube" ? (
                    <YouTubePlayer
                      ref={ytHandleRef}
                      youtubeId={selected.youtubeId}
                      title={selected.title}
                      onPauseAt={setPausedAtMs}
                    />
                  ) : selected.kind === "pdf" ? (
                    <div className="rounded-xl border border-gray-200 bg-white p-6 shadow-sm" role="note">
                      <h2 className="mb-2 text-lg font-semibold text-gray-900">{selected.title}</h2>
                      <p className="text-sm text-gray-500">
                        This is a document resource — its questions are answered
                        through the chat panel with page citations. Ask a question
                        below.
                      </p>
                    </div>
                  ) : (
                    <VideoPlayer
                      src={selected.src}
                      videoRef={videoRef}
                      onPauseAt={setPausedAtMs}
                    />
                  )}
                  <ChatPanel
                    sessionId={session.session_id}
                    sessionToken={session.session_token}
                    videoId={selected.resourceId}
                    getCurrentTimeMs={getCurrentTimeMs}
                    onSeek={handleSeek}
                    offline={offline}
                    onRejoin={handleRejoin}
                  />
                </div>

                {/* Sources */}
                <aside className="flex flex-col gap-4" aria-label="Sources and topics">
                  <div className="rounded-xl border border-gray-200 bg-white p-4 shadow-sm">
                    <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-gray-500">Sources</h2>
                    <ul className="flex flex-col gap-2 text-sm">
                      <li className="flex items-center gap-2 text-gray-700"><span aria-hidden="true">📄</span> Lecture Notes</li>
                      <li className="flex items-center gap-2 text-gray-700"><span aria-hidden="true">📕</span> Textbook</li>
                      <li className="flex items-center gap-2 text-gray-700"><span aria-hidden="true">📝</span> Previous Year Questions</li>
                    </ul>
                  </div>
                  <div className="rounded-xl border border-gray-200 bg-white p-4 shadow-sm">
                    <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-gray-500">Related Topics</h2>
                    <div className="flex flex-wrap gap-1.5">
                      {["Chain Rule", "Gradients", "Directional Derivatives", "Taylor Series", "Hessian"].map((t) => (
                        <span key={t} className="rounded-full bg-violet-50 px-2.5 py-1 text-xs font-medium text-violet-700">
                          {t}
                        </span>
                      ))}
                    </div>
                  </div>
                  <div className="rounded-xl border border-violet-100 bg-gradient-to-br from-violet-50 to-indigo-50 p-4 shadow-sm">
                    <h2 className="mb-1 text-sm font-semibold text-violet-900">AI Insight</h2>
                    <p className="text-xs leading-relaxed text-violet-700">
                      You paused most often on the gradient definition — revisiting
                      that section could boost retention.
                    </p>
                  </div>
                </aside>
              </div>
            )}
          </main>
        </div>
      </div>
    </div>
  );
}
