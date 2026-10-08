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
      <video
        ref={videoRef}
        className="aspect-video w-full"
        src={src}
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
