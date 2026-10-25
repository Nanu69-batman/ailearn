"use client";

import { useEffect, useState } from "react";
import { getChapters, type ResourceChapter } from "@/lib/api";

export type ChaptersState = "idle" | "loading" | "pending" | "ready" | "unavailable" | "error";

/** While chapters are being written, ask again this often, this many times. */
const POLL_MS = 10_000;
const MAX_POLLS = 30;

/**
 * The chapters of one resource. They are written on the server after
 * ingestion, so a fresh resource may answer "pending" for a while: this polls
 * until they are ready. `refreshKey` asks again when it changes (a partly
 * processed video that has been processed further gets new chapters).
 */
export function useChapters(resourceId: string | null, refreshKey: number | string = 0) {
  const [chapters, setChapters] = useState<ResourceChapter[]>([]);
  const [state, setState] = useState<ChaptersState>("idle");

  useEffect(() => {
    setChapters([]);
    setState(resourceId ? "loading" : "idle");
  }, [resourceId]);

  useEffect(() => {
    if (!resourceId) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let polls = 0;
    const load = async (): Promise<void> => {
      try {
        const res = await getChapters(resourceId);
        if (cancelled) return;
        setChapters(res.chapters);
        setState(res.status);
        if (res.status === "pending" && ++polls < MAX_POLLS) timer = setTimeout(() => void load(), POLL_MS);
      } catch {
        if (!cancelled) setState((s) => (s === "ready" ? s : "error"));
      }
    };
    void load();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [resourceId, refreshKey]);

  return { chapters, state };
}
