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
  CHAT_LAYOUT_OPTIONS,
  SAMPLE_CLIP_ID,
  SUBJECTS_PATH,
  contentPath,
  firstParam,
  readChatLayout,
  readCompletion,
  resourceIcon,
  resourceKindLabel,
  sampleClipSelected,
  subjectPath,
  toSelected,
  writeChatLayout,
  writeCompletion,
  type ChatLayout,
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
  const [chatLayoutPref, setChatLayoutPref] = useState<ChatLayout>("bottom");
  const [isWide, setIsWide] = useState(false);
  /** Resource ids the user explicitly ticked. Nothing else marks work done. */
  const [completedIds, setCompletedIds] = useState<string[]>([]);

  // Mobile always uses the bottom layout, whatever was persisted.
  const chatLayout: ChatLayout = isWide ? chatLayoutPref : "bottom";

  useEffect(() => {
    setChatLayoutPref(readChatLayout());
  }, []);

  useEffect(() => {
    setCompletedIds(readCompletion(subjectId ?? ""));
  }, [subjectId]);

  useEffect(() => {
    if (typeof window.matchMedia !== "function") return;
    const mq = window.matchMedia("(min-width: 1024px)");
    const sync = (): void => setIsWide(mq.matches);
    sync();
    mq.addEventListener("change", sync);
    return () => mq.removeEventListener("change", sync);
  }, []);

  const chooseChatLayout = useCallback((value: ChatLayout): void => {
    setChatLayoutPref(value);
    writeChatLayout(value);
  }, []);

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
    duration: number | null;
    state: string;
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
            duration: r.duration_ms ?? null,
            state: r.status,
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
      <div className="rounded-xl border border-amber-200 bg-amber-50 p-6 shadow-sm" role="alert">
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
      <div className="rounded-xl border border-red-200 bg-red-50 p-6 shadow-sm" role="alert">
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
      <div className="rounded-xl border border-amber-200 bg-amber-50 p-6 shadow-sm">
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

  return (
    <div
      className={
        chatLayout === "right"
          ? "grid grid-cols-1 gap-6 lg:grid-cols-[250px_minmax(0,1fr)_minmax(320px,380px)]"
          : "grid grid-cols-1 gap-6 lg:grid-cols-[250px_minmax(0,1fr)_230px]"
      }
    >
      {/* Subject content tree.
          TODO(backend): `GET /courses/{id}/resources` returns a flat
          resource list with no unit/module hierarchy, so this list is
          derived from the ingested resources themselves rather than
          from a course tree. Once the backend exposes the real tree
          (unit -> ordered lessons -> resource ids), render that here
          and keep the same id-based completion map. */}
      <aside className="rounded-xl border border-gray-200 bg-white p-4 shadow-sm" aria-label="Subject content">
        <h2 className="mb-1 text-sm font-semibold uppercase tracking-wide text-gray-500">Subject Content</h2>
        <p className="mb-3 text-[11px] leading-snug text-gray-400">
          Tick an item to mark it done. Nothing is marked complete automatically.
        </p>
        {/* Loading and error are handled above, so by this point the list has
            resolved and may legitimately be empty. */}
        {resources.length === 0 ? (
          <p className="rounded-lg border border-dashed border-gray-200 px-3 py-2 text-xs text-gray-400">
            No subject content available yet — no resources have been ingested
            for this subject.
          </p>
        ) : (
          <ul className="flex flex-col gap-1">
            {resources.map((r) => {
              const isDone = completedIds.includes(r.id);
              const isCurrent = activeResourceId === r.id;
              return (
                <li key={r.id}>
                  <label
                    className={
                      isCurrent
                        ? "flex cursor-pointer items-center gap-2 rounded-lg bg-violet-50 px-2 py-1.5 text-xs font-medium text-violet-700"
                        : "flex cursor-pointer items-center gap-2 rounded-lg px-2 py-1.5 text-xs text-gray-600 hover:bg-gray-50"
                    }
                    aria-current={isCurrent ? "true" : undefined}
                  >
                    <input
                      type="checkbox"
                      className="h-3.5 w-3.5 shrink-0 accent-violet-600"
                      checked={isDone}
                      onChange={() => toggleCompleted(r.id)}
                    />
                    <span className="min-w-0 flex-1 break-words">{r.title ?? r.id}</span>
                    {isCurrent && (
                      <span className="shrink-0 text-[10px] font-semibold uppercase tracking-wide text-violet-600">
                        Viewing
                      </span>
                    )}
                  </label>
                </li>
              );
            })}
          </ul>
        )}
        {/* Hub return control, kept as-is in spirit: it goes back to the
            subject page, now via navigation rather than a state reset. */}
        <button
          type="button"
          className="mt-4 w-full rounded-lg border border-gray-200 px-3 py-1.5 text-xs text-gray-500 transition hover:bg-gray-50"
          onClick={backToSubject}
        >
          ← All videos
        </button>
      </aside>

      {/* Center: player (+ chat when layout = bottom) */}
      <div className="flex min-w-0 flex-col gap-6">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="flex min-w-0 items-center gap-2">
            <button
              type="button"
              onClick={backToSubject}
              className="shrink-0 rounded-lg border border-gray-200 bg-white px-2.5 py-1 text-xs font-medium text-gray-600 transition hover:border-violet-300 hover:bg-gray-50"
            >
              ← Back
            </button>
            <h2 className="min-w-0 truncate text-sm font-semibold text-gray-900">
              {selected.title}
            </h2>
          </div>
          <div
            className="hidden shrink-0 items-center gap-1 rounded-lg border border-gray-200 bg-white p-1 shadow-sm lg:flex"
            role="group"
            aria-label="Chat layout"
          >
            <span className="px-1.5 text-[11px] font-medium uppercase tracking-wide text-gray-400">
              Chat
            </span>
            {CHAT_LAYOUT_OPTIONS.map((opt) => (
              <button
                key={opt.value}
                type="button"
                title={opt.hint}
                aria-pressed={chatLayout === opt.value}
                onClick={() => chooseChatLayout(opt.value)}
                className={
                  chatLayout === opt.value
                    ? "rounded-md bg-violet-600 px-2.5 py-1 text-xs font-medium text-white"
                    : "rounded-md px-2.5 py-1 text-xs font-medium text-gray-500 transition hover:bg-gray-100 hover:text-gray-800"
                }
              >
                {opt.label}
              </button>
            ))}
          </div>
        </div>

        {selected.kind === "youtube" ? (
          <YouTubePlayer
            ref={ytHandleRef}
            youtubeId={selected.youtubeId}
            title={selected.title}
            onPauseAt={setPausedAtMs}
          />
        ) : selected.kind === "pdf" ? (
          <div className="rounded-xl border border-gray-200 bg-white p-6 shadow-sm" role="note">
            <h3 className="mb-2 text-lg font-semibold text-gray-900">{selected.title}</h3>
            <p className="text-sm text-gray-500">
              This is a document resource — its questions are answered through
              the chat panel with page citations. Ask a question below.
            </p>
          </div>
        ) : (
          <VideoPlayer
            src={selected.src}
            videoRef={videoRef}
            onPauseAt={setPausedAtMs}
          />
        )}
        {selected.kind === "file" && procThrough !== null && procState !== "ready" && (
          <p className="rounded-xl border border-violet-200 bg-violet-50 px-3 py-2 text-sm text-violet-800" role="status">
            Processed through {fmtClock(procThrough)}
            {proc?.duration ? ` of ${fmtClock(proc.duration)}` : ""}.{" "}
            {procState === "queued" || procState === "running"
              ? "Processing the next part now…"
              : "The rest is processed as you watch."}
          </p>
        )}
        {chatLayout === "bottom" && (
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
        )}
      </div>

      {/* Right column: chat (layout = right) + real ingested sources */}
      <aside className="flex flex-col gap-4" aria-label="Sources and topics">
        {chatLayout === "right" && (
          <div className="lg:sticky lg:top-20 lg:self-start">
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
          </div>
        )}
        <div className="rounded-xl border border-gray-200 bg-white p-4 shadow-sm">
          <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-gray-500">Sources</h2>
          {resources.length > 0 ? (
            <ul className="flex flex-col gap-1.5">
              {resources.map((r) => {
                const s = toSelected(r);
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
                    <span className="shrink-0" aria-hidden="true">{resourceIcon(r)}</span>
                    <span className="min-w-0 flex-1">
                      <span className="block break-words text-xs font-medium text-gray-800">
                        {r.title ?? r.id}
                      </span>
                      <span className="block text-[11px] text-gray-400">
                        {resourceKindLabel(r)}
                      </span>
                    </span>
                    {r.status && (
                      <span className={`shrink-0 rounded-full border px-2 py-0.5 text-[10px] font-medium ${statusStyle}`}>
                        {r.status}
                      </span>
                    )}
                  </>
                );
                const rowClass =
                  "flex w-full items-start gap-2 rounded-lg border border-transparent px-2 py-1.5 text-left transition";
                return (
                  <li key={r.id}>
                    {s ? (
                      <button
                        type="button"
                        onClick={() => openResource(r.id)}
                        aria-current={isCurrent ? "true" : undefined}
                        title={`Open ${resourceKindLabel(r)}`}
                        className={`${rowClass} hover:border-violet-200 hover:bg-violet-50 focus:outline-none focus:ring-2 focus:ring-violet-300 ${
                          isCurrent ? "border-violet-200 bg-violet-50" : ""
                        }`}
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
          ) : (
            <p className="text-xs text-gray-500">No sources ingested yet</p>
          )}
          <p className="mt-3 text-[11px] leading-snug text-gray-400">
            Titles and statuses come from the ingested course resources. Page/slide
            numbers appear only in chat citations.
          </p>
        </div>
      </aside>
    </div>
  );
}