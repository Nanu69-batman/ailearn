"use client";

/**
 * Loads a course's resource list for whichever route needs it.
 *
 * Both the hub and the player need the same list, and the player needs it
 * before it can resolve a deep-linked `resourceId`. Gating on `session` is
 * deliberate: the list is only requested once a session exists, exactly as the
 * single-route version did.
 */

import { useEffect, useState } from "react";
import {
  formatApiError,
  getCourseResources,
  type CourseResource,
  type CourseResourcesResponse,
} from "@/lib/api";

export type ResourcesState = "idle" | "loading" | "ready" | "error";

export interface CourseResources {
  resources: CourseResource[];
  state: ResourcesState;
  error: string | null;
}

/** Loads a resource list by opaque id. */
export type ResourceLoader = (id: string) => Promise<CourseResourcesResponse>;

export function useCourseResources({
  courseId,
  enabled,
  load = getCourseResources,
}: {
  courseId: string;
  enabled: boolean;
  /**
   * Which listing to read. Defaults to the course listing. A subject page
   * passes `getSubjectResources`, because the subject id is NOT a course id —
   * asking for `/courses/{subjectId}/resources` returns nothing and renders a
   * populated subject as empty.
   */
  load?: ResourceLoader;
}): CourseResources {
  const [resources, setResources] = useState<CourseResource[]>([]);
  const [state, setState] = useState<ResourcesState>("idle");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;
    async function run(): Promise<void> {
      setState("loading");
      setError(null);
      try {
        const res = await load(courseId);
        if (!cancelled) {
          setResources(res.resources ?? []);
          setState("ready");
        }
      } catch (err) {
        if (!cancelled) {
          setError(formatApiError(err));
          setState("error");
        }
      }
    }
    void run();
    return () => {
      cancelled = true;
    };
  }, [courseId, enabled, load]);

  return { resources, state, error };
}