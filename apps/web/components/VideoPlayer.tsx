"use client";

import { useCallback, useEffect, useMemo, useState, type RefObject } from "react";
import PlayerFrame, {
  INITIAL_SNAPSHOT,
  type PlayerCommands,
  type PlayerSnapshot,
} from "./PlayerControls";

interface VideoPlayerProps {
  src: string;
  videoRef: RefObject<HTMLVideoElement | null>;
  onPauseAt: (currentTimeMs: number) => void;
  onTimeChange?: (currentTimeMs: number) => void;
}

export function toMs(seconds: number): number {
  return Math.max(0, Math.floor(seconds * 1000));
}

/** End of the buffered range that contains the playhead (seconds). */
function bufferedEnd(el: HTMLVideoElement): number {
  const ranges = el.buffered;
  const t = el.currentTime;
  for (let i = 0; i < ranges.length; i++) {
    if (ranges.start(i) <= t + 0.5 && t <= ranges.end(i)) return ranges.end(i);
  }
  return ranges.length ? ranges.end(ranges.length - 1) : 0;
}

function readSnapshot(el: HTMLVideoElement): Omit<PlayerSnapshot, "waiting"> {
  return {
    playing: !el.paused && !el.ended,
    currentTime: el.currentTime,
    duration: Number.isFinite(el.duration) ? el.duration : 0,
    buffered: bufferedEnd(el),
    volume: el.volume,
    muted: el.muted,
    rate: el.playbackRate,
  };
}

/**
 * Uploaded-video player: a native <video> (no browser controls) wrapped in the
 * custom PlayerFrame chrome.
 *
 * Safety contract, unchanged from the browser-controls version:
 * - the parent owns `videoRef` and reads `currentTime` from the element
 *   itself (chat T, frame capture, position heartbeat) — nothing here caches
 *   or substitutes the playhead;
 * - `onPauseAt` fires from the element's own pause event, `onTimeChange` from
 *   its own timeupdate event, both with toMs(currentTime);
 * - every seek (custom controls, keyboard, citation click) sets
 *   `el.currentTime`, so the element's own seeked/timeupdate events fire.
 */
export default function VideoPlayer({
  src,
  videoRef,
  onPauseAt,
  onTimeChange,
}: VideoPlayerProps) {
  const [snap, setSnap] = useState<PlayerSnapshot>(INITIAL_SNAPSHOT);

  const refresh = useCallback(
    (el: HTMLVideoElement, waiting?: boolean): void => {
      setSnap((prev) => ({
        ...readSnapshot(el),
        waiting: waiting ?? prev.waiting,
      }));
    },
    [],
  );

  // New source: forget the previous video's state.
  useEffect(() => {
    setSnap(INITIAL_SNAPSHOT);
    const el = videoRef.current;
    if (el) refresh(el, false);
  }, [src, videoRef, refresh]);

  const commands = useMemo<PlayerCommands>(
    () => ({
      play: () => {
        const el = videoRef.current;
        if (el) void el.play().catch(() => undefined);
      },
      pause: () => videoRef.current?.pause(),
      seek: (seconds: number) => {
        const el = videoRef.current;
        if (!el || !Number.isFinite(seconds)) return;
        const max = Number.isFinite(el.duration) ? el.duration : seconds;
        el.currentTime = Math.min(max, Math.max(0, seconds));
      },
      setVolume: (v: number) => {
        const el = videoRef.current;
        if (el) el.volume = Math.min(1, Math.max(0, v));
      },
      setMuted: (m: boolean) => {
        const el = videoRef.current;
        if (el) el.muted = m;
      },
      setRate: (r: number) => {
        const el = videoRef.current;
        if (el) el.playbackRate = r;
      },
    }),
    [videoRef],
  );

  return (
    <PlayerFrame state={snap} commands={commands} label="Course video player">
      {/*
        crossOrigin="anonymous" is REQUIRED, not decorative.

        The media is served from the API origin (NEXT_PUBLIC_API_URL, e.g.
        http://localhost:8123) while this page is served from the web origin
        (http://localhost:3000) — so the video is CROSS-origin, and a canvas
        drawn from it is TAINTED: `toBlob()` throws a SecurityError and no frame
        can ever be captured. The API already answers the media request with
        `Access-Control-Allow-Origin: <web origin>` and honours Range requests,
        so `crossOrigin="anonymous"` both makes playback work (the CORS request
        is answered) and leaves the canvas readable.

        Setting it here, on the element, at render time, is what makes that
        work: the attribute has to be present when resource selection begins.
        Setting `el.crossOrigin` from an effect after mount would be too late.
      */}
      <video
        ref={videoRef}
        className="block h-full w-full bg-black object-contain"
        src={src}
        crossOrigin="anonymous"
        preload="metadata"
        playsInline
        tabIndex={-1}
        onPause={(e) => {
          onPauseAt(toMs(e.currentTarget.currentTime));
          refresh(e.currentTarget);
        }}
        onTimeUpdate={(e) => {
          onTimeChange?.(toMs(e.currentTarget.currentTime));
          refresh(e.currentTarget);
        }}
        onPlay={(e) => refresh(e.currentTarget)}
        onPlaying={(e) => refresh(e.currentTarget, false)}
        onWaiting={(e) => refresh(e.currentTarget, true)}
        onCanPlay={(e) => refresh(e.currentTarget, false)}
        onSeeking={(e) => refresh(e.currentTarget)}
        onSeeked={(e) => refresh(e.currentTarget, false)}
        onEnded={(e) => refresh(e.currentTarget, false)}
        onLoadedMetadata={(e) => refresh(e.currentTarget)}
        onDurationChange={(e) => refresh(e.currentTarget)}
        onProgress={(e) => refresh(e.currentTarget)}
        onVolumeChange={(e) => refresh(e.currentTarget)}
        onRateChange={(e) => refresh(e.currentTarget)}
        onError={(e) => refresh(e.currentTarget, false)}
        aria-label="Course video player"
      />
    </PlayerFrame>
  );
}
