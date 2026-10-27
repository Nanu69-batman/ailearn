"use client";

import { useCallback, useEffect, useState } from "react";
import {
  formatApiError,
  getSubjects,
  isApiConfigured,
  type Subject,
} from "@/lib/api";
import { onSubjectsChanged } from "@/lib/subjectEvents";

export type SubjectsState = "loading" | "ready" | "error";

/**
 * Subjects straight from `GET /subjects`, shared by the dashboard and the
 * My subjects page. There is no built-in subject and no placeholder row: a
 * fabricated subject would send the user into a page with nothing behind it.
 */
export function useSubjects() {
  const apiLive = isApiConfigured();
  const [subjects, setSubjects] = useState<Subject[]>([]);
  const [state, setState] = useState<SubjectsState>("loading");
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async (): Promise<void> => {
    if (!apiLive) {
      // No backend to ask. Stated plainly rather than showing an empty grid
      // that reads like "this account has no subjects".
      setState("error");
      setError(
        "NEXT_PUBLIC_API_URL is not set, so subjects cannot be loaded from the API.",
      );
      return;
    }
    setState("loading");
    setError(null);
    try {
      const res = await getSubjects();
      setSubjects(Array.isArray(res.subjects) ? res.subjects : []);
      setState("ready");
    } catch (err) {
      setError(formatApiError(err));
      setState("error");
    }
  }, [apiLive]);

  useEffect(() => {
    void load();
  }, [load]);

  // New material was added somewhere on the page: its resource ids changed.
  useEffect(() => onSubjectsChanged(() => void load()), [load]);

  const added = useCallback((created: Subject): void => {
    setSubjects((prev) => [created, ...prev.filter((s) => s.id !== created.id)]);
    setState("ready");
  }, []);

  const removed = useCallback((id: string): void => {
    setSubjects((prev) => prev.filter((s) => s.id !== id));
  }, []);

  return { apiLive, subjects, state, error, load, added, removed };
}
