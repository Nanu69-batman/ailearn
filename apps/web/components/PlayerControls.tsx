"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
  type MouseEvent as ReactMouseEvent,
  type ReactNode,
} from "react";

/**
 * Shared custom player chrome: overlay controls, seek bar, volume, speed,
 * fullscreen (on the container, so the controls stay), auto-hide and keyboard
 * shortcuts. It is purely presentational — it never reads or reports the
 * playhead to the tutor. The concrete players (VideoPlayer, YouTubePlayer)
 * own the media, the time reporting and the seek handle; this component only
 * renders a snapshot they give it and calls the commands they expose.
 */

export interface PlayerSnapshot {
  playing: boolean;
  /** Seconds. */
  currentTime: number;
  /** Seconds; 0 while unknown. */
  duration: number;
  /** Seconds buffered from the start (end of the buffered range). */
  buffered: number;
  /** 0..1 */
  volume: number;
  muted: boolean;
  rate: number;
  waiting: boolean;
}

export interface PlayerCommands {
  play: () => void;
  pause: () => void;
  seek: (seconds: number) => void;
  setVolume: (volume: number) => void;
  setMuted: (muted: boolean) => void;
  setRate: (rate: number) => void;
}

export const INITIAL_SNAPSHOT: PlayerSnapshot = {
  playing: false,
  currentTime: 0,
  duration: 0,
  buffered: 0,
  volume: 1,
  muted: false,
  rate: 1,
  waiting: false,
};

export const PLAYBACK_RATES = [0.5, 0.75, 1, 1.25, 1.5, 1.75, 2] as const;

/**
 * Page-owned fullscreen. When a page provides this, the fullscreen button
 * hands fullscreen to the page instead of the player container, so the page
 * can keep its (single, stateful) chat panel on screen beside the video.
 */
export interface Immersive {
  active: boolean;
  toggle: () => void;
  chatOpen: boolean;
  toggleChat: () => void;
}

export const ImmersiveContext = createContext<Immersive | null>(null);

const HIDE_AFTER_MS = 2500;

function clamp(n: number, lo: number, hi: number): number {
  return Math.min(hi, Math.max(lo, n));
}

export function formatClock(seconds: number): string {
  const total = Number.isFinite(seconds) ? Math.max(0, Math.floor(seconds)) : 0;
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = (total % 60).toString().padStart(2, "0");
  return h > 0 ? `${h}:${m.toString().padStart(2, "0")}:${s}` : `${m}:${s}`;
}

/* ------------------------------------------------------------------ icons */

function Svg({ children, className = "h-5 w-5" }: { children: ReactNode; className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="currentColor" className={className} aria-hidden="true">
      {children}
    </svg>
  );
}

const PlayIcon = ({ className }: { className?: string }) => (
  <Svg className={className}>
    <path d="M8 5.14v13.72a1 1 0 0 0 1.52.85l10.98-6.86a1 1 0 0 0 0-1.7L9.52 4.29A1 1 0 0 0 8 5.14Z" />
  </Svg>
);
const PauseIcon = ({ className }: { className?: string }) => (
  <Svg className={className}>
    <rect x="6" y="4.5" width="4" height="15" rx="1.2" />
    <rect x="14" y="4.5" width="4" height="15" rx="1.2" />
  </Svg>
);
const SpeakerBase = () => <path d="M4 9.5v5a1 1 0 0 0 1 1h3l4.4 3.67A1 1 0 0 0 14 18.4V5.6a1 1 0 0 0-1.6-.77L8 8.5H5a1 1 0 0 0-1 1Z" />;
const VolumeHighIcon = () => (
  <Svg>
    <SpeakerBase />
    <path d="M16.5 8.2a.9.9 0 0 1 1.27.08 5.6 5.6 0 0 1 0 7.44.9.9 0 1 1-1.35-1.2 3.8 3.8 0 0 0 0-5.04.9.9 0 0 1 .08-1.28Z" />
    <path d="M18.9 5.6a.9.9 0 0 1 1.27.05 9.4 9.4 0 0 1 0 12.7.9.9 0 1 1-1.32-1.23 7.6 7.6 0 0 0 0-10.24.9.9 0 0 1 .05-1.28Z" />
  </Svg>
);
const VolumeLowIcon = () => (
  <Svg>
    <SpeakerBase />
    <path d="M16.5 8.2a.9.9 0 0 1 1.27.08 5.6 5.6 0 0 1 0 7.44.9.9 0 1 1-1.35-1.2 3.8 3.8 0 0 0 0-5.04.9.9 0 0 1 .08-1.28Z" />
  </Svg>
);
const VolumeMuteIcon = () => (
  <Svg>
    <SpeakerBase />
    <path d="M16.3 9.3a.9.9 0 0 1 1.27 0L19 10.73l1.43-1.43a.9.9 0 1 1 1.27 1.27L20.27 12l1.43 1.43a.9.9 0 1 1-1.27 1.27L19 13.27l-1.43 1.43a.9.9 0 1 1-1.27-1.27L17.73 12 16.3 10.57a.9.9 0 0 1 0-1.27Z" />
  </Svg>
);
const FullscreenIcon = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="h-5 w-5" aria-hidden="true">
    <path d="M4 9V5a1 1 0 0 1 1-1h4M15 4h4a1 1 0 0 1 1 1v4M20 15v4a1 1 0 0 1-1 1h-4M9 20H5a1 1 0 0 1-1-1v-4" />
  </svg>
);
const ChatIcon = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="h-5 w-5" aria-hidden="true">
    <path d="M20 12a8 8 0 0 1-11.6 7.14L4 20l.86-4.4A8 8 0 1 1 20 12Z" />
    <path d="M8.5 10.5h7M8.5 13.5h4.5" />
  </svg>
);
const ExitFullscreenIcon = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="h-5 w-5" aria-hidden="true">
    <path d="M9 4v4a1 1 0 0 1-1 1H4M20 9h-4a1 1 0 0 1-1-1V4M15 20v-4a1 1 0 0 1 1-1h4M4 15h4a1 1 0 0 1 1 1v4" />
  </svg>
);

/* ---------------------------------------------------------------- pieces */

function IconButton({
  label,
  onClick,
  children,
  pressed,
}: {
  label: string;
  onClick: () => void;
  children: ReactNode;
  pressed?: boolean;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={label}
      aria-pressed={pressed}
      title={label}
      className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg text-white/90 transition hover:bg-white/15 hover:text-white focus:outline-none focus-visible:ring-2 focus-visible:ring-violet-400"
    >
      {children}
    </button>
  );
}

function SeekBar({
  current,
  duration,
  buffered,
  onSeek,
}: {
  current: number;
  duration: number;
  buffered: number;
  onSeek: (seconds: number) => void;
}) {
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const [hover, setHover] = useState<{ x: number; width: number; t: number } | null>(null);
  // While scrubbing, show the requested position instead of the (lagging)
  // reported one, so the thumb does not jump back between polls.
  const [scrub, setScrub] = useState<number | null>(null);
  const scrubTimer = useRef<number | null>(null);

  useEffect(
    () => () => {
      if (scrubTimer.current !== null) window.clearTimeout(scrubTimer.current);
    },
    [],
  );

  const known = duration > 0;
  const shown = scrub ?? current;
  const playedPct = known ? clamp((shown / duration) * 100, 0, 100) : 0;
  const bufferedPct = known ? clamp((buffered / duration) * 100, 0, 100) : 0;

  const onMove = (e: ReactMouseEvent<HTMLDivElement>): void => {
    const el = wrapRef.current;
    if (!el || !known) return;
    const rect = el.getBoundingClientRect();
    const x = clamp(e.clientX - rect.left, 0, rect.width);
    setHover({ x, width: rect.width, t: (x / rect.width) * duration });
  };

  return (
    <div
      ref={wrapRef}
      className="group/seek relative flex h-5 cursor-pointer items-center"
      onMouseMove={onMove}
      onMouseLeave={() => setHover(null)}
    >
      <div className="relative h-1 w-full rounded-full bg-white/25 transition-[height] duration-150 group-hover/seek:h-1.5">
        <div
          className="absolute inset-y-0 left-0 rounded-full bg-white/35"
          style={{ width: `${bufferedPct}%` }}
        />
        {hover && (
          <div
            className="absolute inset-y-0 left-0 rounded-full bg-white/20"
            style={{ width: `${clamp((hover.x / hover.width) * 100, 0, 100)}%` }}
          />
        )}
        <div
          className="absolute inset-y-0 left-0 rounded-full bg-violet-600"
          style={{ width: `${playedPct}%` }}
        />
        <div
          className="absolute top-1/2 h-3.5 w-3.5 -translate-x-1/2 -translate-y-1/2 scale-0 rounded-full bg-violet-500 shadow-md ring-2 ring-white/90 transition-transform duration-150 group-hover/seek:scale-100"
          style={{ left: `${playedPct}%` }}
        />
      </div>
      {hover && known && (
        <div
          className="pointer-events-none absolute bottom-full mb-2 -translate-x-1/2 rounded-md bg-gray-950/90 px-2 py-0.5 text-[11px] font-semibold tabular-nums text-white shadow"
          style={{ left: clamp(hover.x, 22, Math.max(22, hover.width - 22)) }}
        >
          {formatClock(hover.t)}
        </div>
      )}
      <input
        type="range"
        min={0}
        max={known ? duration : 0}
        step={0.1}
        value={known ? clamp(shown, 0, duration) : 0}
        disabled={!known}
        aria-label="Seek"
        aria-valuetext={`${formatClock(shown)} of ${formatClock(duration)}`}
        onChange={(e) => {
          const v = Number(e.target.value);
          if (!Number.isFinite(v)) return;
          setScrub(v);
          if (scrubTimer.current !== null) window.clearTimeout(scrubTimer.current);
          scrubTimer.current = window.setTimeout(() => setScrub(null), 700);
          onSeek(v);
        }}
        className="absolute inset-0 h-full w-full cursor-pointer opacity-0 disabled:cursor-default"
      />
    </div>
  );
}

/* ----------------------------------------------------------------- frame */

interface PlayerFrameProps {
  state: PlayerSnapshot;
  commands: PlayerCommands;
  /** False hides all custom controls (e.g. YouTube API unavailable). */
  controlsEnabled?: boolean;
  label: string;
  /** The media element(s). Rendered to fill the frame. */
  children: ReactNode;
}

export default function PlayerFrame({
  state,
  commands,
  controlsEnabled = true,
  label,
  children,
}: PlayerFrameProps) {
  const rootRef = useRef<HTMLDivElement | null>(null);
  const hideTimer = useRef<number | null>(null);
  const [active, setActive] = useState(true);
  const [menuOpen, setMenuOpen] = useState(false);
  const [ownFullscreen, setOwnFullscreen] = useState(false);
  const immersive = useContext(ImmersiveContext);
  const isFullscreen = immersive ? immersive.active : ownFullscreen;

  const poke = useCallback((): void => {
    setActive(true);
    if (hideTimer.current !== null) window.clearTimeout(hideTimer.current);
    hideTimer.current = window.setTimeout(() => setActive(false), HIDE_AFTER_MS);
  }, []);

  useEffect(
    () => () => {
      if (hideTimer.current !== null) window.clearTimeout(hideTimer.current);
    },
    [],
  );

  useEffect(() => {
    const onChange = (): void => {
      setOwnFullscreen(
        Boolean(rootRef.current) && document.fullscreenElement === rootRef.current,
      );
    };
    document.addEventListener("fullscreenchange", onChange);
    return () => document.removeEventListener("fullscreenchange", onChange);
  }, []);

  const toggleFullscreen = useCallback((): void => {
    if (immersive) {
      immersive.toggle();
      return;
    }
    const el = rootRef.current;
    if (!el) return;
    if (document.fullscreenElement) {
      void document.exitFullscreen().catch(() => undefined);
    } else if (typeof el.requestFullscreen === "function") {
      void el.requestFullscreen().catch(() => undefined);
    }
  }, [immersive]);

  const togglePlay = useCallback((): void => {
    if (state.playing) commands.pause();
    else commands.play();
  }, [state.playing, commands]);

  const seekBy = useCallback(
    (delta: number): void => {
      const max = state.duration > 0 ? state.duration : Number.POSITIVE_INFINITY;
      commands.seek(clamp(state.currentTime + delta, 0, max));
    },
    [state.currentTime, state.duration, commands],
  );

  const onKeyDown = (e: ReactKeyboardEvent<HTMLDivElement>): void => {
    if (!controlsEnabled || e.altKey || e.ctrlKey || e.metaKey) return;
    const target = e.target as HTMLElement;
    const tag = target.tagName;
    // Sliders handle their own arrows; text fields are never hijacked.
    if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
    const key = e.key.length === 1 ? e.key.toLowerCase() : e.key;
    let handled = true;
    if (key === " " || key === "Spacebar") {
      // A focused button activates itself on Space; don't double-toggle.
      if (tag === "BUTTON") return;
      togglePlay();
    } else if (key === "k") {
      togglePlay();
    } else if (key === "ArrowLeft") {
      seekBy(-5);
    } else if (key === "ArrowRight") {
      seekBy(5);
    } else if (key === "j") {
      seekBy(-10);
    } else if (key === "l") {
      seekBy(10);
    } else if (key === "m") {
      commands.setMuted(!state.muted);
    } else if (key === "f") {
      toggleFullscreen();
    } else if (key === "c" && immersive?.active) {
      immersive.toggleChat();
    } else if (key === "Escape" && menuOpen) {
      setMenuOpen(false);
    } else {
      handled = false;
    }
    if (handled) {
      e.preventDefault();
      poke();
    }
  };

  const visible = controlsEnabled && (!state.playing || active || menuOpen);
  const effectiveVolume = state.muted ? 0 : state.volume;

  return (
    <div
      ref={rootRef}
      tabIndex={0}
      role="region"
      aria-label={label}
      onKeyDown={onKeyDown}
      onMouseMove={poke}
      onTouchStart={poke}
      onMouseLeave={() => {
        if (state.playing) setActive(false);
      }}
      className={`group/player relative isolate overflow-hidden bg-black outline-none focus-visible:ring-2 focus-visible:ring-violet-500 focus-visible:ring-offset-2 ${
        isFullscreen ? "flex h-full w-full items-center justify-center" : "rounded-2xl shadow-xl shadow-gray-900/10 ring-1 ring-gray-900/10"
      } ${controlsEnabled && !visible ? "cursor-none" : ""}`}
    >
      <div className={isFullscreen ? "h-full w-full" : "aspect-video w-full"}>{children}</div>

      {controlsEnabled && (
        <div
          className="absolute inset-0 z-10"
          aria-hidden="true"
          onClick={() => {
            if (menuOpen) {
              setMenuOpen(false);
              return;
            }
            togglePlay();
            poke();
          }}
          onDoubleClick={toggleFullscreen}
        />
      )}

      {state.waiting && (
        <div className="pointer-events-none absolute inset-0 z-20 flex items-center justify-center" role="status">
          <span className="h-12 w-12 animate-spin rounded-full border-[3px] border-white/25 border-t-violet-400" />
          <span className="sr-only">Loading video…</span>
        </div>
      )}

      {controlsEnabled && !state.playing && !state.waiting && (
        <button
          type="button"
          onClick={() => {
            commands.play();
            poke();
          }}
          aria-label="Play"
          className="absolute left-1/2 top-1/2 z-20 flex h-16 w-16 -translate-x-1/2 -translate-y-1/2 items-center justify-center rounded-full bg-violet-600/90 text-white shadow-2xl shadow-violet-900/40 ring-4 ring-white/15 backdrop-blur transition hover:scale-105 hover:bg-violet-600 focus:outline-none focus-visible:ring-violet-300 sm:h-20 sm:w-20"
        >
          <PlayIcon className="ml-1 h-7 w-7 sm:h-9 sm:w-9" />
        </button>
      )}

      {controlsEnabled && (
        <div
          className={`absolute inset-x-0 bottom-0 z-30 transition-opacity duration-300 ${
            visible ? "opacity-100" : "pointer-events-none opacity-0"
          }`}
        >
          <div className="pointer-events-none absolute inset-x-0 bottom-0 h-32 bg-gradient-to-t from-black/85 via-black/45 to-transparent" />
          <div className="relative px-3 pb-2 sm:px-4 sm:pb-3">
            <SeekBar
              current={state.currentTime}
              duration={state.duration}
              buffered={state.buffered}
              onSeek={(s) => {
                commands.seek(s);
                poke();
              }}
            />
            <div className="mt-0.5 flex items-center gap-1 text-white">
              <IconButton label={state.playing ? "Pause (k)" : "Play (k)"} onClick={togglePlay}>
                {state.playing ? <PauseIcon /> : <PlayIcon />}
              </IconButton>

              <div className="group/vol flex items-center">
                <IconButton
                  label={state.muted ? "Unmute (m)" : "Mute (m)"}
                  pressed={state.muted}
                  onClick={() => commands.setMuted(!state.muted)}
                >
                  {effectiveVolume === 0 ? (
                    <VolumeMuteIcon />
                  ) : effectiveVolume < 0.5 ? (
                    <VolumeLowIcon />
                  ) : (
                    <VolumeHighIcon />
                  )}
                </IconButton>
                <input
                  type="range"
                  min={0}
                  max={1}
                  step={0.05}
                  value={effectiveVolume}
                  aria-label="Volume"
                  onChange={(e) => {
                    const v = clamp(Number(e.target.value), 0, 1);
                    commands.setVolume(v);
                    if (v > 0 && state.muted) commands.setMuted(false);
                    if (v === 0 && !state.muted) commands.setMuted(true);
                  }}
                  className="h-1 w-0 cursor-pointer accent-violet-500 opacity-0 transition-all duration-200 focus:w-20 focus:opacity-100 group-hover/vol:w-20 group-hover/vol:opacity-100"
                />
              </div>

              <span className="ml-2 select-none text-xs font-medium tabular-nums text-white/90">
                {formatClock(state.currentTime)}
                <span className="mx-1 text-white/50">/</span>
                <span className="text-white/70">{formatClock(state.duration)}</span>
              </span>

              <div className="ml-auto flex items-center gap-1">
                <div className="relative">
                  <button
                    type="button"
                    onClick={() => setMenuOpen((o) => !o)}
                    aria-haspopup="menu"
                    aria-expanded={menuOpen}
                    aria-label={`Playback speed ${state.rate}x`}
                    title="Playback speed"
                    className="h-9 min-w-[3rem] rounded-lg px-2 text-xs font-semibold tabular-nums text-white/90 transition hover:bg-white/15 hover:text-white focus:outline-none focus-visible:ring-2 focus-visible:ring-violet-400"
                  >
                    {state.rate}x
                  </button>
                  {menuOpen && (
                    <div
                      role="menu"
                      aria-label="Playback speed"
                      className="absolute bottom-full right-0 mb-2 w-32 overflow-hidden rounded-xl border border-white/10 bg-gray-950/95 py-1 shadow-2xl backdrop-blur"
                    >
                      <p className="px-3 pb-1 pt-1.5 text-[10px] font-semibold uppercase tracking-wider text-white/40">
                        Speed
                      </p>
                      {PLAYBACK_RATES.map((r) => {
                        const current = Math.abs(state.rate - r) < 0.001;
                        return (
                          <button
                            key={r}
                            type="button"
                            role="menuitemradio"
                            aria-checked={current}
                            onClick={() => {
                              commands.setRate(r);
                              setMenuOpen(false);
                            }}
                            className={`flex w-full items-center justify-between px-3 py-1.5 text-left text-xs tabular-nums transition ${
                              current
                                ? "bg-violet-600/30 font-semibold text-white"
                                : "text-white/80 hover:bg-white/10 hover:text-white"
                            }`}
                          >
                            {r === 1 ? "Normal" : `${r}x`}
                            {current && <span className="h-1.5 w-1.5 rounded-full bg-violet-400" />}
                          </button>
                        );
                      })}
                    </div>
                  )}
                </div>
                {immersive?.active && (
                  <IconButton
                    label={immersive.chatOpen ? "Hide chat (c)" : "Ask Malai (c)"}
                    pressed={immersive.chatOpen}
                    onClick={immersive.toggleChat}
                  >
                    <ChatIcon />
                  </IconButton>
                )}
                <IconButton
                  label={isFullscreen ? "Exit fullscreen (f)" : "Fullscreen (f)"}
                  onClick={toggleFullscreen}
                >
                  {isFullscreen ? <ExitFullscreenIcon /> : <FullscreenIcon />}
                </IconButton>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
