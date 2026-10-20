"use client";

import {
  forwardRef,
  useEffect,
  useImperativeHandle,
  useMemo,
  useRef,
  useState,
} from "react";
import { youTubeNoCookieEmbedUrl } from "../lib/youtube";
import PlayerFrame, {
  INITIAL_SNAPSHOT,
  type PlayerCommands,
  type PlayerSnapshot,
} from "./PlayerControls";

/**
 * Privacy-enhanced YouTube player (youtube-nocookie embed + IFrame Player
 * API). Mirrors the VideoPlayer contract: reports pause position and
 * exposes seek/get-time for citation click-to-seek + "Ask at position".
 *
 * YouTube's own controls are off (controls=0); the shared PlayerFrame chrome
 * drives the player through the API. The tutor-facing playhead contract is
 * unchanged: `getCurrentTimeMs()` returns the position sampled by the 500 ms
 * poll (or the exact pause position), and `onPauseAt` fires on the API's
 * PAUSED state. The UI poll below only feeds the controls and never touches
 * that value. If the API cannot load, a plain nocookie iframe with YouTube's
 * native controls is shown and the custom controls are hidden.
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
  playVideo: () => void;
  pauseVideo: () => void;
  getDuration: () => number;
  getPlayerState: () => number;
  getVideoLoadedFraction: () => number;
  getVolume: () => number;
  setVolume: (volume: number) => void;
  isMuted: () => boolean;
  mute: () => void;
  unMute: () => void;
  getPlaybackRate: () => number;
  setPlaybackRate: (rate: number) => void;
}

interface YTNamespace {
  Player: new (
    element: HTMLElement,
    options: {
      host?: string;
      videoId?: string;
      playerVars?: Record<string, number | string>;
      events?: {
        onReady?: () => void;
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
const YT_PLAYING = 1;
const YT_BUFFERING = 3;
const POLL_MS = 500;
const UI_POLL_MS = 250;

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

function finite(n: number): number {
  return Number.isFinite(n) ? n : 0;
}

function readSnapshot(p: YTPlayerInstance): PlayerSnapshot {
  const state = p.getPlayerState();
  const duration = finite(p.getDuration());
  return {
    playing: state === YT_PLAYING,
    waiting: state === YT_BUFFERING,
    currentTime: finite(p.getCurrentTime()),
    duration,
    buffered: finite(p.getVideoLoadedFraction()) * duration,
    volume: finite(p.getVolume()) / 100,
    muted: p.isMuted(),
    rate: finite(p.getPlaybackRate()) || 1,
  };
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

    /** "loading" until onReady; "fallback" when the API could not load. */
    const [phase, setPhase] = useState<"loading" | "ready" | "fallback">("loading");
    const [snap, setSnap] = useState<PlayerSnapshot>(INITIAL_SNAPSHOT);

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
      let uiPollId: number | null = null;
      let ready = false;

      const syncUi = (): void => {
        if (!player || !ready || cancelled) return;
        try {
          setSnap(readSnapshot(player));
        } catch {
          // Player mid-teardown — keep the last snapshot.
        }
      };

      async function mount(): Promise<void> {
        try {
          const YT = await loadYouTubeIframeApi();
          if (cancelled || !mountRef.current) return;
          player = new YT.Player(mountRef.current, {
            host: "https://www.youtube-nocookie.com",
            videoId: youtubeId,
            playerVars: {
              rel: 0,
              modestbranding: 1,
              controls: 0,
              disablekb: 1,
              fs: 0,
              iv_load_policy: 3,
              playsinline: 1,
            },
            events: {
              onReady: () => {
                if (cancelled) return;
                ready = true;
                setPhase("ready");
                syncUi();
              },
              onStateChange: (event) => {
                const paused = event.data === (YT.PlayerState?.PAUSED ?? YT_PAUSED_FALLBACK);
                if (paused && player) {
                  const ms = toMs(player.getCurrentTime());
                  latestMsRef.current = ms;
                  onPauseAtRef.current(ms);
                }
                syncUi();
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
          uiPollId = window.setInterval(syncUi, UI_POLL_MS);
        } catch {
          if (cancelled || !mountRef.current) return;
          // API script unreachable (offline/blocked) — fall back to a
          // plain nocookie embed so the video still plays. Time tracking
          // and seek degrade gracefully (position stays 0).
          setPhase("fallback");
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
      setPhase("loading");
      setSnap(INITIAL_SNAPSHOT);
      void mount();
      return () => {
        cancelled = true;
        if (pollId !== null) window.clearInterval(pollId);
        if (uiPollId !== null) window.clearInterval(uiPollId);
        playerRef.current = null;
        try {
          player?.destroy();
        } catch {
          // Destroy is best-effort during unmount/remount.
        }
      };
      // `title` is only used for the fallback iframe label; remounting the
      // player when it changes would reset playback.
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [youtubeId]);

    const commands = useMemo<PlayerCommands>(() => {
      const withPlayer = (fn: (p: YTPlayerInstance) => void): void => {
        const p = playerRef.current;
        if (!p) return;
        try {
          fn(p);
        } catch {
          // Player not ready — ignore the command.
        }
      };
      return {
        play: () =>
          withPlayer((p) => {
            p.playVideo();
            setSnap((s) => ({ ...s, playing: true }));
          }),
        pause: () =>
          withPlayer((p) => {
            p.pauseVideo();
            setSnap((s) => ({ ...s, playing: false }));
          }),
        seek: (seconds: number) =>
          withPlayer((p) => {
            if (!Number.isFinite(seconds)) return;
            const s = Math.max(0, seconds);
            p.seekTo(s, true);
            setSnap((prev) => ({ ...prev, currentTime: s }));
          }),
        setVolume: (v: number) =>
          withPlayer((p) => {
            const vol = Math.min(1, Math.max(0, v));
            p.setVolume(Math.round(vol * 100));
            setSnap((s) => ({ ...s, volume: vol }));
          }),
        setMuted: (m: boolean) =>
          withPlayer((p) => {
            if (m) p.mute();
            else p.unMute();
            setSnap((s) => ({ ...s, muted: m }));
          }),
        setRate: (r: number) =>
          withPlayer((p) => {
            p.setPlaybackRate(r);
            setSnap((s) => ({ ...s, rate: r }));
          }),
      };
    }, []);

    const state: PlayerSnapshot =
      phase === "loading" ? { ...snap, waiting: true } : snap;

    return (
      <PlayerFrame
        state={state}
        commands={commands}
        controlsEnabled={phase === "ready"}
        label={title ?? "YouTube course video player"}
      >
        <div className="h-full w-full [&_iframe]:pointer-events-auto [&_iframe]:h-full [&_iframe]:w-full">
          {/* IFrame API replaces this node with the nocookie player. The
              plain iframe below is the no-JS/API fallback with the same
              video — never both visible once the API mounts. With the API
              ready, PlayerFrame lays a click surface over the iframe so all
              interaction goes through the custom controls. */}
          <div ref={mountRef} className="h-full w-full" />
          <noscript>
            <iframe
              src={youTubeNoCookieEmbedUrl(youtubeId)}
              title={title ?? "Course video"}
              allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture"
              allowFullScreen
            />
          </noscript>
        </div>
      </PlayerFrame>
    );
  },
);

export default YouTubePlayer;
