"use client";

import { useEffect, useState } from "react";

/**
 * Which models answer the learner, picked under the chat input. The four
 * modes are free models on the app's key; under Advanced the learner can
 * instead use their own key for any provider and one of its models
 * ("custom"). Everything stays in this browser and goes to the API as
 * request headers (apps/api/app/chat/models.py); the key is never stored on
 * the server.
 */
export type ModelMode = "auto" | "fast" | "balanced" | "powerful" | "custom";

export const MODEL_MODES: { id: Exclude<ModelMode, "custom">; label: string; hint: string }[] = [
  { id: "auto", label: "Auto", hint: "Picks a free model for you" },
  { id: "fast", label: "Fast", hint: "Quick answers from lighter models" },
  { id: "balanced", label: "Balanced", hint: "Good answers at a good speed" },
  { id: "powerful", label: "Powerful", hint: "The strongest free models, slower" },
];

/** Same ids as PROVIDERS in apps/api/app/chat/models.py. */
export const PROVIDERS: { id: string; label: string; placeholder: string }[] = [
  { id: "openrouter", label: "OpenRouter", placeholder: "sk-or-v1-…" },
  { id: "openai", label: "OpenAI", placeholder: "sk-…" },
  { id: "anthropic", label: "Anthropic", placeholder: "sk-ant-…" },
  { id: "google", label: "Google Gemini", placeholder: "AIza…" },
  { id: "groq", label: "Groq", placeholder: "gsk_…" },
  { id: "mistral", label: "Mistral", placeholder: "Your Mistral key" },
  { id: "deepseek", label: "DeepSeek", placeholder: "sk-…" },
  { id: "xai", label: "xAI", placeholder: "xai-…" },
];

export interface ModelSettings {
  mode: ModelMode;
  /** The learner's own provider, key and chosen model ("custom" mode). */
  provider: string;
  key: string;
  model: string;
  modelName: string;
}

const STORE_KEY = "malai:model-settings";
const DEFAULTS: ModelSettings = { mode: "auto", provider: "openrouter", key: "", model: "", modelName: "" };

let cache: ModelSettings | null = null;
const listeners = new Set<(s: ModelSettings) => void>();

function clean(raw: unknown): ModelSettings {
  const r = (raw && typeof raw === "object" ? raw : {}) as Partial<ModelSettings>;
  const str = (v: unknown) => (typeof v === "string" ? v.trim() : "");
  const modes: ModelMode[] = ["auto", "fast", "balanced", "powerful", "custom"];
  const s: ModelSettings = {
    mode: modes.includes(r.mode as ModelMode) ? (r.mode as ModelMode) : "auto",
    provider: PROVIDERS.some((p) => p.id === r.provider) ? (r.provider as string) : "openrouter",
    key: str(r.key),
    model: str(r.model),
    modelName: str(r.modelName),
  };
  // Custom needs a key and a model; without them it is Auto.
  if (s.mode === "custom" && !(s.key && s.model)) s.mode = "auto";
  return s;
}

export function loadModelSettings(): ModelSettings {
  if (cache) return cache;
  try {
    const raw = typeof window !== "undefined" ? window.localStorage.getItem(STORE_KEY) : null;
    cache = raw ? clean(JSON.parse(raw)) : { ...DEFAULTS };
  } catch {
    cache = { ...DEFAULTS };
  }
  return cache;
}

export function saveModelSettings(patch: Partial<ModelSettings>): void {
  const next = clean({ ...loadModelSettings(), ...patch });
  cache = next;
  try {
    window.localStorage.setItem(STORE_KEY, JSON.stringify(next));
  } catch {
    // The choice lasts for this page only.
  }
  for (const fn of listeners) fn(next);
}

/** Headers that tell the API which models to use. */
export function modelHeaders(): Record<string, string> {
  if (typeof window === "undefined") return {};
  return modelHeadersFor(loadModelSettings().mode);
}

/** Headers for `mode`; "custom" uses the saved key and model (Auto without them). */
export function modelHeadersFor(mode: ModelMode): Record<string, string> {
  const s = loadModelSettings();
  if (mode !== "custom" || !(s.key && s.model)) return { "X-Model-Mode": mode === "custom" ? "auto" : mode };
  return { "X-Model-Mode": "custom", "X-LLM-Provider": s.provider, "X-LLM-Key": s.key, "X-LLM-Model": s.model };
}

/**
 * Which model writes a quiz, picked in the quiz form. "default" is the app's
 * fastest provider; any other value is a chat mode (or the saved own key)
 * tried first. A quiz asked for in the chat uses the chat's own choice.
 */
export type QuizModel = "default" | ModelMode;

const QUIZ_STORE_KEY = "malai:quiz-model";

export function loadQuizModel(): QuizModel {
  try {
    const raw = window.localStorage.getItem(QUIZ_STORE_KEY);
    const ok: QuizModel[] = ["default", "auto", "fast", "balanced", "powerful", "custom"];
    return ok.includes(raw as QuizModel) ? (raw as QuizModel) : "default";
  } catch {
    return "default";
  }
}

export function saveQuizModel(model: QuizModel): void {
  try {
    window.localStorage.setItem(QUIZ_STORE_KEY, model);
  } catch {
    // The choice lasts for this page only.
  }
}

export function useModelSettings(): ModelSettings {
  // Defaults on the server render; the stored choice after mount.
  const [s, setS] = useState<ModelSettings>(DEFAULTS);
  useEffect(() => {
    setS(loadModelSettings());
    listeners.add(setS);
    return () => {
      listeners.delete(setS);
    };
  }, []);
  return s;
}
