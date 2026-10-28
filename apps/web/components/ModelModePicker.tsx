"use client";

import { useEffect, useId, useMemo, useRef, useState } from "react";
import { formatApiError, listProviderModels, type ProviderModel } from "@/lib/api";
import { MODEL_MODES, PROVIDERS, saveModelSettings, useModelSettings } from "@/lib/modelMode";

const CHECK = "M5 12l5 5L20 7";

function Check({ on }: { on: boolean }) {
  return (
    <span className="mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center text-violet-600" aria-hidden="true">
      {on && (
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.6" strokeLinecap="round" strokeLinejoin="round" className="h-4 w-4">
          <path d={CHECK} />
        </svg>
      )}
    </span>
  );
}

/**
 * The model chooser under the chat input: the four free modes, and an
 * Advanced section to use your own key for any provider and pick one of the
 * models on it. Opens upward, over the composer.
 */
export default function ModelModePicker({ className = "" }: { className?: string }) {
  const settings = useModelSettings();
  const { mode } = settings;
  const [open, setOpen] = useState(false);
  const [advanced, setAdvanced] = useState(false);
  const [provider, setProvider] = useState(settings.provider);
  const [key, setKey] = useState(settings.key);
  const [models, setModels] = useState<ProviderModel[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState("");
  const rootRef = useRef<HTMLDivElement | null>(null);
  const menuId = useId();
  const keyId = useId();
  const providerId = useId();

  const label =
    mode === "custom"
      ? settings.modelName || settings.model
      : (MODEL_MODES.find((m) => m.id === mode) ?? MODEL_MODES[0]).label;

  useEffect(() => {
    if (!open) return;
    function onDown(e: MouseEvent): void {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false);
    }
    function onKey(e: KeyboardEvent): void {
      if (e.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  // Opening the menu starts from what is saved.
  useEffect(() => {
    if (!open) return;
    setProvider(settings.provider);
    setKey(settings.key);
    setAdvanced(mode === "custom");
    setError(null);
    setFilter("");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const shown = useMemo(() => {
    const q = filter.trim().toLowerCase();
    const list = models ?? [];
    return q ? list.filter((m) => m.id.toLowerCase().includes(q) || m.name.toLowerCase().includes(q)) : list;
  }, [models, filter]);

  async function loadModels(): Promise<void> {
    const k = key.trim();
    if (!k || loading) return;
    setLoading(true);
    setError(null);
    try {
      const list = await listProviderModels(provider, k);
      setModels(list);
      // The key worked: keep it, so the list can be reopened later.
      saveModelSettings({ provider, key: k });
      if (list.length === 0) setError("This key has no chat models.");
    } catch (err) {
      setModels(null);
      setError(formatApiError(err));
    } finally {
      setLoading(false);
    }
  }

  const placeholder = PROVIDERS.find((p) => p.id === provider)?.placeholder ?? "API key";

  return (
    <div ref={rootRef} className={`relative flex items-center gap-2 ${className}`}>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={open ? menuId : undefined}
        title="Choose which model answers"
        className="inline-flex max-w-full items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium text-gray-500 transition hover:bg-gray-100 hover:text-gray-800 focus:outline-none focus-visible:ring-2 focus-visible:ring-violet-400"
      >
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="h-3.5 w-3.5 shrink-0 text-violet-500" aria-hidden="true">
          <path d="M13 2L4 14h7l-1 8 9-12h-7l1-8z" />
        </svg>
        <span className="truncate">{label}</span>
        {mode === "custom" && <span className="shrink-0 rounded bg-violet-50 px-1 text-[10px] font-semibold text-violet-700">Own key</span>}
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" className={`h-3 w-3 shrink-0 transition ${open ? "rotate-180" : ""}`} aria-hidden="true">
          <path d="M6 9l6 6 6-6" />
        </svg>
      </button>

      {open && (
        <div
          id={menuId}
          role="menu"
          className="absolute bottom-full left-0 z-40 mb-2 flex max-h-[min(34rem,70dvh)] w-80 max-w-[calc(100vw-2rem)] flex-col rounded-2xl border border-gray-200 bg-white p-1.5 shadow-xl"
        >
          <div className="min-h-0 overflow-y-auto">
            {MODEL_MODES.map((m) => (
              <button
                key={m.id}
                type="button"
                role="menuitemradio"
                aria-checked={m.id === mode}
                onClick={() => {
                  saveModelSettings({ mode: m.id });
                  setOpen(false);
                }}
                className="flex w-full items-start gap-2.5 rounded-xl px-3 py-2 text-left transition hover:bg-gray-50 focus:bg-gray-50 focus:outline-none"
              >
                <Check on={m.id === mode} />
                <span className="min-w-0">
                  <span className="block text-sm font-medium text-gray-900">{m.label}</span>
                  <span className="block text-xs text-gray-500">{m.hint}</span>
                </span>
              </button>
            ))}

            <div className="mt-1 border-t border-gray-100 pt-1">
              <button
                type="button"
                onClick={() => setAdvanced((a) => !a)}
                aria-expanded={advanced}
                className="flex w-full items-center justify-between rounded-xl px-3 py-2 text-left text-sm font-medium text-gray-700 transition hover:bg-gray-50 focus:bg-gray-50 focus:outline-none"
              >
                <span>
                  Advanced
                  <span className="ml-1.5 text-xs font-normal text-gray-500">your own key</span>
                </span>
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" className={`h-3.5 w-3.5 transition ${advanced ? "rotate-180" : ""}`} aria-hidden="true">
                  <path d="M6 9l6 6 6-6" />
                </svg>
              </button>

              {advanced && (
                <div className="flex flex-col gap-2 px-3 pb-2 pt-1">
                  <div className="flex gap-2">
                    <label htmlFor={providerId} className="sr-only">Provider</label>
                    <select
                      id={providerId}
                      value={provider}
                      onChange={(e) => {
                        setProvider(e.target.value);
                        setModels(null);
                        setError(null);
                      }}
                      className="w-32 shrink-0 rounded-lg border border-gray-300 bg-white px-2 py-1.5 text-xs text-gray-900 outline-none focus:border-violet-500 focus:ring-2 focus:ring-violet-100"
                    >
                      {PROVIDERS.map((p) => (
                        <option key={p.id} value={p.id}>{p.label}</option>
                      ))}
                    </select>
                    <label htmlFor={keyId} className="sr-only">API key</label>
                    <input
                      id={keyId}
                      type="password"
                      autoComplete="off"
                      spellCheck={false}
                      value={key}
                      onChange={(e) => {
                        setKey(e.target.value);
                        setModels(null);
                      }}
                      onKeyDown={(e) => {
                        if (e.key === "Enter") {
                          e.preventDefault();
                          void loadModels();
                        }
                      }}
                      placeholder={placeholder}
                      className="min-w-0 flex-1 rounded-lg border border-gray-300 px-2.5 py-1.5 text-xs text-gray-900 outline-none focus:border-violet-500 focus:ring-2 focus:ring-violet-100"
                    />
                  </div>
                  <button
                    type="button"
                    onClick={() => void loadModels()}
                    disabled={!key.trim() || loading}
                    className="inline-flex items-center justify-center gap-1.5 rounded-lg bg-violet-600 px-2.5 py-1.5 text-xs font-semibold text-white hover:bg-violet-700 disabled:opacity-50"
                  >
                    {loading && <span className="h-3 w-3 animate-spin rounded-full border-2 border-white/60 border-t-transparent" aria-hidden="true" />}
                    {loading ? "Loading models…" : models ? "Reload models" : "Show models on this key"}
                  </button>
                  {error && <p role="alert" className="text-xs text-red-600">{error}</p>}

                  {models && models.length > 0 && (
                    <>
                      <input
                        type="search"
                        value={filter}
                        onChange={(e) => setFilter(e.target.value)}
                        placeholder={`Search ${models.length} models`}
                        aria-label="Search models"
                        className="rounded-lg border border-gray-200 px-2.5 py-1.5 text-xs text-gray-900 outline-none focus:border-violet-500 focus:ring-2 focus:ring-violet-100"
                      />
                      <ul className="max-h-48 overflow-y-auto rounded-lg border border-gray-100">
                        {shown.map((m) => {
                          const on = mode === "custom" && settings.model === m.id && settings.provider === provider;
                          return (
                            <li key={m.id}>
                              <button
                                type="button"
                                role="menuitemradio"
                                aria-checked={on}
                                onClick={() => {
                                  saveModelSettings({ mode: "custom", provider, key: key.trim(), model: m.id, modelName: m.name });
                                  setOpen(false);
                                }}
                                className="flex w-full items-start gap-2 px-2.5 py-1.5 text-left hover:bg-gray-50 focus:bg-gray-50 focus:outline-none"
                              >
                                <Check on={on} />
                                <span className="min-w-0">
                                  <span className="block truncate text-xs font-medium text-gray-900">{m.name}</span>
                                  {m.name !== m.id && <span className="block truncate text-[11px] text-gray-400">{m.id}</span>}
                                </span>
                              </button>
                            </li>
                          );
                        })}
                        {shown.length === 0 && <li className="px-2.5 py-2 text-xs text-gray-500">No model matches.</li>}
                      </ul>
                    </>
                  )}

                  <div className="flex items-start justify-between gap-2">
                    <p className="text-[11px] leading-snug text-gray-500">
                      Kept only in this browser and sent with your questions. If your model fails, the free models answer.
                    </p>
                    {settings.key && (
                      <button
                        type="button"
                        onClick={() => {
                          saveModelSettings({ mode: mode === "custom" ? "auto" : mode, key: "", model: "", modelName: "" });
                          setKey("");
                          setModels(null);
                        }}
                        className="shrink-0 rounded-lg px-2 py-0.5 text-xs text-red-600 hover:bg-red-50"
                      >
                        Remove key
                      </button>
                    )}
                  </div>
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
