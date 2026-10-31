"use client";

import { useEffect, useMemo, useState, type ReactNode } from "react";
import Link from "next/link";
import Select from "@/components/Select";
import PersonalizationModal from "@/components/PersonalizationModal";
import { formatApiError, listProviderModels, type ProviderModel } from "@/lib/api";
import {
  MODEL_MODES,
  PROVIDERS,
  loadQuizModel,
  saveModelSettings,
  saveQuizModel,
  useModelSettings,
  type ModelMode,
  type QuizModel,
} from "@/lib/modelMode";
import { useProfile } from "@/lib/profile";

function Section({ title, hint, children }: { title: string; hint?: string; children: ReactNode }) {
  return (
    <section className="rounded-2xl border border-gray-200 bg-white p-5 shadow-sm">
      <h2 className="text-base font-semibold text-gray-900">{title}</h2>
      {hint && <p className="mt-0.5 text-sm text-gray-500">{hint}</p>}
      <div className="mt-4">{children}</div>
    </section>
  );
}

const field =
  "w-full rounded-xl border border-gray-200 bg-white px-3 py-2 text-sm text-gray-900 outline-none transition placeholder:text-gray-400 focus:border-violet-400 focus:ring-4 focus:ring-violet-100";

export default function SettingsView() {
  const settings = useModelSettings();
  const profile = useProfile();
  const [provider, setProvider] = useState(settings.provider);
  const [key, setKey] = useState(settings.key);
  const [showKey, setShowKey] = useState(false);
  const [models, setModels] = useState<ProviderModel[] | null>(null);
  const [filter, setFilter] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [quizModel, setQuizModel] = useState<QuizModel>("default");
  const [personalizing, setPersonalizing] = useState(false);

  // The stored values arrive after mount (and again when the account's copy is pulled).
  useEffect(() => {
    setProvider(settings.provider);
    setKey(settings.key);
  }, [settings.provider, settings.key]);
  useEffect(() => setQuizModel(loadQuizModel()), [settings]);

  const hasOwnKey = Boolean(settings.key && settings.model);
  const placeholder = PROVIDERS.find((p) => p.id === provider)?.placeholder ?? "API key";
  const shown = useMemo(() => {
    const q = filter.trim().toLowerCase();
    const list = models ?? [];
    return q ? list.filter((m) => m.id.toLowerCase().includes(q) || m.name.toLowerCase().includes(q)) : list;
  }, [models, filter]);

  async function loadModels(): Promise<void> {
    if (!key.trim()) return;
    setLoading(true);
    setError(null);
    try {
      const list = await listProviderModels(provider, key.trim());
      setModels(list);
      if (list.length === 0) setError("No chat models found on this key.");
    } catch (err) {
      setModels(null);
      setError(formatApiError(err));
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="mx-auto flex w-full max-w-3xl flex-col gap-5 pb-10">
      <header>
        <h1 className="text-2xl font-bold tracking-tight text-gray-900">Settings</h1>
        <p className="mt-1 text-sm text-gray-500">Saved to your account, so they follow you to any device you sign in on.</p>
      </header>

      <Section title="Chat model" hint="Which models answer you in Malai and the lesson chat.">
        <div className="grid gap-2 sm:grid-cols-2">
          {MODEL_MODES.map((m) => {
            const on = settings.mode === m.id;
            return (
              <button
                key={m.id}
                type="button"
                onClick={() => saveModelSettings({ mode: m.id as ModelMode })}
                aria-pressed={on}
                className={`rounded-xl border px-4 py-3 text-left transition ${
                  on ? "border-violet-400 bg-violet-50 ring-2 ring-violet-200" : "border-gray-200 hover:border-violet-200"
                }`}
              >
                <span className="block text-sm font-medium text-gray-900">{m.label}</span>
                <span className="block text-xs text-gray-500">{m.hint}</span>
              </button>
            );
          })}
          {hasOwnKey && (
            <button
              type="button"
              onClick={() => saveModelSettings({ mode: "custom" })}
              aria-pressed={settings.mode === "custom"}
              className={`rounded-xl border px-4 py-3 text-left transition sm:col-span-2 ${
                settings.mode === "custom" ? "border-violet-400 bg-violet-50 ring-2 ring-violet-200" : "border-gray-200 hover:border-violet-200"
              }`}
            >
              <span className="block text-sm font-medium text-gray-900">Own key</span>
              <span className="block text-xs text-gray-500">
                {PROVIDERS.find((p) => p.id === settings.provider)?.label} · {settings.modelName || settings.model}
              </span>
            </button>
          )}
        </div>
      </Section>

      <Section
        title="Your own API key"
        hint="Use any provider's model with your own key. It is saved to your account (only you can read it) and sent to the API with each request, never stored there."
      >
        <div className="flex flex-col gap-3">
          <div className="flex flex-col gap-2 sm:flex-row">
            <Select
              label="Provider"
              value={provider}
              onChange={(v) => {
                setProvider(v);
                setModels(null);
                setError(null);
              }}
              options={PROVIDERS.map((p) => ({ value: p.id, label: p.label }))}
              className="sm:w-48"
            />
            <div className="relative flex-1">
              <input
                type={showKey ? "text" : "password"}
                autoComplete="off"
                spellCheck={false}
                value={key}
                onChange={(e) => {
                  setKey(e.target.value);
                  setModels(null);
                }}
                onKeyDown={(e) => {
                  if (e.key === "Enter") void loadModels();
                }}
                placeholder={placeholder}
                aria-label="API key"
                className={`${field} pr-16`}
              />
              <button
                type="button"
                onClick={() => setShowKey((v) => !v)}
                className="absolute right-2 top-1/2 -translate-y-1/2 rounded-lg px-2 py-1 text-xs text-gray-500 hover:bg-gray-100"
              >
                {showKey ? "Hide" : "Show"}
              </button>
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <button
              type="button"
              onClick={() => void loadModels()}
              disabled={!key.trim() || loading}
              className="inline-flex items-center gap-2 rounded-xl bg-violet-600 px-4 py-2 text-sm font-medium text-white hover:bg-violet-700 disabled:opacity-50"
            >
              {loading && <span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-white border-t-transparent" aria-hidden="true" />}
              {loading ? "Loading models…" : "Show models on this key"}
            </button>
            {settings.key && (
              <button
                type="button"
                onClick={() => {
                  saveModelSettings({ key: "", model: "", modelName: "", mode: settings.mode === "custom" ? "auto" : settings.mode });
                  setKey("");
                  setModels(null);
                }}
                className="rounded-xl border border-gray-200 px-3 py-2 text-sm text-gray-600 hover:border-red-200 hover:bg-red-50 hover:text-red-700"
              >
                Remove saved key
              </button>
            )}
          </div>
          {error && <p role="alert" className="text-sm text-red-600">{error}</p>}
          {models && models.length > 0 && (
            <div className="flex flex-col gap-2">
              <input
                type="search"
                value={filter}
                onChange={(e) => setFilter(e.target.value)}
                placeholder={`Search ${models.length} models`}
                aria-label="Search models"
                className={field}
              />
              <ul className="max-h-64 overflow-y-auto rounded-xl border border-gray-100">
                {shown.map((m) => {
                  const on = settings.mode === "custom" && settings.model === m.id && settings.provider === provider;
                  return (
                    <li key={m.id}>
                      <button
                        type="button"
                        onClick={() =>
                          saveModelSettings({ mode: "custom", provider, key: key.trim(), model: m.id, modelName: m.name })
                        }
                        className={`flex w-full items-center justify-between gap-2 px-3 py-2 text-left text-sm hover:bg-gray-50 ${on ? "bg-violet-50" : ""}`}
                      >
                        <span className="min-w-0">
                          <span className="block truncate font-medium text-gray-900">{m.name}</span>
                          <span className="block truncate text-xs text-gray-500">{m.id}</span>
                        </span>
                        {on && <span className="shrink-0 text-xs font-medium text-violet-700">In use</span>}
                      </button>
                    </li>
                  );
                })}
              </ul>
            </div>
          )}
        </div>
      </Section>

      <Section title="Quizzes" hint="The model that writes a quiz from the quiz form. A quiz asked for in the chat uses the chat model.">
        <Select<QuizModel>
          label="Quiz model"
          value={quizModel === "custom" && !hasOwnKey ? "default" : quizModel}
          onChange={(v) => {
            setQuizModel(v);
            saveQuizModel(v);
          }}
          options={[
            { value: "default", label: "Default (fastest)" },
            ...MODEL_MODES.map((m) => ({ value: m.id as QuizModel, label: `${m.label} · ${m.hint}` })),
            ...(hasOwnKey ? [{ value: "custom" as QuizModel, label: `Own key · ${settings.modelName || settings.model}` }] : []),
          ]}
          className="sm:w-80"
        />
      </Section>

      <Section title="Personalization" hint="Your name, what you're studying, how Malai should answer.">
        <button
          type="button"
          onClick={() => setPersonalizing(true)}
          className="rounded-xl border border-violet-200 bg-white px-4 py-2 text-sm font-medium text-violet-700 hover:bg-violet-50"
        >
          Edit personalization
        </button>
      </Section>

      <Section title="Account">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <p className="text-sm text-gray-700">
            {profile ? (
              <>
                Signed in as <span className="font-medium text-gray-900">{profile.email}</span>
              </>
            ) : (
              "Loading…"
            )}
          </p>
          <Link href="/profile" className="rounded-xl border border-gray-200 px-4 py-2 text-sm text-gray-700 hover:bg-gray-50">
            Open profile
          </Link>
        </div>
      </Section>

      {personalizing && <PersonalizationModal onClose={() => setPersonalizing(false)} />}
    </div>
  );
}
