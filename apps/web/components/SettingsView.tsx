"use client";

import { useEffect, useMemo, useState, type ReactNode } from "react";
import Link from "next/link";
import Select from "@/components/Select";
import PersonalizationModal from "@/components/PersonalizationModal";
import { FreeBadge } from "@/components/ModelModePicker";
import { formatApiError, listProviderModels, type ProviderModel } from "@/lib/api";
import {
  MAX_KEYS,
  MODEL_MODES,
  PROVIDERS,
  addKey,
  chooseOption,
  enabledOptions,
  isModelEnabled,
  isOptionActive,
  loadQuizModel,
  maskKey,
  providerLabel,
  removeKey,
  renameKey,
  saveQuizModel,
  setModeEnabled,
  setModelEnabled,
  useModelSettings,
  type QuizModel,
  type SavedKey,
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

function Switch({ on, onChange, label }: { on: boolean; onChange: (on: boolean) => void; label: string }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={on}
      aria-label={label}
      onClick={() => onChange(!on)}
      className={`relative inline-flex h-5 w-9 shrink-0 items-center rounded-full transition focus:outline-none focus-visible:ring-2 focus-visible:ring-violet-400 ${
        on ? "bg-violet-600" : "bg-gray-300"
      }`}
    >
      <span className={`inline-block h-4 w-4 rounded-full bg-white shadow transition ${on ? "translate-x-4" : "translate-x-0.5"}`} />
    </button>
  );
}

const TABS = [
  { id: "models", label: "Models", hint: "Chat, built-in and quiz models", icon: "M13 2L4 14h7l-1 8 9-12h-7l1-8z" },
  { id: "keys", label: "API keys", hint: "Your keys and their models", icon: "M15 7a2 2 0 012 2m4 0a6 6 0 01-7.743 5.743L11 17H9v2H7v2H4a1 1 0 01-1-1v-2.586a1 1 0 01.293-.707l5.964-5.964A6 6 0 1121 9z" },
  { id: "personalization", label: "Personalization", hint: "How Malai talks to you", icon: "M12 3l1.9 5.8H20l-4.9 3.6 1.9 5.8L12 14.6l-5 3.6 1.9-5.8L4 8.8h6.1z" },
  { id: "account", label: "Account", hint: "Sign-in and profile", icon: "M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z" },
] as const;
type TabId = (typeof TABS)[number]["id"];

function tabFromHash(): TabId {
  if (typeof window === "undefined") return "models";
  const h = window.location.hash.replace("#", "");
  return (TABS.find((t) => t.id === h)?.id ?? "models") as TabId;
}

const field =
  "w-full rounded-xl border border-gray-200 bg-white px-3 py-2 text-sm text-gray-900 outline-none transition placeholder:text-gray-400 focus:border-violet-400 focus:ring-4 focus:ring-violet-100";

type KeyModels = { state: "loading" } | { state: "error"; error: string } | { state: "ready"; models: ProviderModel[] };

/** One saved key: rename, remove, and its models with on/off switches. */
function KeyCard({
  k,
  models,
  onLoad,
}: {
  k: SavedKey;
  models: KeyModels | undefined;
  onLoad: () => void;
}) {
  const settings = useModelSettings();
  const [open, setOpen] = useState(false);
  const [filter, setFilter] = useState("");
  const [freeOnly, setFreeOnly] = useState(false);
  const [editing, setEditing] = useState(false);
  const [label, setLabel] = useState(k.label);
  const [error, setError] = useState<string | null>(null);

  const enabledHere = settings.enabled.filter((m) => m.keyId === k.id);
  // Before the list is loaded, the models already on can still be switched off.
  const list: { id: string; name: string; free: boolean | null }[] =
    models?.state === "ready" ? models.models : enabledHere;
  const shown = useMemo(() => {
    const q = filter.trim().toLowerCase();
    return list.filter(
      (m) => (!freeOnly || m.free === true) && (!q || m.id.toLowerCase().includes(q) || m.name.toLowerCase().includes(q)),
    );
  }, [list, filter, freeOnly]);
  const freeCount = models?.state === "ready" ? models.models.filter((m) => m.free === true).length : 0;

  function toggle(m: { id: string; name: string; free: boolean | null }, on: boolean) {
    try {
      setError(null);
      setModelEnabled(k.id, m, on);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  return (
    <li className="rounded-xl border border-gray-200">
      <div className="flex flex-wrap items-center gap-2 px-3 py-2.5">
        <div className="min-w-0 flex-1">
          {editing ? (
            <form
              onSubmit={(e) => {
                e.preventDefault();
                renameKey(k.id, label);
                setEditing(false);
              }}
              className="flex gap-2"
            >
              <input value={label} onChange={(e) => setLabel(e.target.value)} maxLength={60} aria-label="Key name" className={`${field} py-1`} autoFocus />
              <button type="submit" className="rounded-lg bg-violet-600 px-3 text-xs font-medium text-white hover:bg-violet-700">Save</button>
            </form>
          ) : (
            <>
              <p className="truncate text-sm font-medium text-gray-900">{k.label}</p>
              <p className="truncate text-xs text-gray-500">
                {providerLabel(k.provider)} · <span className="font-mono">{maskKey(k.key)}</span> · {enabledHere.length} on
              </p>
            </>
          )}
        </div>
        {!editing && (
          <>
            <button
              type="button"
              onClick={() => {
                const next = !open;
                setOpen(next);
                if (next && !models) onLoad();
              }}
              aria-expanded={open}
              className="rounded-lg border border-violet-200 px-3 py-1.5 text-xs font-medium text-violet-700 hover:bg-violet-50"
            >
              {open ? "Hide models" : "Choose models"}
            </button>
            <button
              type="button"
              onClick={() => {
                setLabel(k.label);
                setEditing(true);
              }}
              className="rounded-lg px-2 py-1.5 text-xs text-gray-600 hover:bg-gray-100"
            >
              Rename
            </button>
            <button
              type="button"
              onClick={() => {
                if (window.confirm(`Remove "${k.label}"? Its ${enabledHere.length} model(s) will be turned off.`)) removeKey(k.id);
              }}
              className="rounded-lg px-2 py-1.5 text-xs text-red-600 hover:bg-red-50"
            >
              Remove
            </button>
          </>
        )}
      </div>

      {open && (
        <div className="flex flex-col gap-2 border-t border-gray-100 px-3 py-3">
          <div className="flex flex-wrap items-center gap-2">
            <input
              type="search"
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
              placeholder={models?.state === "ready" ? `Search ${models.models.length} models` : "Search"}
              aria-label="Search models"
              className={`${field} min-w-0 flex-1 py-1.5`}
            />
            <button
              type="button"
              aria-pressed={freeOnly}
              onClick={() => setFreeOnly((v) => !v)}
              className={`rounded-full border px-3 py-1.5 text-xs font-medium transition ${
                freeOnly ? "border-emerald-300 bg-emerald-50 text-emerald-700" : "border-gray-200 text-gray-600 hover:border-emerald-200"
              }`}
            >
              {freeOnly ? "✓ Free only" : "Free only"}
              {models?.state === "ready" && ` (${freeCount})`}
            </button>
            <button type="button" onClick={onLoad} className="rounded-lg px-2 py-1.5 text-xs text-gray-600 hover:bg-gray-100">
              {models?.state === "loading" ? "Loading…" : "Reload"}
            </button>
          </div>
          {k.provider !== "openrouter" && (
            <p className="text-[11px] text-gray-500">{providerLabel(k.provider)} doesn&apos;t publish prices, so its models can&apos;t be marked free.</p>
          )}
          {models?.state === "error" && <p role="alert" className="text-xs text-red-600">{models.error}</p>}
          {error && <p role="alert" className="text-xs text-red-600">{error}</p>}
          <ul className="max-h-72 overflow-y-auto rounded-lg border border-gray-100">
            {shown.map((m) => {
              const on = isModelEnabled(settings, k.id, m.id);
              return (
                <li key={m.id} className="flex items-center gap-3 px-3 py-2 hover:bg-gray-50">
                  <span className="min-w-0 flex-1">
                    <span className="flex items-center gap-1.5">
                      <span className="truncate text-sm font-medium text-gray-900">{m.name}</span>
                      <FreeBadge free={m.free} />
                    </span>
                    {m.name !== m.id && <span className="block truncate text-xs text-gray-500">{m.id}</span>}
                  </span>
                  <Switch on={on} onChange={(v) => toggle(m, v)} label={`${m.name} ${on ? "on" : "off"}`} />
                </li>
              );
            })}
            {models?.state === "loading" && list.length === 0 && <li className="px-3 py-3 text-xs text-gray-500">Loading models…</li>}
            {models?.state !== "loading" && shown.length === 0 && (
              <li className="px-3 py-3 text-xs text-gray-500">{freeOnly ? "No free models on this key." : "No model matches."}</li>
            )}
          </ul>
        </div>
      )}
    </li>
  );
}

export default function SettingsView() {
  const settings = useModelSettings();
  const profile = useProfile();
  const [provider, setProvider] = useState("openrouter");
  const [label, setLabel] = useState("");
  const [key, setKey] = useState("");
  const [showKey, setShowKey] = useState(false);
  const [adding, setAdding] = useState(false);
  const [addError, setAddError] = useState<string | null>(null);
  const [keyModels, setKeyModels] = useState<Record<string, KeyModels>>({});
  const [quizModel, setQuizModel] = useState<QuizModel>("default");
  const [personalizing, setPersonalizing] = useState(false);
  const [tab, setTab] = useState<TabId>("models");

  // The tab lives in the URL hash, so /settings#keys links straight to it.
  useEffect(() => {
    const sync = () => setTab(tabFromHash());
    sync();
    window.addEventListener("hashchange", sync);
    return () => window.removeEventListener("hashchange", sync);
  }, []);
  function openTab(id: TabId) {
    setTab(id);
    window.history.replaceState(null, "", `#${id}`);
  }

  useEffect(() => setQuizModel(loadQuizModel()), [settings]);

  const options = useMemo(() => enabledOptions(settings), [settings]);
  const active = options.find((o) => isOptionActive(settings, o));
  const hasOwnModel = settings.mode === "custom" && Boolean(settings.model);
  const placeholder = PROVIDERS.find((p) => p.id === provider)?.placeholder ?? "API key";

  async function loadFor(k: SavedKey): Promise<void> {
    setKeyModels((m) => ({ ...m, [k.id]: { state: "loading" } }));
    try {
      const models = await listProviderModels(k.provider, k.key);
      setKeyModels((m) => ({ ...m, [k.id]: { state: "ready", models } }));
    } catch (err) {
      setKeyModels((m) => ({ ...m, [k.id]: { state: "error", error: formatApiError(err) } }));
    }
  }

  async function onAddKey(): Promise<void> {
    const k = key.trim();
    if (!k || adding) return;
    setAdding(true);
    setAddError(null);
    try {
      // Checked against the provider before it is saved.
      const models = await listProviderModels(provider, k);
      const id = addKey(provider, k, label);
      setKeyModels((m) => ({ ...m, [id]: { state: "ready", models } }));
      setKey("");
      setLabel("");
      if (models.length === 0) setAddError("Saved, but this key has no chat models.");
    } catch (err) {
      setAddError(err instanceof Error && !("status" in err) ? err.message : formatApiError(err));
    } finally {
      setAdding(false);
    }
  }

  return (
    <div className="mx-auto flex w-full max-w-5xl flex-col gap-5 pb-10">
      <header>
        <h1 className="text-2xl font-bold tracking-tight text-gray-900">Settings</h1>
        <p className="mt-1 text-sm text-gray-500">Saved to your account, so they follow you to any device you sign in on.</p>
      </header>

      <div className="flex flex-col gap-5 md:flex-row md:items-start">
        <nav aria-label="Settings sections" className="md:sticky md:top-4 md:w-56 md:shrink-0">
          <ul role="tablist" aria-orientation="vertical" className="-mx-1 flex gap-1 overflow-x-auto px-1 pb-1 md:mx-0 md:flex-col md:overflow-visible md:px-0 md:pb-0">
            {TABS.map((t) => {
              const on = tab === t.id;
              return (
                <li key={t.id} className="shrink-0">
                  <button
                    type="button"
                    role="tab"
                    id={`settings-tab-${t.id}`}
                    aria-selected={on}
                    aria-controls={`settings-panel-${t.id}`}
                    onClick={() => openTab(t.id)}
                    className={`flex w-full items-center gap-2.5 rounded-xl px-3 py-2 text-left transition focus:outline-none focus-visible:ring-2 focus-visible:ring-violet-400 ${
                      on ? "bg-violet-50 text-violet-800 ring-1 ring-violet-200" : "text-gray-700 hover:bg-gray-100"
                    }`}
                  >
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" className={`h-4 w-4 shrink-0 ${on ? "text-violet-600" : "text-gray-400"}`} aria-hidden="true">
                      <path d={t.icon} />
                    </svg>
                    <span className="min-w-0">
                      <span className="block whitespace-nowrap text-sm font-medium">
                        {t.label}
                        {t.id === "keys" && settings.keys.length > 0 && (
                          <span className="ml-1.5 rounded-full bg-gray-200 px-1.5 text-[10px] font-semibold text-gray-700">{settings.keys.length}</span>
                        )}
                      </span>
                      <span className="hidden truncate text-xs text-gray-500 md:block">{t.hint}</span>
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
        </nav>

        <div role="tabpanel" id={`settings-panel-${tab}`} aria-labelledby={`settings-tab-${tab}`} className="flex min-w-0 flex-1 flex-col gap-5">
      {tab === "models" && (
      <>
      <Section title="Chat model" hint="Which model answers you in Malai and the lesson chat. Only models turned on below are listed.">
        <Select<string>
          label="Chat model"
          value={active?.value ?? "auto"}
          onChange={(v) => {
            const o = options.find((x) => x.value === v);
            if (o) chooseOption(o);
          }}
          options={options.map((o) => ({
            value: o.value,
            label: `${o.kind === "own" ? `${o.keyLabel} · ` : ""}${o.label}${o.free === true ? " · Free" : ""}`,
          }))}
          className="sm:w-96"
        />
      </Section>

      <Section title="Built-in models" hint="Free models on the app's key. Turn off the ones you don't want in the model chooser.">
        <ul className="divide-y divide-gray-100 rounded-xl border border-gray-200">
          {MODEL_MODES.map((m) => {
            const on = !settings.disabledModes.includes(m.id);
            return (
              <li key={m.id} className="flex items-center gap-3 px-3 py-2.5">
                <span className="min-w-0 flex-1">
                  <span className="flex items-center gap-1.5">
                    <span className="text-sm font-medium text-gray-900">{m.label}</span>
                    <FreeBadge free />
                  </span>
                  <span className="block text-xs text-gray-500">{m.hint}</span>
                </span>
                <Switch on={on} onChange={(v) => setModeEnabled(m.id, v)} label={`${m.label} ${on ? "on" : "off"}`} />
              </li>
            );
          })}
        </ul>
        {options.length === 0 && (
          <p className="mt-2 text-xs text-amber-700">Everything is turned off, so Auto is used.</p>
        )}
        <p className="mt-3 text-xs text-gray-500">
          Models from your own keys are turned on in{" "}
          <button type="button" onClick={() => openTab("keys")} className="font-medium text-violet-700 hover:underline">
            API keys
          </button>
          .
        </p>
      </Section>

      <Section title="Quizzes" hint="The model that writes a quiz from the quiz form. A quiz asked for in the chat uses the chat model.">
        <Select<QuizModel>
          label="Quiz model"
          value={quizModel === "custom" && !hasOwnModel ? "default" : quizModel}
          onChange={(v) => {
            setQuizModel(v);
            saveQuizModel(v);
          }}
          options={[
            { value: "default", label: "Default (fastest)" },
            ...MODEL_MODES.filter((m) => !settings.disabledModes.includes(m.id)).map((m) => ({
              value: m.id as QuizModel,
              label: `${m.label} · ${m.hint}`,
            })),
            ...(hasOwnModel ? [{ value: "custom" as QuizModel, label: `Own key · ${settings.modelName || settings.model}` }] : []),
          ]}
          className="sm:w-80"
        />
      </Section>
      </>
      )}

      {tab === "keys" && (
      <Section
        title="Your API keys"
        hint="Add keys for any provider, then choose which of their models appear in the model chooser. Keys are saved to your account (only you can read them); only the key of the model in use is sent with a question, and the API never stores it."
      >
        <div className="flex flex-col gap-3">
          {settings.keys.length > 0 && (
            <ul className="flex flex-col gap-2">
              {settings.keys.map((k) => (
                <KeyCard key={k.id} k={k} models={keyModels[k.id]} onLoad={() => void loadFor(k)} />
              ))}
            </ul>
          )}

          {settings.keys.length < MAX_KEYS ? (
            <div className="flex flex-col gap-2 rounded-xl border border-dashed border-gray-300 p-3">
              <p className="text-sm font-medium text-gray-800">Add a key</p>
              <div className="flex flex-col gap-2 sm:flex-row">
                <Select
                  label="Provider"
                  value={provider}
                  onChange={(v) => {
                    setProvider(v);
                    setAddError(null);
                  }}
                  options={PROVIDERS.map((p) => ({ value: p.id, label: p.label }))}
                  className="sm:w-44"
                />
                <input
                  value={label}
                  onChange={(e) => setLabel(e.target.value)}
                  maxLength={60}
                  placeholder={`Name (optional, e.g. "${providerLabel(provider)} work")`}
                  aria-label="Key name"
                  className={`${field} sm:flex-1`}
                />
              </div>
              <div className="relative">
                <input
                  type={showKey ? "text" : "password"}
                  autoComplete="off"
                  spellCheck={false}
                  value={key}
                  onChange={(e) => setKey(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") void onAddKey();
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
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={() => void onAddKey()}
                  disabled={!key.trim() || adding}
                  className="inline-flex items-center gap-2 rounded-xl bg-violet-600 px-4 py-2 text-sm font-medium text-white hover:bg-violet-700 disabled:opacity-50"
                >
                  {adding && <span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-white border-t-transparent" aria-hidden="true" />}
                  {adding ? "Checking key…" : "Check & add key"}
                </button>
                {addError && <p role="alert" className="text-sm text-red-600">{addError}</p>}
              </div>
            </div>
          ) : (
            <p className="text-xs text-gray-500">You&apos;ve saved the maximum of {MAX_KEYS} keys.</p>
          )}
        </div>
      </Section>
      )}

      {tab === "personalization" && (
      <Section title="Personalization" hint="Your name, what you're studying, how Malai should answer.">
        <button
          type="button"
          onClick={() => setPersonalizing(true)}
          className="rounded-xl border border-violet-200 bg-white px-4 py-2 text-sm font-medium text-violet-700 hover:bg-violet-50"
        >
          Edit personalization
        </button>
      </Section>
      )}

      {tab === "account" && (
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
      )}
        </div>
      </div>

      {personalizing && <PersonalizationModal onClose={() => setPersonalizing(false)} />}
    </div>
  );
}
