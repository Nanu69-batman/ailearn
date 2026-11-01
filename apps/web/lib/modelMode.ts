"use client";

import { useEffect, useState } from "react";
import { getSupabaseBrowserClient } from "@/lib/supabase/client";

/**
 * Which models answer the learner, picked under the chat input.
 *
 * - Built-in modes on the app's keys: Gemini 3.5 Flash Lite (the default),
 *   then four modes of free OpenRouter models.
 * - Any number of the learner's own keys (BYOK), each for a provider. From
 *   each key the learner turns individual models on in Settings; only the
 *   modes and models that are turned on appear in the chooser.
 *
 * The chosen model goes to the API as request headers
 * (apps/api/app/chat/models.py): only the one key in use is sent, and the API
 * never stores it. For a signed-in learner the settings (keys included) are
 * also saved to their own Supabase row (`user_settings`, owner-only RLS) so
 * they follow the account.
 */
export type ModelMode = "gemini" | "auto" | "fast" | "balanced" | "powerful" | "custom";
export type BuiltInMode = Exclude<ModelMode, "custom">;

export const MODEL_MODES: { id: BuiltInMode; label: string; hint: string }[] = [
  { id: "gemini", label: "Gemini 3.5 Flash Lite", hint: "Google's fast model (default)" },
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

export const MAX_KEYS = 20;
export const MAX_ENABLED_MODELS = 100;

/** One of the learner's own API keys. */
export interface SavedKey {
  id: string;
  provider: string;
  /** The learner's name for it ("Work OpenRouter"); defaults to the provider. */
  label: string;
  key: string;
}

/** A model on one of those keys that the learner turned on. */
export interface EnabledModel {
  keyId: string;
  id: string;
  name: string;
  /** True/false where the provider publishes prices (OpenRouter); null = unknown. */
  free: boolean | null;
}

export interface ModelSettings {
  mode: ModelMode;
  keys: SavedKey[];
  enabled: EnabledModel[];
  /** Built-in modes the learner turned off (hidden in the chooser). */
  disabledModes: BuiltInMode[];
  /** The chooser's "Free only" filter. */
  freeOnly: boolean;
  /** The own-key model in use when `mode` is "custom". */
  activeKeyId: string;
  model: string;
  modelName: string;
  /** Derived from the active key, for callers that send it: not stored. */
  provider: string;
  key: string;
}

const STORE_KEY = "malai:model-settings";
/** Settings saved before this version defaulted to Auto; they move to Gemini once. */
const SETTINGS_VERSION = 2;
const DEFAULTS: ModelSettings = {
  mode: "gemini",
  keys: [],
  enabled: [],
  disabledModes: [],
  freeOnly: false,
  activeKeyId: "",
  model: "",
  modelName: "",
  provider: "",
  key: "",
};

let cache: ModelSettings | null = null;
const listeners = new Set<(s: ModelSettings) => void>();

export function newKeyId(): string {
  try {
    return `k_${crypto.randomUUID().slice(0, 8)}`;
  } catch {
    return `k_${Date.now().toString(36)}${Math.random().toString(36).slice(2, 6)}`;
  }
}

export function providerLabel(id: string): string {
  return PROVIDERS.find((p) => p.id === id)?.label ?? id;
}

/** "sk-or-…a1b2": enough to tell keys apart, never the whole key. */
export function maskKey(key: string): string {
  return key.length <= 8 ? "••••" : `${key.slice(0, 5)}…${key.slice(-4)}`;
}

const BUILT_IN_IDS = MODEL_MODES.map((m) => m.id);

function clean(raw: unknown): ModelSettings {
  const r = (raw && typeof raw === "object" ? raw : {}) as Record<string, unknown>;
  const str = (v: unknown, max = 500) => (typeof v === "string" ? v.trim().slice(0, max) : "");
  const isProvider = (v: unknown) => PROVIDERS.some((p) => p.id === v);

  let keys: SavedKey[] = Array.isArray(r.keys)
    ? (r.keys as unknown[])
        .map((k) => (k && typeof k === "object" ? (k as Record<string, unknown>) : {}))
        .filter((k) => str(k.id, 64) && isProvider(k.provider) && str(k.key))
        .map((k) => ({
          id: str(k.id, 64),
          provider: k.provider as string,
          label: str(k.label, 60) || providerLabel(k.provider as string),
          key: str(k.key),
        }))
    : [];
  let enabled: EnabledModel[] = Array.isArray(r.enabled)
    ? (r.enabled as unknown[])
        .map((m) => (m && typeof m === "object" ? (m as Record<string, unknown>) : {}))
        .filter((m) => str(m.keyId, 64) && str(m.id))
        .map((m) => ({
          keyId: str(m.keyId, 64),
          id: str(m.id),
          name: str(m.name) || str(m.id),
          free: typeof m.free === "boolean" ? m.free : null,
        }))
    : [];
  let activeKeyId = str(r.activeKeyId, 64);

  // Older builds kept one key: { provider, key, model, modelName }. Keep it.
  if (keys.length === 0 && str(r.key) && isProvider(r.provider)) {
    const id = "k_legacy";
    keys = [{ id, provider: r.provider as string, label: providerLabel(r.provider as string), key: str(r.key) }];
    if (str(r.model)) enabled = [{ keyId: id, id: str(r.model), name: str(r.modelName) || str(r.model), free: null }];
    activeKeyId = id;
  }

  keys = keys.slice(0, MAX_KEYS);
  // A model whose key is gone cannot be used; duplicates collapse.
  const seen = new Set<string>();
  enabled = enabled
    .filter((m) => keys.some((k) => k.id === m.keyId))
    .filter((m) => {
      const id = `${m.keyId}\n${m.id}`;
      if (seen.has(id)) return false;
      seen.add(id);
      return true;
    })
    .slice(0, MAX_ENABLED_MODELS);

  const disabledModes = Array.isArray(r.disabledModes)
    ? (r.disabledModes as unknown[]).filter((m): m is BuiltInMode => BUILT_IN_IDS.includes(m as BuiltInMode))
    : [];

  const modes: ModelMode[] = ["gemini", "auto", "fast", "balanced", "powerful", "custom"];
  let mode: ModelMode = modes.includes(r.mode as ModelMode) ? (r.mode as ModelMode) : "gemini";
  // Auto was the old default: settings from before Gemini existed start on Gemini.
  if (r.v !== SETTINGS_VERSION && mode === "auto") mode = "gemini";
  let model = str(r.model);
  let active = enabled.find((m) => m.keyId === activeKeyId && m.id === model);

  // The chosen option must still be turned on; otherwise the first one that is.
  const builtInOn = (m: ModelMode) => m !== "custom" && !disabledModes.includes(m);
  if (mode === "custom" ? !active : !builtInOn(mode)) {
    const firstMode = MODEL_MODES.find((m) => !disabledModes.includes(m.id));
    if (firstMode) {
      mode = firstMode.id;
      active = undefined;
    } else if (enabled[0]) {
      mode = "custom";
      active = enabled[0];
    } else {
      mode = "gemini"; // Nothing on at all: the default still answers.
      active = undefined;
    }
  }
  if (mode !== "custom") active = undefined;
  const activeKey = active ? keys.find((k) => k.id === active.keyId) : undefined;
  model = active?.id ?? "";

  return {
    mode,
    keys,
    enabled,
    disabledModes,
    freeOnly: r.freeOnly === true,
    activeKeyId: active?.keyId ?? "",
    model,
    modelName: active?.name ?? "",
    provider: activeKey?.provider ?? "",
    key: activeKey?.key ?? "",
  };
}

/** What is stored: the derived fields are left out. */
function stored(s: ModelSettings) {
  const { provider: _p, key: _k, ...rest } = s;
  void _p;
  void _k;
  return { ...rest, v: SETTINGS_VERSION };
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

function commit(next: ModelSettings): void {
  cache = next;
  try {
    window.localStorage.setItem(STORE_KEY, JSON.stringify(stored(next)));
  } catch {
    // The choice lasts for this page only.
  }
  for (const fn of listeners) fn(next);
  void pushSettings();
}

export function saveModelSettings(patch: Partial<ModelSettings>): void {
  commit(clean({ ...stored(loadModelSettings()), ...patch }));
}

// ---------------------------------------------------------------------------
// Keys and on/off switches.
// ---------------------------------------------------------------------------

/** Add a key (already checked against the provider). Returns its id. */
export function addKey(provider: string, key: string, label: string): string {
  const s = loadModelSettings();
  const existing = s.keys.find((k) => k.provider === provider && k.key === key.trim());
  if (existing) return existing.id;
  if (s.keys.length >= MAX_KEYS) throw new Error(`You can save up to ${MAX_KEYS} keys.`);
  const id = newKeyId();
  saveModelSettings({ keys: [...s.keys, { id, provider, key: key.trim(), label: label.trim() || providerLabel(provider) }] });
  return id;
}

export function renameKey(keyId: string, label: string): void {
  const s = loadModelSettings();
  saveModelSettings({
    keys: s.keys.map((k) => (k.id === keyId ? { ...k, label: label.trim() || providerLabel(k.provider) } : k)),
  });
}

/** Removes the key and every model turned on from it. */
export function removeKey(keyId: string): void {
  const s = loadModelSettings();
  saveModelSettings({ keys: s.keys.filter((k) => k.id !== keyId), enabled: s.enabled.filter((m) => m.keyId !== keyId) });
}

export function isModelEnabled(s: ModelSettings, keyId: string, modelId: string): boolean {
  return s.enabled.some((m) => m.keyId === keyId && m.id === modelId);
}

export function setModelEnabled(keyId: string, model: { id: string; name: string; free: boolean | null }, on: boolean): void {
  const s = loadModelSettings();
  const rest = s.enabled.filter((m) => !(m.keyId === keyId && m.id === model.id));
  if (on && rest.length >= MAX_ENABLED_MODELS) throw new Error(`You can turn on up to ${MAX_ENABLED_MODELS} models.`);
  saveModelSettings({ enabled: on ? [...rest, { keyId, id: model.id, name: model.name, free: model.free }] : rest });
}

export function setModeEnabled(mode: BuiltInMode, on: boolean): void {
  const s = loadModelSettings();
  const rest = s.disabledModes.filter((m) => m !== mode);
  saveModelSettings({ disabledModes: on ? rest : [...rest, mode] });
}

/** Use a built-in mode, or a model turned on from one of the keys. */
export function chooseOption(option: ModelOption): void {
  if (option.kind === "mode") saveModelSettings({ mode: option.mode });
  else saveModelSettings({ mode: "custom", activeKeyId: option.keyId, model: option.modelId });
}

// ---------------------------------------------------------------------------
// What the chooser shows.
// ---------------------------------------------------------------------------

export type ModelOption =
  | { kind: "mode"; value: string; mode: BuiltInMode; label: string; hint: string; free: true }
  | { kind: "own"; value: string; keyId: string; keyLabel: string; modelId: string; label: string; hint: string; free: boolean | null };

/** Every option that is turned on, built-in modes first, then per key. */
export function enabledOptions(s: ModelSettings): ModelOption[] {
  const out: ModelOption[] = MODEL_MODES.filter((m) => !s.disabledModes.includes(m.id)).map((m) => ({
    kind: "mode",
    value: m.id,
    mode: m.id,
    label: m.label,
    hint: m.hint,
    free: true,
  }));
  for (const k of s.keys) {
    for (const m of s.enabled.filter((e) => e.keyId === k.id)) {
      out.push({
        kind: "own",
        value: `own:${k.id}:${m.id}`,
        keyId: k.id,
        keyLabel: k.label,
        modelId: m.id,
        label: m.name,
        hint: m.name !== m.id ? m.id : providerLabel(k.provider),
        free: m.free,
      });
    }
  }
  return out;
}

export function isOptionActive(s: ModelSettings, o: ModelOption): boolean {
  return o.kind === "mode" ? s.mode === o.mode : s.mode === "custom" && s.activeKeyId === o.keyId && s.model === o.modelId;
}

// ---------------------------------------------------------------------------
// Account sync (supabase/migrations/0005_user_settings.sql).
// ---------------------------------------------------------------------------

async function signedIn() {
  const supabase = getSupabaseBrowserClient();
  if (!supabase) return null;
  try {
    const { data } = await supabase.auth.getSession();
    const userId = data.session?.user.id;
    return userId ? { supabase, userId } : null;
  } catch {
    return null;
  }
}

async function pushSettings(): Promise<void> {
  const s = await signedIn();
  if (!s) return;
  const { error } = await s.supabase.from("user_settings").upsert(
    { owner_id: s.userId, model: stored(loadModelSettings()), quiz_model: loadQuizModel(), updated_at: new Date().toISOString() },
    { onConflict: "owner_id" },
  );
  if (error) console.warn("settings sync failed", error);
}

let pulled = false;

/** The account's saved settings replace this browser's; none saved yet: upload these. */
export async function pullSettings(): Promise<void> {
  if (pulled) return;
  pulled = true;
  const s = await signedIn();
  if (!s) return;
  const { data, error } = await s.supabase
    .from("user_settings")
    .select("model, quiz_model")
    .eq("owner_id", s.userId)
    .maybeSingle();
  if (error) {
    console.warn("settings pull failed", error);
    return;
  }
  if (!data) {
    void pushSettings();
    return;
  }
  const next = clean(data.model);
  const quiz = cleanQuizModel(data.quiz_model);
  cache = next;
  try {
    window.localStorage.setItem(STORE_KEY, JSON.stringify(stored(next)));
    window.localStorage.setItem(QUIZ_STORE_KEY, quiz);
  } catch {
    // This page keeps the pulled copy.
  }
  for (const fn of listeners) fn(next);
  for (const fn of quizListeners) fn(quiz);
}

/** Headers that tell the API which models to use. */
export function modelHeaders(): Record<string, string> {
  if (typeof window === "undefined") return {};
  return modelHeadersFor(loadModelSettings().mode);
}

/** Headers for `mode`; "custom" sends only the key of the model in use (the default without one). */
export function modelHeadersFor(mode: ModelMode): Record<string, string> {
  const s = loadModelSettings();
  if (mode !== "custom" || !(s.key && s.model)) return { "X-Model-Mode": mode === "custom" ? "gemini" : mode };
  return { "X-Model-Mode": "custom", "X-LLM-Provider": s.provider, "X-LLM-Key": s.key, "X-LLM-Model": s.model };
}

/**
 * Which model writes a quiz, picked in the quiz form. "default" is the app's
 * Gemini 3.5 Flash Lite; a built-in mode, or any own-key model that is turned on
 * (`own:<keyId>:<modelId>`), is tried first. "custom" (older builds) means the
 * chat's own-key model in use. A quiz asked for in the chat uses the chat's
 * own choice.
 */
export type QuizModel = "default" | ModelMode | `own:${string}`;

const QUIZ_STORE_KEY = "malai:quiz-model";
const quizListeners = new Set<(q: QuizModel) => void>();

/** `own:<keyId>:<modelId>`; the model id may itself contain colons. */
export function parseOwnValue(v: string): { keyId: string; modelId: string } | null {
  if (!v.startsWith("own:")) return null;
  const rest = v.slice(4);
  const i = rest.indexOf(":");
  if (i <= 0 || i === rest.length - 1) return null;
  return { keyId: rest.slice(0, i), modelId: rest.slice(i + 1) };
}

function cleanQuizModel(raw: unknown): QuizModel {
  const ok: QuizModel[] = ["default", "gemini", "auto", "fast", "balanced", "powerful", "custom"];
  if (typeof raw !== "string") return "default";
  if (ok.includes(raw as QuizModel)) return raw as QuizModel;
  return parseOwnValue(raw) ? (raw as QuizModel) : "default";
}

export function loadQuizModel(): QuizModel {
  try {
    return cleanQuizModel(window.localStorage.getItem(QUIZ_STORE_KEY));
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
  for (const fn of quizListeners) fn(model);
  void pushSettings();
}

/** The quiz choice as it can be used now: one that is turned off falls back to "default". */
export function effectiveQuizModel(s: ModelSettings, q: QuizModel): QuizModel {
  if (q === "custom") return s.mode === "custom" && s.key && s.model ? q : "default";
  const own = parseOwnValue(q);
  if (own) return isModelEnabled(s, own.keyId, own.modelId) ? q : "default";
  if (q !== "default" && s.disabledModes.includes(q as BuiltInMode)) return "default";
  return q;
}

/** What the quiz model menus list: Default, the built-in modes on, then every own-key model on. */
export function quizModelOptions(s: ModelSettings): { value: QuizModel; label: string }[] {
  return [
    { value: "default", label: "Default · Gemini 3.5 Flash Lite" },
    ...enabledOptions(s).map((o) => ({
      value: o.value as QuizModel,
      label:
        o.kind === "mode"
          ? `${o.label} · ${o.hint}`
          : `${o.keyLabel} · ${o.label}${o.free === true ? " · Free" : ""}`,
    })),
  ];
}

/** Headers for a quiz written with `q`; undefined = the app's default. */
export function quizModelHeaders(q: QuizModel): Record<string, string> | undefined {
  const s = loadModelSettings();
  const use = effectiveQuizModel(s, q);
  if (use === "default") return undefined;
  const own = parseOwnValue(use);
  if (!own) return modelHeadersFor(use as ModelMode);
  const key = s.keys.find((k) => k.id === own.keyId);
  if (!key) return undefined;
  return { "X-Model-Mode": "custom", "X-LLM-Provider": key.provider, "X-LLM-Key": key.key, "X-LLM-Model": own.modelId };
}

/** The saved quiz model, kept in step across every open menu and an account pull. */
export function useQuizModel(): QuizModel {
  const [q, setQ] = useState<QuizModel>("default");
  useEffect(() => {
    setQ(loadQuizModel());
    quizListeners.add(setQ);
    return () => {
      quizListeners.delete(setQ);
    };
  }, []);
  return q;
}

export function useModelSettings(): ModelSettings {
  // Defaults on the server render; the stored choice after mount.
  const [s, setS] = useState<ModelSettings>(DEFAULTS);
  useEffect(() => {
    setS(loadModelSettings());
    listeners.add(setS);
    void pullSettings();
    return () => {
      listeners.delete(setS);
    };
  }, []);
  return s;
}
