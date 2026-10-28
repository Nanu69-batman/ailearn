"use client";

import { useEffect, useState } from "react";

/**
 * Which models answer the learner (picked under the chat input) and their own
 * OpenRouter key, if they brought one. Both stay in this browser and go to the
 * API as request headers (see apps/api/app/chat/models.py); the key is never
 * stored on the server.
 */
export type ModelMode = "auto" | "fast" | "balanced" | "powerful";

export const MODEL_MODES: { id: ModelMode; label: string; hint: string }[] = [
  { id: "auto", label: "Auto", hint: "Picks a free model for you" },
  { id: "fast", label: "Fast", hint: "Quick answers from lighter models" },
  { id: "balanced", label: "Balanced", hint: "Good answers at a good speed" },
  { id: "powerful", label: "Powerful", hint: "The strongest models, slower" },
];

export interface ModelSettings {
  mode: ModelMode;
  /** The learner's OpenRouter key, or "" to use the app's. */
  key: string;
}

const MODE_KEY = "malai:model-mode";
const BYOK_KEY = "malai:openrouter-key";

let cache: ModelSettings | null = null;
const listeners = new Set<(s: ModelSettings) => void>();

function isMode(v: unknown): v is ModelMode {
  return MODEL_MODES.some((m) => m.id === v);
}

export function loadModelSettings(): ModelSettings {
  if (cache) return cache;
  let mode: ModelMode = "auto";
  let key = "";
  try {
    if (typeof window !== "undefined") {
      const m = window.localStorage.getItem(MODE_KEY);
      if (isMode(m)) mode = m;
      key = window.localStorage.getItem(BYOK_KEY) ?? "";
    }
  } catch {
    // Storage unavailable: defaults for this page.
  }
  cache = { mode, key };
  return cache;
}

export function saveModelSettings(patch: Partial<ModelSettings>): void {
  const next = { ...loadModelSettings(), ...patch };
  next.key = next.key.trim();
  cache = next;
  try {
    window.localStorage.setItem(MODE_KEY, next.mode);
    if (next.key) window.localStorage.setItem(BYOK_KEY, next.key);
    else window.localStorage.removeItem(BYOK_KEY);
  } catch {
    // The choice lasts for this page only.
  }
  for (const fn of listeners) fn(next);
}

/** Headers that tell the API which models to use. */
export function modelHeaders(): Record<string, string> {
  if (typeof window === "undefined") return {};
  const { mode, key } = loadModelSettings();
  return key ? { "X-Model-Mode": mode, "X-OpenRouter-Key": key } : { "X-Model-Mode": mode };
}

export function useModelSettings(): ModelSettings {
  // Defaults on the server render; the stored choice after mount.
  const [s, setS] = useState<ModelSettings>({ mode: "auto", key: "" });
  useEffect(() => {
    setS(loadModelSettings());
    listeners.add(setS);
    return () => {
      listeners.delete(setS);
    };
  }, []);
  return s;
}
