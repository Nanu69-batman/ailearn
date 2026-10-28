"use client";

import { useEffect, useId, useRef, useState } from "react";
import { MODEL_MODES, saveModelSettings, useModelSettings } from "@/lib/modelMode";

const CHECK = "M5 12l5 5L20 7";

/**
 * The model mode under the chat input (Auto / Fast / Balanced / Powerful),
 * and the learner's own OpenRouter key. Opens upward, over the composer.
 */
export default function ModelModePicker({ className = "" }: { className?: string }) {
  const { mode, key } = useModelSettings();
  const [open, setOpen] = useState(false);
  const [editingKey, setEditingKey] = useState(false);
  const [draft, setDraft] = useState("");
  const rootRef = useRef<HTMLDivElement | null>(null);
  const menuId = useId();
  const keyId = useId();
  const current = MODEL_MODES.find((m) => m.id === mode) ?? MODEL_MODES[0];

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

  useEffect(() => {
    if (!open) setEditingKey(false);
  }, [open]);

  return (
    <div ref={rootRef} className={`relative flex items-center gap-2 ${className}`}>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={open ? menuId : undefined}
        title="Choose which models answer"
        className="inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium text-gray-500 transition hover:bg-gray-100 hover:text-gray-800 focus:outline-none focus-visible:ring-2 focus-visible:ring-violet-400"
      >
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="h-3.5 w-3.5 text-violet-500" aria-hidden="true">
          <path d="M13 2L4 14h7l-1 8 9-12h-7l1-8z" />
        </svg>
        {current.label}
        {key && <span className="rounded bg-violet-50 px-1 text-[10px] font-semibold text-violet-700">Own key</span>}
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" className={`h-3 w-3 transition ${open ? "rotate-180" : ""}`} aria-hidden="true">
          <path d="M6 9l6 6 6-6" />
        </svg>
      </button>

      {open && (
        <div
          id={menuId}
          role="menu"
          className="absolute bottom-full left-0 z-40 mb-2 w-72 rounded-2xl border border-gray-200 bg-white p-1.5 shadow-xl"
        >
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
              <span className="mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center text-violet-600" aria-hidden="true">
                {m.id === mode && (
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.6" strokeLinecap="round" strokeLinejoin="round" className="h-4 w-4">
                    <path d={CHECK} />
                  </svg>
                )}
              </span>
              <span className="min-w-0">
                <span className="block text-sm font-medium text-gray-900">{m.label}</span>
                <span className="block text-xs text-gray-500">{m.hint}</span>
              </span>
            </button>
          ))}

          <div className="mt-1 border-t border-gray-100 px-3 pb-1.5 pt-2.5">
            {editingKey ? (
              <form
                onSubmit={(e) => {
                  e.preventDefault();
                  saveModelSettings({ key: draft });
                  setEditingKey(false);
                }}
                className="flex flex-col gap-2"
              >
                <label htmlFor={keyId} className="text-xs font-medium text-gray-700">
                  Your OpenRouter key
                </label>
                <input
                  id={keyId}
                  type="password"
                  autoFocus
                  autoComplete="off"
                  spellCheck={false}
                  value={draft}
                  onChange={(e) => setDraft(e.target.value)}
                  placeholder="sk-or-v1-…"
                  className="w-full rounded-lg border border-gray-300 px-2.5 py-1.5 text-xs text-gray-900 outline-none focus:border-violet-500 focus:ring-2 focus:ring-violet-100"
                />
                <p className="text-[11px] leading-snug text-gray-500">
                  Kept only in this browser and sent with your questions. Fast, Balanced and Powerful then use paid models
                  on your account first.
                </p>
                <div className="flex justify-end gap-1.5">
                  <button type="button" onClick={() => setEditingKey(false)} className="rounded-lg px-2.5 py-1 text-xs text-gray-600 hover:bg-gray-100">
                    Cancel
                  </button>
                  <button
                    type="submit"
                    disabled={!draft.trim()}
                    className="rounded-lg bg-violet-600 px-2.5 py-1 text-xs font-semibold text-white hover:bg-violet-700 disabled:opacity-50"
                  >
                    Save key
                  </button>
                </div>
              </form>
            ) : (
              <div className="flex items-center justify-between gap-2">
                <span className="text-xs text-gray-500">{key ? "Using your OpenRouter key" : "Using the app's free models"}</span>
                <span className="flex gap-1">
                  {key && (
                    <button
                      type="button"
                      onClick={() => saveModelSettings({ key: "" })}
                      className="rounded-lg px-2 py-1 text-xs text-red-600 hover:bg-red-50"
                    >
                      Remove
                    </button>
                  )}
                  <button
                    type="button"
                    onClick={() => {
                      setDraft("");
                      setEditingKey(true);
                    }}
                    className="rounded-lg px-2 py-1 text-xs font-medium text-violet-700 hover:bg-violet-50"
                  >
                    {key ? "Change key" : "Use your own key"}
                  </button>
                </span>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
