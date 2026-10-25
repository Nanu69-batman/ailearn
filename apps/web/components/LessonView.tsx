"use client";

import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import Link from "next/link";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import VideoPlayer from "@/components/VideoPlayer";
import DocumentViewer from "@/components/DocumentViewer";
import YouTubePlayer, {
  type YouTubePlayerHandle,
} from "@/components/YouTubePlayer";
import ChatPanel from "@/components/ChatPanel";
import LessonPanel from "@/components/LessonPanel";
import LessonWorkspace from "@/components/LessonWorkspace";
import WindowToolbar from "@/components/WindowToolbar";
import { useLessonLayout } from "@/components/useLessonLayout";
import {
  CitedRangeContext,
  ImmersiveContext,
  type Immersive,
} from "@/components/PlayerControls";
import type { PdfTarget } from "@/components/PdfView";
import { useLearnSession } from "@/components/LearnSessionProvider";
import { useCourseResources } from "@/components/useCourseResources";
import { useStudyTracker } from "@/components/useStudyTracker";
import {
  isApiConfigured,
  getSubjectResources,
  reportPosition,
  type Citation,
  type LiveFrameCapture,
} from "@/lib/api";
import {
  SAMPLE_CLIP_ID,
  SUBJECTS_PATH,
  contentPath,
  firstParam,
  readCompletion,
  resourceIcon,
  resourceKindLabel,
  sampleClipSelected,
  subjectPath,
  toSelected,
  writeCompletion,
  type Selected,
} from "@/lib/learn";
import {
  MALAI_PATH,
  getChat,
  malaiLessonPath,
  malaiSubjectPath,
  recordLessonChat,
} from "@/lib/malai";

/**
 * The lesson page: video/PDF + chat + sources, one real URL per resource.
 * Served at /ai-tutor/subjects/<s>/content/<r> and, inside Malai (`malai`),
 * at /malai/lesson/<s>/<r>; the two differ only in where their links lead.
 * Either way, every chat asked here is filed under its subject in Malai.
 *
 * `resourceId` in the path is the authoritative selection — there is no local
 * "selected" state to drift out of sync with the address bar, which is what
 * makes the back button, a reload, and a shared link all behave the same.
 */
export default function LessonView({ malai = false }: { malai?: boolean }) {
  const params = useParams<{ subjectId?: string | string[]; resourceId?: string | string[] }>();
  const router = useRouter();
  const search = useSearchParams();
  /** Malai chat to reopen (?c=): its thread, if it lives in this session. */
  const openChatId = search.get("c");
  // The subject id is whatever the backend put in the URL. No literal default:
  // an unknown id must surface as "not available", never as a silent fallback.
  const subjectId = firstParam(params.subjectId);
  const resourceId = firstParam(params.resourceId) ?? "";

  const { session, offline, reset, subject } = useLearnSession();
  const apiLive = isApiConfigured() && !offline;
  const { resources, state: resourcesState, error: resourcesError } =
    useCourseResources({
      courseId: subjectId ?? "",
      enabled: Boolean(session) && apiLive && subjectId !== null,
      // The URL carries a subject id, not a course id — a subject page/player
      // must list its attached materials, not the (unrelated) course list.
      load: getSubjectResources,
    });

  const videoRef = useRef<HTMLVideoElement | null>(null);
  const ytHandleRef = useRef<YouTubePlayerHandle | null>(null);
  const [pausedAtMs, setPausedAtMs] = useState(0);
  /**
   * Display-only copy of the playhead, whole seconds, for the "Malai can see
   * up to" chip. It is NOT what the tutor is sent — every chat request still
   * reads the live player via getCurrentTimeMs() below. Rounded to seconds so
   * the page re-renders about once a second, not on every timeupdate.
   */
  const [playheadMs, setPlayheadMs] = useState(0);
  const onTimeChange = useCallback((ms: number): void => {
    setPlayheadMs(Math.floor(ms / 1000) * 1000);
  }, []);

  /**
   * Fullscreen with the tutor. The page (not the player) goes fullscreen, and
   * the player and the ONE ChatPanel are re-laid out as fixed panes. Nothing
   * remounts, so the chat keeps its history and the player keeps its element,
   * its time reporting and its frame capture. If the browser refuses the
   * Fullscreen API (iOS Safari), the same layout still fills the window.
   */
  const [immersive, setImmersive] = useState(false);
  const [immersiveChat, setImmersiveChat] = useState(true);
  useEffect(() => {
    const onChange = (): void => {
      if (!document.fullscreenElement) setImmersive(false);
    };
    document.addEventListener("fullscreenchange", onChange);
    return () => document.removeEventListener("fullscreenchange", onChange);
  }, []);
  useEffect(() => {
    if (!immersive) return;
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const onKey = (e: KeyboardEvent): void => {
      // Esc in the window fallback; native fullscreen handles its own Esc.
      if (e.key === "Escape" && !document.fullscreenElement) setImmersive(false);
    };
    window.addEventListener("keydown", onKey);
    return () => {
      document.body.style.overflow = prev;
      window.removeEventListener("keydown", onKey);
    };
  }, [immersive]);
  const immersiveCtx = useMemo<Immersive>(
    () => ({
      active: immersive,
      toggle: () => {
        if (immersive) {
          setImmersive(false);
          if (document.fullscreenElement) void document.exitFullscreen().catch(() => undefined);
          return;
        }
        setImmersive(true);
        const root = document.documentElement;
        if (typeof root.requestFullscreen === "function") {
          void root.requestFullscreen().catch(() => undefined);
        }
      },
      chatOpen: immersiveChat,
      toggleChat: () => setImmersiveChat((o) => !o),
    }),
    [immersive, immersiveChat],
  );
  const handlePauseAt = useCallback(
    (ms: number): void => {
      setPausedAtMs(ms);
      onTimeChange(ms);
    },
    [onTimeChange],
  );
  /** Resource ids the user explicitly ticked. Nothing else marks work done. */
  const [completedIds, setCompletedIds] = useState<string[]>([]);

  useEffect(() => {
    setCompletedIds(readCompletion(subjectId ?? ""));
  }, [subjectId]);

  useEffect(() => {
    setPlayheadMs(0);
  }, [resourceId]);

  const toggleCompleted = useCallback(
    (id: string): void => {
      setCompletedIds((prev) => {
        const next = prev.includes(id)
          ? prev.filter((x) => x !== id)
          : [...prev, id];
        writeCompletion(subjectId ?? "", next);
        return next;
      });
    },
    [subjectId],
  );

  /**
   * What the chat may read besides the open material: the ticked ones that
   * still exist in this subject. The server intersects this with the session
   * grant again, so a stale or edited list can only narrow, never widen.
   */
  const completedForChat = useMemo(
    () =>
      completedIds.filter(
        (id) => id !== resourceId && resources.some((r) => r.id === id),
      ),
    [completedIds, resourceId, resources],
  );

  /**
   * Explicit, history-independent return to the subject page. It is a push,
   * not `history.back()`, because it must work on a cold deep link too.
   */
  const backToSubject = useCallback((): void => {
    router.push(malai ? malaiSubjectPath(subjectId ?? "") : subjectPath(subjectId ?? ""));
  }, [router, subjectId, malai]);

  const openResource = useCallback(
    (id: string): void => {
      router.push(malai ? malaiLessonPath(subjectId ?? "", id) : contentPath(subjectId ?? "", id));
    },
    [router, subjectId, malai],
  );

  // The sample clip is not in the resource list, so it is resolved from the URL
  // directly. Everything else must match a real resource before we render.
  // Null means "not resolved yet" (still loading) or "not playable" — the two
  // are distinguished below.
  const selected = useMemo<Selected | null>(() => {
    if (resourceId === SAMPLE_CLIP_ID) return sampleClipSelected();
    if (resourcesState !== "ready") return null;
    const match = resources.find((r) => r.id === resourceId);
    return match ? toSelected(match) : null;
  }, [resourceId, resources, resourcesState]);

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

  /**
   * The citation the learner last clicked, if it came from this resource. A
   * PDF one becomes a viewer target (page + passage to highlight; the nonce
   * re-applies a repeated click). A video one is drawn on the scrub bar and its
   * passage shown under the player; the seek itself is handleSeek's job.
   */
  const [pdfJump, setPdfJump] = useState<PdfTarget | null>(null);
  const [cited, setCited] = useState<Citation | null>(null);
  useEffect(() => {
    setPdfJump(null);
    setCited(null);
  }, [resourceId]);
  const openDoc = useMemo(
    () => ({
      resourceId,
      onOpen: (c: Citation): void => {
        if (c.page_no != null) {
          const page = c.page_no;
          setPdfJump((prev) => ({ page, text: c.text ?? null, nonce: (prev?.nonce ?? 0) + 1 }));
        } else if (c.start_ms != null) {
          setCited(c);
        }
      },
    }),
    [resourceId],
  );
  const citedRange = useMemo(
    () =>
      cited?.start_ms != null
        ? { startMs: cited.start_ms, endMs: cited.end_ms ?? cited.start_ms }
        : null,
    [cited],
  );

  /**
   * Capture the frame the learner is looking at RIGHT NOW.
   *
   * The two lines that matter are `currentTime` and `drawImage`, and they are
   * adjacent on purpose: reading the playhead and painting the pixels happen in
   * ONE synchronous tick, with no `await`, no state update and no reflow
   * between them. A stale frame posted with a fresh timestamp is the one
   * realistic spoiler leak this feature could introduce — the pixels and the
   * number that describes them must be the same instant, and only synchronous
   * adjacency guarantees that.
   *
   * `canvas.toBlob` is asynchronous, but it encodes the ALREADY-FROZEN canvas
   * bitmap, not the video element, so the async encode cannot go stale.
   *
   * Returns `null` when there is nothing honest to send — no video element, or
   * a video with no decoded frame yet (videoWidth/videoHeight are 0). The
   * caller must then fall back to an ordinary question, never to a substitute
   * frame.
   */
  const captureFrame = useCallback(async (): Promise<LiveFrameCapture | null> => {
    const el = videoRef.current;
    if (!el) return null;
    const width = el.videoWidth;
    const height = el.videoHeight;
    if (!width || !height) return null;  // nothing decoded yet -> no frame exists
    const canvas = document.createElement("canvas");
    canvas.width = width;
    canvas.height = height;
    const ctx = canvas.getContext("2d");
    if (!ctx) return null;

    // ---- SAME SYNCHRONOUS TICK: playhead read, then pixels painted. ----
    const atMs = Math.max(0, Math.floor(el.currentTime * 1000));
    ctx.drawImage(el, 0, 0, width, height);
    // ---------------------------------------------------------------------

    const blob = await new Promise<Blob | null>((resolve) => {
      canvas.toBlob((b) => resolve(b), "image/jpeg", 0.8);
    });
    if (!blob) return null;
    return { blob, atMs };
  }, []);

  const activeResourceId = selected ? selected.resourceId : null;

  // Feeds the dashboard: last lesson opened, and time spent studying.
  useStudyTracker(
    subjectId && selected && selected.resourceId !== SAMPLE_CLIP_ID
      ? {
          subjectId,
          subjectName: subject?.name ?? null,
          resourceId: selected.resourceId,
          title: selected.title,
        }
      : null,
  );

  // Position heartbeat: tells the backend where a stream-mode video is being
  // watched so later windows are processed ahead. Throttled to ~10 s while
  // playing, immediate on seek, stopped once the server says "ready".
  // Errors are swallowed; the real course id comes from the subject record.
  const hbCourseId = subject?.course_id ?? null;
  const hbResourceId = selected?.kind === "file" ? selected.resourceId : null;
  const hbRes = resources.find((r) => r.id === hbResourceId);
  const hbEligible = Boolean(hbRes) && (!hbRes?.status || hbRes.status === "partial");
  // Latest processing state: seeded from the resource list, then refreshed by
  // every heartbeat answer so the learner can see windows being processed.
  const [proc, setProc] = useState<{
    through: number | null;
    visual: number | null;
    duration: number | null;
    state: string;
    error: string | null;
  } | null>(null);
  const procThrough = proc?.through ?? hbRes?.processed_through_ms ?? null;
  const procState = proc?.state ?? (hbRes?.status === "partial" ? "partial" : "ready");
  const fmtClock = (ms: number): string => {
    const s = Math.max(0, Math.floor(ms / 1000));
    return `${Math.floor(s / 60)}:${(s % 60).toString().padStart(2, "0")}`;
  };
  useEffect(() => {
    const el = videoRef.current;
    if (!el || !hbResourceId || !hbCourseId || !apiLive || !hbEligible) return;
    let done = false;
    let last = 0;
    const send = (): void => {
      if (done) return;
      last = Date.now();
      reportPosition(hbCourseId, hbResourceId, Math.floor(el.currentTime * 1000))
        .then((r) => {
          setProc({
            through: r.processed_through_ms ?? null,
            visual: r.visual_through_ms ?? null,
            duration: r.duration_ms ?? null,
            state: r.status,
            error: r.error ?? null,
          });
          if (r.status === "ready") done = true;
        })
        .catch(() => undefined);
    };
    const onTime = (): void => {
      if (!el.paused && Date.now() - last >= 10_000) send();
    };
    el.addEventListener("timeupdate", onTime);
    el.addEventListener("play", send);
    el.addEventListener("seeked", send);
    return () => {
      done = true;
      el.removeEventListener("timeupdate", onTime);
      el.removeEventListener("play", send);
      el.removeEventListener("seeked", send);
    };
  }, [hbResourceId, hbCourseId, apiLive, hbEligible]);

  // Window layout: Malai's side, the split, the panel, and "hide video".
  const layout = useLessonLayout();
  // One window may be hidden at a time (both kept mounted); hiding one
  // brings the other back.
  const [lessonHidden, setLessonHidden] = useState(false);
  const [malaiHidden, setMalaiHidden] = useState(false);
  const toggleLesson = useCallback((): void => {
    if (!lessonHidden) {
      // Out of sight is not out of earshot: stop playback when hiding.
      videoRef.current?.pause();
      ytHandleRef.current?.pause();
      setMalaiHidden(false);
    }
    setLessonHidden(!lessonHidden);
  }, [lessonHidden]);
  const toggleMalai = useCallback((): void => {
    if (!malaiHidden) setLessonHidden(false);
    setMalaiHidden(!malaiHidden);
  }, [malaiHidden]);
  /** At `lg` the windows dock; below it they simply stack. */
  const [wide, setWide] = useState(false);
  useEffect(() => {
    const mq = window.matchMedia("(min-width: 1024px)");
    const on = (): void => setWide(mq.matches);
    on();
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, []);

  // File each chat asked here under its subject in Malai's sidebar, and keep
  // the address on that chat so a reload (or the sidebar) reopens it.
  const sessionId = session?.session_id ?? null;
  const sessionToken = session?.session_token ?? null;
  const onAsk = useCallback(
    (threadId: string, question: string): void => {
      if (!sessionId || !sessionToken || !subjectId || resourceId === SAMPLE_CLIP_ID) return;
      const id = recordLessonChat({ sessionId, sessionToken, threadId, subjectId, resourceId, question });
      if (malai && id !== openChatId) {
        // Same page, new address: no remount, so the answer keeps streaming.
        window.history.replaceState(null, "", malaiLessonPath(subjectId, resourceId, id));
      }
    },
    [sessionId, sessionToken, subjectId, resourceId, malai, openChatId],
  );
  const openChat = openChatId ? getChatSafe(openChatId) : null;
  const openThreadId = openChat && openChat.sessionId === sessionId ? openChat.threadId : null;

  if (!session) return null;

  if (subjectId === null) {
    return (
      <div className="rounded-2xl border border-amber-200 bg-amber-50 p-6 shadow-sm" role="alert">
        <p className="font-semibold text-amber-800">
          This content link is missing its subject.
        </p>
        <p className="mt-1 text-sm text-gray-700">
          A content URL needs both a subject and a resource. Start from the{" "}
          <Link className="font-medium underline" href={SUBJECTS_PATH}>
            subject list
          </Link>
          .
        </p>
      </div>
    );
  }

  if (resourcesState === "loading" || resourcesState === "idle") {
    return (
      <div
        className="rounded-xl border border-gray-200 bg-white p-6 text-center text-sm text-gray-500 shadow-sm"
        role="status"
      >
        <span className="mr-2 inline-block h-4 w-4 animate-spin rounded-full border-2 border-violet-400 border-t-transparent align-middle" />
        Loading content…
      </div>
    );
  }

  if (resourcesState === "error") {
    return (
      <div className="rounded-2xl border border-red-200 bg-red-50 p-6 shadow-sm" role="alert">
        <p className="font-semibold text-red-600">
          Could not load this subject&apos;s content.
        </p>
        <p className="mt-1 text-sm text-gray-600">{resourcesError}</p>
        <div className="mt-4">
          <button
            type="button"
            className="rounded-xl bg-violet-600 px-4 py-2 text-sm font-medium text-white transition hover:bg-violet-700"
            onClick={backToSubject}
          >
            ← Back to course
          </button>
        </div>
      </div>
    );
  }

  // Unknown id, or a real resource whose file no longer exists. Say so and
  // offer the way out — never an empty player and never a crash.
  if (!selected) {
    const match = resources.find((r) => r.id === resourceId);
    const reason = match
      ? "its original file is no longer available, so it cannot be played"
      : "no resource with this id exists in this subject";
    return (
      <div className="rounded-2xl border border-amber-200 bg-amber-50 p-6 shadow-sm">
        <p className="font-semibold text-amber-800">
          This content isn&apos;t available.
        </p>
        <p className="mt-1 text-sm text-gray-700">
          Nothing here matches <code className="rounded bg-white/70 px-1">{resourceId}</code>{" "}
          — {reason}. It may have been re-ingested under a new id.
        </p>
        <div className="mt-4">
          <button
            type="button"
            className="rounded-xl bg-violet-600 px-4 py-2 text-sm font-medium text-white transition hover:bg-violet-700"
            onClick={backToSubject}
          >
            ← Back to course
          </button>
        </div>
      </div>
    );
  }

  const isMedia = selected.kind === "file" || selected.kind === "youtube";
  const doneCount = resources.filter((r) => completedIds.includes(r.id)).length;
  const selectedRes = resources.find((r) => r.id === selected.resourceId);
  // The developer-view button floats over the workspace's top-right corner;
  // whichever window is there keeps its header clear of it.
  const cornerWindow = lessonHidden
    ? "malai"
    : malaiHidden
      ? "lesson"
      : layout.side === "right" || layout.side === "top"
        ? "malai"
        : "lesson";
  const panelPlacement = wide && layout.stacked ? "side" : "below";

  const iconButton =
    "flex h-8 w-8 shrink-0 items-center justify-center rounded-lg text-gray-500 transition hover:bg-gray-100 hover:text-gray-800 focus:outline-none focus-visible:ring-2 focus-visible:ring-violet-400";

  /* Course materials: completion ticks + open + ingest status.
     TODO(backend): `GET /subjects/{id}/resources` returns a flat resource list
     with no unit/module hierarchy, so this list is derived from the ingested
     resources themselves rather than from a course tree. Once the backend
     exposes the real tree (unit -> ordered lessons -> resource ids), render
     that here and keep the same id-based completion map. */
  const materials = (
    <div aria-label="Subject content">
      <div className="mb-2 flex flex-wrap items-baseline justify-between gap-2 px-1">
        <h2 className="text-sm font-semibold text-gray-900">Course materials</h2>
        {resources.length > 0 && (
          <span className="text-xs text-gray-400">
            {doneCount} of {resources.length} marked done
          </span>
        )}
      </div>
      {/* Loading and error are handled above, so by this point the list has
          resolved and may legitimately be empty. */}
      {resources.length === 0 ? (
        <p className="rounded-xl border border-dashed border-gray-200 px-3 py-3 text-xs text-gray-400">
          No subject content available yet — no resources have been ingested
          for this subject.
        </p>
      ) : (
        <ul className="flex flex-col gap-1">
          {resources.map((r) => {
            const s = toSelected(r);
            const isDone = completedIds.includes(r.id);
            const isCurrent = activeResourceId === r.id;
            const statusStyle =
              r.status === "done"
                ? "border-emerald-200 bg-emerald-50 text-emerald-600"
                : r.status === "failed"
                  ? "border-red-200 bg-red-50 text-red-600"
                  : r.status
                    ? "border-amber-200 bg-amber-50 text-amber-600"
                    : "border-gray-200 bg-gray-50 text-gray-500";
            const body = (
              <>
                <span
                  className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-lg text-sm ${
                    isCurrent ? "bg-violet-600 text-white" : "bg-gray-100"
                  }`}
                  aria-hidden="true"
                >
                  {resourceIcon(r)}
                </span>
                <span className="min-w-0 flex-1">
                  <span
                    className={`block break-words text-sm font-medium ${
                      isCurrent ? "text-violet-800" : "text-gray-800"
                    } ${isDone ? "line-through decoration-gray-300" : ""}`}
                  >
                    {r.title ?? r.id}
                  </span>
                  <span className="block text-[11px] text-gray-400">
                    {resourceKindLabel(r)}
                  </span>
                </span>
                {isCurrent && (
                  <span className="shrink-0 rounded-full bg-violet-100 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-violet-700">
                    Viewing
                  </span>
                )}
                {r.status && (
                  <span className={`shrink-0 rounded-full border px-2 py-0.5 text-[10px] font-medium ${statusStyle}`}>
                    {r.status}
                  </span>
                )}
              </>
            );
            const rowClass =
              "flex min-w-0 flex-1 items-center gap-3 rounded-xl px-2 py-2 text-left transition";
            return (
              <li
                key={r.id}
                className={`flex items-center gap-2 rounded-xl pl-2 ${
                  isCurrent ? "bg-violet-50 ring-1 ring-violet-200" : ""
                }`}
              >
                <input
                  type="checkbox"
                  className="h-4 w-4 shrink-0 cursor-pointer rounded accent-violet-600"
                  checked={isDone}
                  onChange={() => toggleCompleted(r.id)}
                  aria-label={`Mark "${r.title ?? r.id}" as done`}
                  title="Mark as done"
                />
                {s ? (
                  <button
                    type="button"
                    onClick={() => openResource(r.id)}
                    aria-current={isCurrent ? "true" : undefined}
                    title={`Open ${resourceKindLabel(r)}`}
                    className={`${rowClass} hover:bg-violet-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-violet-300`}
                  >
                    {body}
                  </button>
                ) : (
                  <div
                    className={`${rowClass} opacity-60`}
                    title="Original file is unavailable, so this resource cannot be opened"
                  >
                    {body}
                    <span className="sr-only">(original file unavailable)</span>
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      )}
      <p className="mt-3 px-1 text-[11px] leading-snug text-gray-400">
        Tick an item to mark it done — nothing is marked complete
        automatically. Titles and statuses come from the ingested course
        resources.
      </p>
    </div>
  );

  const lessonWindow = (grip: ReactNode) => (
    <div className="flex min-h-0 min-w-0 flex-1 flex-col gap-3">
      {/* Window header: move handle, back, breadcrumb */}
      <div className={`flex min-w-0 shrink-0 items-center gap-1.5 ${cornerWindow === "lesson" ? "md:pr-14" : ""}`}>
        {grip}
        <button
          type="button"
          onClick={backToSubject}
          aria-label="Back to subject"
          title="Back to subject"
          className={iconButton}
        >
          <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="h-4 w-4" aria-hidden="true">
            <path d="M12.5 4.5 7 10l5.5 5.5" />
          </svg>
        </button>
        <nav aria-label="Breadcrumb" className="flex min-w-0 flex-1 items-center gap-1.5 text-sm font-medium text-gray-500">
          <Link href={malai ? MALAI_PATH : SUBJECTS_PATH} className="hidden shrink-0 transition hover:text-violet-700 sm:inline">
            {malai ? "Malai" : "My subjects"}
          </Link>
          <span aria-hidden="true" className="hidden text-gray-300 sm:inline">›</span>
          <Link href={malai ? malaiSubjectPath(subjectId) : subjectPath(subjectId)} className="min-w-0 max-w-[40%] shrink truncate transition hover:text-violet-700">
            {subject?.name ?? "Subject"}
          </Link>
          <span aria-hidden="true" className="text-gray-300">›</span>
          <span className="min-w-0 flex-1 truncate text-gray-800" aria-current="page">
            {selected.title}
          </span>
        </nav>
        {isMedia && (
          <div
            className="hidden shrink-0 items-center gap-2 rounded-full border border-violet-200 bg-violet-50 py-1 pl-2.5 pr-3 text-xs font-medium text-violet-800 sm:flex"
            role="status"
            aria-live="off"
            title="The tutor only answers from what has played up to the current playhead."
          >
            <span className="relative flex h-2 w-2" aria-hidden="true">
              <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-violet-400 opacity-60" />
              <span className="relative inline-flex h-2 w-2 rounded-full bg-violet-600" />
            </span>
            Malai sees up to{" "}
            <span className="font-semibold tabular-nums text-violet-900">{fmtClock(playheadMs)}</span>
          </div>
        )}
      </div>

      <div
        className={`flex flex-col gap-4 lg:min-h-0 lg:flex-1 ${
          layout.stacked ? "lg:flex-row lg:overflow-hidden" : "lg:overflow-y-auto lg:pr-1"
        }`}
      >
        {/* Player + what Malai reads + processing status */}
        <section
          className={
            immersive
              ? `fixed inset-y-0 left-0 z-[60] flex animate-[immersive-in_280ms_cubic-bezier(0.2,0.8,0.2,1)] items-center justify-center bg-black transition-[right] duration-300 ease-out ${
                  immersiveChat ? "right-0 sm:right-[400px]" : "right-0"
                }`
              : `flex min-w-0 flex-col gap-3 ${layout.stacked ? "lg:min-h-0 lg:flex-1 lg:overflow-y-auto" : ""}`
          }
          aria-label="Lesson"
        >
          {immersive && isMedia && (
            <div className="pointer-events-none absolute left-4 top-4 z-[70] flex items-center gap-2 rounded-full bg-black/55 py-1.5 pl-2.5 pr-3.5 text-xs font-medium text-white/90 backdrop-blur">
              <span className="h-2 w-2 rounded-full bg-violet-400" aria-hidden="true" />
              Malai can see up to{" "}
              <span className="font-semibold tabular-nums text-white">{fmtClock(playheadMs)}</span>
            </div>
          )}
          {/* Stacked: the player is as large as the window's height allows,
              at 16:9 (a size container, so its width follows its height).
              In immersive mode both wrappers step aside (display: contents):
              a size container would also trap the fixed-position layout. */}
          <div
            className={
              immersive
                ? "contents"
                : layout.stacked && isMedia
                  ? "lg:min-h-[180px] lg:flex-1 lg:[container-type:size]"
                  : ""
            }
          >
            <div className={immersive ? "contents" : layout.stacked && isMedia ? "lg:mx-auto lg:w-[min(100cqw,calc(100cqh*16/9))]" : ""}>
              <ImmersiveContext.Provider value={immersiveCtx}>
              <CitedRangeContext.Provider value={citedRange}>
              {selected.kind === "youtube" ? (
                <YouTubePlayer
                  ref={ytHandleRef}
                  youtubeId={selected.youtubeId}
                  title={selected.title}
                  onPauseAt={handlePauseAt}
                  onTimeChange={onTimeChange}
                />
              ) : selected.kind === "pdf" ||
                selected.kind === "pptx" ||
                selected.kind === "image" ? (
                <DocumentViewer
                  key={selected.resourceId}
                  kind={selected.kind}
                  src={selected.src}
                  title={selected.title}
                  jump={pdfJump}
                />
              ) : (
                <VideoPlayer
                  src={selected.src}
                  videoRef={videoRef}
                  onPauseAt={handlePauseAt}
                  onTimeChange={onTimeChange}
                />
              )}
              </CitedRangeContext.Provider>
              </ImmersiveContext.Provider>
            </div>
          </div>
          {!immersive && (
            <div className="shrink-0 px-1">
              <h1 className="truncate text-lg font-semibold tracking-tight text-gray-900 sm:text-xl">
                {selected.title}
              </h1>
              <p className="truncate text-sm text-gray-500">
                {subject?.name ?? "Subject"}
                {selectedRes ? ` · ${resourceKindLabel(selectedRes)}` : ""}
              </p>
            </div>
          )}
          {isMedia && !immersive && cited?.start_ms != null && (
            <div className="shrink-0 rounded-2xl border border-amber-200 bg-amber-50/70 px-4 py-3 shadow-sm" role="region" aria-label="Cited passage">
              <div className="mb-1.5 flex items-center gap-2">
                <span className="h-2.5 w-2.5 rounded-sm bg-amber-400" aria-hidden="true" />
                <span className="text-xs font-semibold text-amber-900">
                  {cited.live
                    ? `Live frame at ${fmtClock(cited.start_ms)}`
                    : `Cited ${cited.modality === "visual" ? "on screen" : cited.modality === "transcript" ? "speech" : "passage"} · ${fmtClock(cited.start_ms)}–${fmtClock(cited.end_ms ?? cited.start_ms)}`}
                </span>
                <button
                  type="button"
                  onClick={() => handleSeek(cited.start_ms as number)}
                  className="ml-auto rounded-lg px-2 py-0.5 text-xs font-medium text-amber-800 transition hover:bg-amber-100"
                >
                  ▶ Replay
                </button>
                <button
                  type="button"
                  onClick={() => setCited(null)}
                  aria-label="Clear cited passage"
                  className="rounded-lg px-1.5 py-0.5 text-xs text-amber-700 transition hover:bg-amber-100"
                >
                  ✕
                </button>
              </div>
              {cited.text ? (
                <p className="whitespace-pre-line text-sm leading-relaxed text-gray-800">
                  <mark className="rounded bg-amber-200/70 px-0.5 text-gray-900">{cited.text}</mark>
                </p>
              ) : (
                <p className="text-xs text-gray-500">The passage text was not saved with this older answer.</p>
              )}
            </div>
          )}
          {!isMedia && !immersive && (
            <p className="shrink-0 px-1 text-xs text-gray-500">
              Malai answers from this {selected.kind === "pptx" ? "deck" : selected.kind === "image" ? "image" : "document"} with{" "}
              {selected.kind === "pptx" ? "slide" : selected.kind === "image" ? "image" : "page"} citations
              {selected.kind === "pdf" ? " — click one to see the passage highlighted on its page" : ""}.
            </p>
          )}
          {isMedia && !immersive && (
            <p className="shrink-0 px-1 text-xs text-gray-500">
              Malai answers only from what you&apos;ve watched so far. Click a
              timestamp in an answer to jump there. Shortcuts: Space/K play ·
              ←/→ 5s · J/L 10s · M mute · F fullscreen.
            </p>
          )}
          {!immersive && selected.kind === "file" && procThrough !== null && procState !== "ready" && (
            <p className="shrink-0 rounded-2xl border border-violet-200 bg-violet-50 px-4 py-2.5 text-sm text-violet-800" role="status">
              Processed through {fmtClock(procThrough)}
              {proc?.duration ? ` of ${fmtClock(proc.duration)}` : ""}.{" "}
              {proc?.visual != null && proc.visual < procThrough
                ? `On-screen descriptions are ready through ${fmtClock(proc.visual)} (still processing). `
                : ""}
              {procState === "failed"
                ? ""
                : procState === "queued" || procState === "running"
                  ? "Processing the next part now…"
                  : "The rest is processed as you watch."}
            </p>
          )}
          {!immersive && selected.kind === "file" && procState === "failed" && (
            <p className="shrink-0 rounded-2xl border border-red-200 bg-red-50 px-4 py-2.5 text-sm text-red-700" role="alert">
              Processing stopped: {proc?.error ?? "the server could not process the next part."}
            </p>
          )}
        </section>

        {/* TODO(chapters): no chapter data yet; see to-do.md. */}
        <LessonPanel
          placement={panelPlacement}
          isMedia={isMedia}
          chapters={null}
          playheadMs={playheadMs}
          onSeek={handleSeek}
          resources={materials}
          collapsed={layout.panelCollapsed}
          onCollapsedChange={layout.setPanelCollapsed}
        />
      </div>
    </div>
  );

  const malaiWindow = (grip: ReactNode) => (
    <aside
      className={
        !immersive
          ? "flex min-h-0 min-w-0 flex-1 flex-col"
          : `fixed inset-y-0 right-0 z-[61] w-full bg-white shadow-2xl transition-transform duration-300 ease-out sm:w-[400px] [&>section]:rounded-none [&>section]:border-0 ${
              immersiveChat
                ? "translate-x-0 animate-[immersive-chat-in_320ms_cubic-bezier(0.2,0.8,0.2,1)]"
                : "pointer-events-none translate-x-full"
            }`
      }
      aria-hidden={immersive && !immersiveChat ? true : undefined}
      aria-label="Malai tutor chat"
    >
      {immersive && (
        <button
          type="button"
          onClick={() => setImmersiveChat(false)}
          aria-label="Hide chat"
          title="Hide chat (c)"
          className="absolute right-3 top-3 z-10 flex h-8 w-8 items-center justify-center rounded-full text-gray-400 transition hover:bg-gray-100 hover:text-gray-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-violet-400"
        >
          <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" className="h-4 w-4" aria-hidden="true">
            <path d="M5 5l10 10M15 5 5 15" />
          </svg>
        </button>
      )}
      <ChatPanel
        fill
        sessionId={session.session_id}
        sessionToken={session.session_token}
        videoId={selected.resourceId}
        getCurrentTimeMs={getCurrentTimeMs}
        captureFrame={captureFrame}
        onSeek={handleSeek}
        openDoc={selected.kind === "pptx" || selected.kind === "image" || (selected.kind === "pdf" && !selected.src) ? null : openDoc}
        completedResourceIds={completedForChat}
        offline={offline}
        onRejoin={reset}
        openThreadId={openThreadId}
        onAsk={onAsk}
        headerLead={grip}
        isVideo={isMedia}
        headerActions={
          !immersive && cornerWindow === "malai" ? <span className="hidden w-11 shrink-0 md:block" aria-hidden="true" /> : null
        }
      />
    </aside>
  );

  return (
    // The bottom gutter is the floating toolbar's place, so it covers nothing.
    <div className="relative mx-auto flex w-full max-w-[1600px] flex-col pb-20 lg:h-[calc(100dvh-8rem)] lg:pb-0">
      <LessonWorkspace
        side={layout.side}
        split={layout.split}
        onSide={layout.setSide}
        onSplit={layout.setSplit}
        onResetSplit={layout.resetSplit}
        lessonHidden={lessonHidden}
        malaiHidden={malaiHidden}
        immersive={immersive}
        lesson={lessonWindow}
        malai={malaiWindow}
      />
      {!immersive && (
        <WindowToolbar
          open={layout.toolsOpen}
          onOpenChange={layout.setToolsOpen}
          resourceLabel={isMedia ? "video" : "resource"}
          lessonShown={!lessonHidden}
          malaiShown={!malaiHidden}
          stacked={layout.stacked}
          onToggleLesson={toggleLesson}
          onToggleMalai={toggleMalai}
          onToggleOrientation={layout.toggleOrientation}
          onSwap={layout.swap}
        />
      )}
    </div>
  );
}

/** `getChat` reads browser storage, which the server render does not have. */
function getChatSafe(id: string) {
  return typeof window === "undefined" ? null : getChat(id);
}
