"use client";

import { useEffect } from "react";
import { addStudySeconds, recordVisit, type LastVisit } from "@/lib/activity";

/** How often the open lesson is credited with study time. */
const TICK_SECONDS = 30;

/**
 * Records the open lesson as the learner's last visit, and credits study time
 * while the tab is visible. Pass null to track nothing (e.g. the sample clip).
 */
export function useStudyTracker(visit: Omit<LastVisit, "at"> | null): void {
  const subjectId = visit?.subjectId ?? null;
  const subjectName = visit?.subjectName ?? null;
  const resourceId = visit?.resourceId ?? null;
  const title = visit?.title ?? null;

  useEffect(() => {
    if (!subjectId || !resourceId) return;
    recordVisit({ subjectId, subjectName, resourceId, title: title ?? resourceId });
  }, [subjectId, subjectName, resourceId, title]);

  useEffect(() => {
    if (!subjectId || !resourceId) return;
    const t = window.setInterval(() => {
      if (document.visibilityState === "visible") addStudySeconds(TICK_SECONDS);
    }, TICK_SECONDS * 1000);
    return () => window.clearInterval(t);
  }, [subjectId, resourceId]);
}
