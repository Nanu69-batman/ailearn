"use client";

import type { RefObject } from "react";
import styles from "./VideoPlayer.module.css";

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
    <div className={styles.wrapper}>
      <video
        ref={videoRef}
        className={styles.video}
        src={src}
        controls
        preload="metadata"
        onPause={(e) => onPauseAt(toMs(e.currentTarget.currentTime))}
        onTimeUpdate={(e) => onTimeChange?.(toMs(e.currentTarget.currentTime))}
        aria-label="Course video player"
      />
      <p className={styles.hint}>
        Pause the video, then ask a question — the chat uses your pause position
        as context.
      </p>
    </div>
  );
}
