"use client";

import type { RefObject } from "react";

interface VideoPlayerProps {
  src: string;
  videoRef: RefObject<HTMLVideoElement | null>;
  onPauseAt: (currentTimeMs: number) => void;
  onTimeChange?: (currentTimeMs: number) => void;
}

export function toMs(seconds: number): number {
  return Math.max(0, Math.floor(seconds * 1000));
}

export default function VideoPlayer({
  src,
  videoRef,
  onPauseAt,
  onTimeChange,
}: VideoPlayerProps) {
  return (
    <div className="overflow-hidden rounded-2xl border border-white/10 bg-black">
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
        className="aspect-video w-full"
        src={src}
        crossOrigin="anonymous"
        controls
        preload="metadata"
        onPause={(e) => onPauseAt(toMs(e.currentTarget.currentTime))}
        onTimeUpdate={(e) => onTimeChange?.(toMs(e.currentTarget.currentTime))}
        aria-label="Course video player"
      />
      <p className="bg-zinc-950/80 px-4 py-2 text-xs text-zinc-500">
        Pause the video, then ask a question — the chat uses your pause position
        as context.
      </p>
    </div>
  );
}
