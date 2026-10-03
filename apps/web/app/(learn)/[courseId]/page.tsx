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
  type CourseResource,
  type StoredSession,
} from "@/lib/api";
import styles from "./learn.module.css";

const FALLBACK_VIDEO =
  "https://interactive-examples.mdn.mozilla.net/media/cc0-videos/flower.mp4";

type Selected =
  | { kind: "file"; resourceId: string; src: string; title: string }
  | { kind: "youtube"; resourceId: string; youtubeId: string; title: string };

function toSelected(resource: CourseResource, fileSrc: string): Selected | null {
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
  return { kind: "file", resourceId: resource.id, src: fileSrc, title };
}

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
    <main className={styles.page}>
      <header className={styles.header}>
        <div>
          <h1 className={styles.title}>AI Learning Assistant</h1>
          <p className={styles.sub}>
            Course <code>{courseId}</code> · watch → pause → ask → understand
          </p>
        </div>
        {selected && (
          <button
            type="button"
            className={styles.back}
            onClick={() => setSelected(null)}
          >
            ← All videos
          </button>
        )}
      </header>

      {!session ? (
        <SessionBootstrap courseId={courseId} onReady={handleReady} />
      ) : !selected ? (
        <div className={styles.hub}>
          <YouTubeIngestForm
            courseId={courseId}
            onReady={handleIngested}
            disabled={!apiLive}
          />
          {!apiLive && (
            <p className={styles.note} role="note">
              YouTube ingest needs the live API — offline demo uses the file
              video below.
            </p>
          )}

          <section aria-label="Course videos">
            <h2 className={styles.sectionTitle}>Course videos</h2>
            {resourcesState === "loading" && (
              <div className={styles.status} role="status">
                Loading course videos…
              </div>
            )}
            {resourcesState === "error" && (
              <div className={styles.error} role="alert">
                Could not load course videos: {resourcesError}
              </div>
            )}
            {(resourcesState === "ready" || resourcesState === "idle") &&
              resources.length === 0 &&
              resourcesState === "ready" && (
                <div className={styles.status} role="status">
                  No videos in this course yet — paste a YouTube link above to
                  add the first one.
                </div>
              )}
            {resources.length > 0 && (
              <ul className={styles.list}>
                {resources.map((r) => {
                  const s = toSelected(r, fileSrc);
                  if (!s) return null;
                  return (
                    <li key={r.id} className={styles.item}>
                      <div>
                        <div className={styles.itemTitle}>{s.title}</div>
                        <div className={styles.itemMeta}>
                          {r.provider === "youtube" ? "YouTube" : "File"}
                          {r.status ? ` · ${r.status}` : ""}
                        </div>
                      </div>
                      <button
                        type="button"
                        className={styles.watch}
                        onClick={() => setSelected(s)}
                      >
                        Watch
                      </button>
                    </li>
                  );
                })}
              </ul>
            )}
            <div className={styles.demoRow}>
              <button
                type="button"
                className={styles.secondary}
                onClick={() =>
                  setSelected({
                    kind: "file",
                    resourceId: "demo-resource",
                    src: fileSrc,
                    title: "Demo file video",
                  })
                }
              >
                ▶ Play demo file video
              </button>
            </div>
          </section>
        </div>
      ) : (
        <div className={styles.grid}>
          <div className={styles.videoCol}>
            {selected.kind === "youtube" ? (
              <YouTubePlayer
                ref={ytHandleRef}
                youtubeId={selected.youtubeId}
                title={selected.title}
                onPauseAt={setPausedAtMs}
              />
            ) : (
              <VideoPlayer
                src={selected.src}
                videoRef={videoRef}
                onPauseAt={setPausedAtMs}
              />
            )}
          </div>
          <div className={styles.chatCol}>
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
        </div>
      )}
    </main>
  );
}
