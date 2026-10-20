"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import VideoPlayer from "@/components/VideoPlayer";
import YouTubePlayer, {
  type YouTubePlayerHandle,
} from "@/components/YouTubePlayer";
import ChatPanel from "@/components/ChatPanel";
import { useLearnSession } from "@/components/LearnSessionProvider";
import { useCourseResources } from "@/components/useCourseResources";
import {
  isApiConfigured,
  getSubjectResources,
  reportPosition,
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

/**
 * The player route: video/PDF + chat + sources, one real URL per resource.
 *
 * `resourceId` in the path is the authoritative selection — there is no local
 * "selected" state to drift out of sync with the address bar, which is what
 * makes the back button, a reload, and a shared link all behave the same.
 */
export default function ContentPage() {
  const params = useParams<{ subjectId?: string | string[]; resourceId?: string | string[] }>();
  const router = useRouter();
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
   * Explicit, history-independent return to the subject page. It is a push,
   * not `history.back()`, because it must work on a cold deep link too.
   */
  const backToSubject = useCallback((): void => {
    router.push(subjectPath(subjectId ?? ""));
  }, [router, subjectId]);

  const openResource = useCallback(
    (id: string): void => {
      router.push(contentPath(subjectId ?? "", id));
    },
    [router, subjectId],
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

  const isMedia = selected.kind !== "pdf";
  const doneCount = resources.filter((r) => completedIds.includes(r.id)).length;

  return (
    <div className="mx-auto flex w-full max-w-[1440px] flex-col gap-5">
      {/* Header: breadcrumb, back, title, tutor boundary chip */}
      <header className="flex flex-wrap items-end justify-between gap-x-4 gap-y-3">
        <div className="min-w-0 flex-1">
          <nav aria-label="Breadcrumb" className="mb-2 flex min-w-0 items-center gap-1.5 text-xs font-medium text-gray-500">
            <Link href={SUBJECTS_PATH} className="shrink-0 transition hover:text-violet-700">
              Subjects
            </Link>
            <span aria-hidden="true" className="text-gray-300">/</span>
            <Link href={subjectPath(subjectId)} className="min-w-0 truncate transition hover:text-violet-700">
              {subject?.name ?? "Subject"}
            </Link>
            <span aria-hidden="true" className="text-gray-300">/</span>
            <span className="min-w-0 truncate text-gray-400" aria-current="page">
              {selected.title}
            </span>
          </nav>
          <div className="flex min-w-0 items-center gap-3">
            <button
              type="button"
              onClick={backToSubject}
              aria-label="Back to subject"
              title="Back to subject"
              className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full border border-gray-200 bg-white text-gray-600 shadow-sm transition hover:border-violet-300 hover:bg-violet-50 hover:text-violet-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-violet-400"
            >
              <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="h-4 w-4" aria-hidden="true">
                <path d="M12.5 4.5 7 10l5.5 5.5" />
              </svg>
            </button>
            <h1 className="min-w-0 truncate text-xl font-semibold tracking-tight text-gray-900 sm:text-2xl">
              {selected.title}
            </h1>
          </div>
        </div>
        {isMedia && (
          <div
            className="flex shrink-0 items-center gap-2 rounded-full border border-violet-200 bg-violet-50 py-1.5 pl-2.5 pr-3.5 text-xs font-medium text-violet-800 shadow-sm"
            role="status"
            aria-live="off"
            title="The tutor only answers from what has played up to the current playhead."
          >
            <span className="relative flex h-2 w-2" aria-hidden="true">
              <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-violet-400 opacity-60" />
              <span className="relative inline-flex h-2 w-2 rounded-full bg-violet-600" />
            </span>
            Malai can see up to{" "}
            <span className="font-semibold tabular-nums text-violet-900">{fmtClock(playheadMs)}</span>
          </div>
        )}
      </header>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[minmax(0,2fr)_minmax(340px,1fr)] lg:items-start">
        {/* Player + processing status */}
        <section className="flex min-w-0 flex-col gap-3 lg:col-start-1 lg:row-start-1" aria-label="Lesson">
          {selected.kind === "youtube" ? (
            <YouTubePlayer
              ref={ytHandleRef}
              youtubeId={selected.youtubeId}
              title={selected.title}
              onPauseAt={handlePauseAt}
              onTimeChange={onTimeChange}
            />
          ) : selected.kind === "pdf" ? (
            <div className="flex items-start gap-4 rounded-2xl border border-gray-200 bg-white p-6 shadow-sm" role="note">
              <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-violet-50 text-xl" aria-hidden="true">
                📄
              </span>
              <div>
                <h2 className="mb-1 text-lg font-semibold text-gray-900">{selected.title}</h2>
                <p className="text-sm text-gray-500">
                  This is a document resource — its questions are answered through
                  the chat panel with page citations. Ask Malai a question.
                </p>
              </div>
            </div>
          ) : (
            <VideoPlayer
              src={selected.src}
              videoRef={videoRef}
              onPauseAt={handlePauseAt}
              onTimeChange={onTimeChange}
            />
          )}
          {isMedia && (
            <p className="px-1 text-xs text-gray-500">
              Malai answers only from what you&apos;ve watched so far. Click a
              timestamp in an answer to jump there. Shortcuts: Space/K play ·
              ←/→ 5s · J/L 10s · M mute · F fullscreen.
            </p>
          )}
          {selected.kind === "file" && procThrough !== null && procState !== "ready" && (
            <p className="rounded-2xl border border-violet-200 bg-violet-50 px-4 py-2.5 text-sm text-violet-800" role="status">
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
          {selected.kind === "file" && procState === "failed" && (
            <p className="rounded-2xl border border-red-200 bg-red-50 px-4 py-2.5 text-sm text-red-700" role="alert">
              Processing stopped: {proc?.error ?? "the server could not process the next part."}
            </p>
          )}
        </section>

        {/* Chat: right column on desktop, directly under the video on mobile */}
        <aside
          className="min-w-0 lg:sticky lg:top-20 lg:col-start-2 lg:row-span-2 lg:row-start-1 lg:self-start"
          aria-label="Malai tutor chat"
        >
          <ChatPanel
            sessionId={session.session_id}
            sessionToken={session.session_token}
            videoId={selected.resourceId}
            getCurrentTimeMs={getCurrentTimeMs}
            captureFrame={captureFrame}
            onSeek={handleSeek}
            offline={offline}
            onRejoin={reset}
          />
        </aside>

        {/* Course materials: completion ticks + open + ingest status.
            TODO(backend): `GET /subjects/{id}/resources` returns a flat
            resource list with no unit/module hierarchy, so this list is
            derived from the ingested resources themselves rather than
            from a course tree. Once the backend exposes the real tree
            (unit -> ordered lessons -> resource ids), render that here
            and keep the same id-based completion map. */}
        <section
          className="min-w-0 rounded-2xl border border-gray-200 bg-white p-4 shadow-sm sm:p-5 lg:col-start-1 lg:row-start-2"
          aria-label="Subject content"
        >
          <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
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
          <p className="mt-3 text-[11px] leading-snug text-gray-400">
            Tick an item to mark it done — nothing is marked complete
            automatically. Titles and statuses come from the ingested course
            resources.
          </p>
        </section>
      </div>
    </div>
  );
}
