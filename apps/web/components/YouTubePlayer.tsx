"use client";

import {
  forwardRef,
  useEffect,
  useImperativeHandle,
  useRef,
} from "react";
import { youTubeNoCookieEmbedUrl } from "../lib/youtube";
import styles from "./YouTubePlayer.module.css";

/**
 * Privacy-enhanced YouTube player (youtube-nocookie embed + IFrame Player
 * API). Mirrors the VideoPlayer contract: reports pause position and
 * exposes seek/get-time for citation click-to-seek + "Ask at position".
 */
export interface YouTubePlayerHandle {
  seekTo: (startMs: number) => void;
  getCurrentTimeMs: () => number;
}

interface YouTubePlayerProps {
  youtubeId: string;
  title?: string;
  onPauseAt: (currentTimeMs: number) => void;
  onTimeChange?: (currentTimeMs: number) => void;
}

interface YTPlayerInstance {
  getCurrentTime: () => number;
  seekTo: (seconds: number, allowSeekAhead: boolean) => void;
  destroy: () => void;
}

interface YTNamespace {
  Player: new (
    element: HTMLElement,
    options: {
      host?: string;
      videoId?: string;
      playerVars?: Record<string, number | string>;
      events?: {
        onStateChange?: (event: { data: number }) => void;
        onError?: () => void;
      };
    },
  ) => YTPlayerInstance;
  PlayerState: { PAUSED: number };
}

declare global {
  interface Window {
    YT?: YTNamespace;
    onYouTubeIframeAPIReady?: () => void;
  }
}

const YT_PAUSED_FALLBACK = 2;
const POLL_MS = 500;

let ytApiPromise: Promise<YTNamespace> | null = null;

function loadYouTubeIframeApi(): Promise<YTNamespace> {
  if (typeof window === "undefined") {
    return Promise.reject(new Error("YouTube API requires a browser"));
  }
  if (window.YT?.Player) return Promise.resolve(window.YT);
  if (!ytApiPromise) {
    ytApiPromise = new Promise<YTNamespace>((resolve, reject) => {
      const prev = window.onYouTubeIframeAPIReady;
      window.onYouTubeIframeAPIReady = () => {
        prev?.();
        if (window.YT?.Player) resolve(window.YT);
        else reject(new Error("YouTube IFrame API failed to load"));
      };
      const script = document.querySelector<HTMLScriptElement>(
        'script[src="https://www.youtube.com/iframe_api"]',
      );
      if (!script) {
        const tag = document.createElement("script");
        tag.src = "https://www.youtube.com/iframe_api";
        tag.async = true;
        tag.onerror = () => {
          ytApiPromise = null;
          reject(new Error("Could not load the YouTube player script"));
        };
        document.head.appendChild(tag);
      }
      // Safety net: never leave the player in a permanent loading state.
      window.setTimeout(() => {
        if (window.YT?.Player) resolve(window.YT);
      }, 15000);
    });
  }
  return ytApiPromise;
}

function toMs(seconds: number): number {
  if (!Number.isFinite(seconds)) return 0;
  return Math.max(0, Math.floor(seconds * 1000));
}

const YouTubePlayer = forwardRef<YouTubePlayerHandle, YouTubePlayerProps>(
  function YouTubePlayer({ youtubeId, title, onPauseAt, onTimeChange }, ref) {
    const mountRef = useRef<HTMLDivElement | null>(null);
    const playerRef = useRef<YTPlayerInstance | null>(null);
    const latestMsRef = useRef(0);
    const onPauseAtRef = useRef(onPauseAt);
    const onTimeChangeRef = useRef(onTimeChange);
    onPauseAtRef.current = onPauseAt;
    onTimeChangeRef.current = onTimeChange;

    useImperativeHandle(
      ref,
      () => ({
        seekTo(startMs: number): void {
          if (!Number.isFinite(startMs)) return;
          playerRef.current?.seekTo(Math.max(0, startMs / 1000), true);
        },
        getCurrentTimeMs(): number {
          return latestMsRef.current;
        },
      }),
      [],
    );

    useEffect(() => {
      let cancelled = false;
      let player: YTPlayerInstance | null = null;
      let pollId: number | null = null;

      async function mount(): Promise<void> {
        try {
          const YT = await loadYouTubeIframeApi();
          if (cancelled || !mountRef.current) return;
          player = new YT.Player(mountRef.current, {
            host: "https://www.youtube-nocookie.com",
            videoId: youtubeId,
            playerVars: { rel: 0, modestbranding: 1 },
            events: {
              onStateChange: (event) => {
                const paused = event.data === (YT.PlayerState?.PAUSED ?? YT_PAUSED_FALLBACK);
                if (paused && player) {
                  const ms = toMs(player.getCurrentTime());
                  latestMsRef.current = ms;
                  onPauseAtRef.current(ms);
                }
              },
            },
          });
          playerRef.current = player;
          pollId = window.setInterval(() => {
            if (!player) return;
            try {
              const ms = toMs(player.getCurrentTime());
              latestMsRef.current = ms;
              onTimeChangeRef.current?.(ms);
            } catch {
              // Player not ready yet — keep last known position.
            }
          }, POLL_MS);
        } catch {
          if (cancelled || !mountRef.current) return;
          // API script unreachable (offline/blocked) — fall back to a
          // plain nocookie embed so the video still plays. Time tracking
          // and seek degrade gracefully (position stays 0).
          const iframe = document.createElement("iframe");
          iframe.src = youTubeNoCookieEmbedUrl(youtubeId);
          iframe.title = title ?? "Course video";
          iframe.allow =
            "accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture";
          iframe.allowFullscreen = true;
          mountRef.current.appendChild(iframe);
        }
      }

      latestMsRef.current = 0;
      void mount();
      return () => {
        cancelled = true;
        if (pollId !== null) window.clearInterval(pollId);
        playerRef.current = null;
        try {
          player?.destroy();
        } catch {
          // Destroy is best-effort during unmount/remount.
        }
      };
    }, [youtubeId]);

    return (
      <div className={styles.wrapper}>
        <div
          className={styles.frame}
          role="region"
          aria-label={title ?? "YouTube course video player"}
        >
          {/* IFrame API replaces this node with the nocookie player. The
              plain iframe below is the no-JS/API fallback with the same
              video — never both visible once the API mounts. */}
          <div ref={mountRef} className={styles.mount} />
          <noscript>
            <iframe
              src={youTubeNoCookieEmbedUrl(youtubeId)}
              title={title ?? "Course video"}
              allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture"
              allowFullScreen
            />
          </noscript>
        </div>
        <p className={styles.hint}>
          Pause the video, then ask a question — the chat uses your pause
          position as context.
        </p>
      </div>
    );
  },
);

export default YouTubePlayer;
