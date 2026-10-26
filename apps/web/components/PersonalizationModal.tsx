"use client";

import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import {
  LIMITS,
  TRAITS,
  loadPersonalization,
  savePersonalization,
  syncPersonalization,
  type LanguagePref,
  type LengthPref,
  type Personalization,
} from "@/lib/personalization";

const LANGUAGES: Array<{ value: LanguagePref; label: string; hint: string }> = [
  { value: "auto", label: "Match me", hint: "Reply in whatever I write or speak" },
  { value: "english", label: "English", hint: "Always English" },
  { value: "hinglish", label: "Hinglish", hint: "Hindi + English, in Roman letters" },
  { value: "hindi", label: "Hindi", hint: "Simple Hindi in Devanagari" },
];

const LENGTHS: Array<{ value: LengthPref; label: string }> = [
  { value: "concise", label: "Concise" },
  { value: "balanced", label: "Balanced" },
  { value: "detailed", label: "Detailed" },
];

const field =
  "w-full rounded-xl border border-gray-200 bg-white px-3 py-2.5 text-sm text-gray-900 placeholder-gray-400 outline-none transition focus:border-violet-400 focus:ring-2 focus:ring-violet-500/20";

function Counter({ value, max }: { value: string; max: number }) {
  return (
    <span className={`text-[11px] tabular-nums ${value.length > max * 0.9 ? "text-amber-600" : "text-gray-400"}`}>
      {value.length}/{max}
    </span>
  );
}

/** Malai's personalization, like ChatGPT's: how to address the learner, traits, language, custom instructions. */
export default function PersonalizationModal({ onClose }: { onClose: () => void }) {
  const [p, setP] = useState<Personalization>(() => loadPersonalization());
  const [saving, setSaving] = useState(false);
  const [customTrait, setCustomTrait] = useState("");
  const dialogRef = useRef<HTMLDivElement>(null);

  // Another device may have changed it: show the account's copy.
  useEffect(() => {
    let cancelled = false;
    void syncPersonalization().then((remote) => {
      if (!cancelled) setP(remote);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    dialogRef.current?.querySelector<HTMLElement>("input, textarea, button")?.focus();
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  const set = <K extends keyof Personalization>(key: K, value: Personalization[K]) => setP((prev) => ({ ...prev, [key]: value }));

  const toggleTrait = (t: string) =>
    set("traits", p.traits.includes(t) ? p.traits.filter((x) => x !== t) : p.traits.length < LIMITS.traits ? [...p.traits, t] : p.traits);

  const addCustomTrait = () => {
    const t = customTrait.trim().slice(0, LIMITS.trait);
    if (t && !p.traits.includes(t) && p.traits.length < LIMITS.traits) set("traits", [...p.traits, t]);
    setCustomTrait("");
  };

  const save = async () => {
    setSaving(true);
    await savePersonalization(p);
    setSaving(false);
    onClose();
  };

  const chip = (on: boolean) =>
    `rounded-full border px-3 py-1.5 text-xs font-medium transition focus:outline-none focus-visible:ring-2 focus-visible:ring-violet-400 ${
      on ? "border-violet-500 bg-violet-600 text-white" : "border-gray-200 bg-white text-gray-700 hover:border-violet-300 hover:bg-violet-50"
    }`;

  const extraTraits = p.traits.filter((t) => !(TRAITS as readonly string[]).includes(t));

  return createPortal(
    <div className="fixed inset-0 z-[80] flex items-center justify-center bg-gray-900/40 p-4" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="personalize-title"
        className="flex max-h-full w-full max-w-xl flex-col overflow-hidden rounded-3xl bg-white shadow-2xl shadow-gray-900/20"
      >
        <div className="flex items-start justify-between gap-4 border-b border-gray-100 px-6 py-4">
          <div>
            <h2 id="personalize-title" className="text-base font-semibold text-gray-900">
              Personalize Malai
            </h2>
            <p className="text-xs text-gray-500">Applies to every chat and voice conversation. Answers still come only from your materials.</p>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="rounded-lg p-1.5 text-gray-400 transition hover:bg-gray-100 hover:text-gray-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-violet-400"
          >
            <svg viewBox="0 0 24 24" className="h-5 w-5" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" aria-hidden="true">
              <path d="M6 6l12 12M18 6L6 18" />
            </svg>
          </button>
        </div>

        <div className="min-h-0 flex-1 space-y-5 overflow-y-auto px-6 py-5">
          <label className="flex items-center justify-between gap-4 rounded-2xl bg-violet-50 px-4 py-3">
            <span>
              <span className="block text-sm font-medium text-gray-900">Use personalization</span>
              <span className="block text-xs text-gray-500">Turn off to get Malai&apos;s default style.</span>
            </span>
            <button
              type="button"
              role="switch"
              aria-checked={p.enabled}
              onClick={() => set("enabled", !p.enabled)}
              className={`relative h-6 w-11 shrink-0 rounded-full transition focus:outline-none focus-visible:ring-2 focus-visible:ring-violet-400 ${p.enabled ? "bg-violet-600" : "bg-gray-300"}`}
            >
              <span className={`absolute top-0.5 h-5 w-5 rounded-full bg-white shadow transition-all ${p.enabled ? "left-[1.375rem]" : "left-0.5"}`} />
            </button>
          </label>

          <fieldset disabled={!p.enabled} className={`space-y-5 ${p.enabled ? "" : "opacity-50"}`}>
            <div className="space-y-1.5">
              <div className="flex items-center justify-between">
                <label htmlFor="pz-name" className="text-sm font-medium text-gray-800">
                  What should Malai call you?
                </label>
                <Counter value={p.name} max={LIMITS.name} />
              </div>
              <input id="pz-name" className={field} value={p.name} maxLength={LIMITS.name} placeholder="Your name or nickname" onChange={(e) => set("name", e.target.value)} />
            </div>

            <div className="space-y-1.5">
              <div className="flex items-center justify-between">
                <label htmlFor="pz-about" className="text-sm font-medium text-gray-800">
                  What do you study?
                </label>
                <Counter value={p.about} max={LIMITS.about} />
              </div>
              <input
                id="pz-about"
                className={field}
                value={p.about}
                maxLength={LIMITS.about}
                placeholder="e.g. 2nd year B.Tech CSE, preparing for end-sems"
                onChange={(e) => set("about", e.target.value)}
              />
            </div>

            <div className="space-y-2">
              <p className="text-sm font-medium text-gray-800">What traits should Malai have?</p>
              <div className="flex flex-wrap gap-2">
                {TRAITS.map((t) => (
                  <button key={t} type="button" aria-pressed={p.traits.includes(t)} onClick={() => toggleTrait(t)} className={chip(p.traits.includes(t))}>
                    {t}
                  </button>
                ))}
                {extraTraits.map((t) => (
                  <button key={t} type="button" aria-pressed onClick={() => toggleTrait(t)} className={chip(true)} title="Remove">
                    {t} ×
                  </button>
                ))}
              </div>
              <div className="flex gap-2">
                <input
                  className={field}
                  value={customTrait}
                  maxLength={LIMITS.trait}
                  placeholder="Add your own trait"
                  aria-label="Add your own trait"
                  onChange={(e) => setCustomTrait(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") {
                      e.preventDefault();
                      addCustomTrait();
                    }
                  }}
                />
                <button
                  type="button"
                  onClick={addCustomTrait}
                  disabled={!customTrait.trim() || p.traits.length >= LIMITS.traits}
                  className="shrink-0 rounded-xl border border-gray-200 px-3 text-sm font-medium text-gray-700 transition hover:bg-gray-50 disabled:opacity-40"
                >
                  Add
                </button>
              </div>
            </div>

            <div className="space-y-2">
              <p className="text-sm font-medium text-gray-800">Reply language</p>
              <div className="grid grid-cols-2 gap-2">
                {LANGUAGES.map((l) => (
                  <button
                    key={l.value}
                    type="button"
                    aria-pressed={p.language === l.value}
                    onClick={() => set("language", l.value)}
                    className={`rounded-2xl border px-3 py-2.5 text-left transition focus:outline-none focus-visible:ring-2 focus-visible:ring-violet-400 ${
                      p.language === l.value ? "border-violet-500 bg-violet-50" : "border-gray-200 hover:border-violet-300"
                    }`}
                  >
                    <span className="block text-sm font-medium text-gray-900">{l.label}</span>
                    <span className="block text-[11px] text-gray-500">{l.hint}</span>
                  </button>
                ))}
              </div>
            </div>

            <div className="space-y-2">
              <p className="text-sm font-medium text-gray-800">Answer length</p>
              <div className="inline-flex rounded-xl border border-gray-200 p-1">
                {LENGTHS.map((l) => (
                  <button
                    key={l.value}
                    type="button"
                    aria-pressed={p.length === l.value}
                    onClick={() => set("length", l.value)}
                    className={`rounded-lg px-4 py-1.5 text-sm font-medium transition ${p.length === l.value ? "bg-violet-600 text-white" : "text-gray-600 hover:bg-gray-50"}`}
                  >
                    {l.label}
                  </button>
                ))}
              </div>
            </div>

            <div className="space-y-1.5">
              <div className="flex items-center justify-between">
                <label htmlFor="pz-notes" className="text-sm font-medium text-gray-800">
                  Anything else Malai should know?
                </label>
                <Counter value={p.instructions} max={LIMITS.instructions} />
              </div>
              <textarea
                id="pz-notes"
                rows={4}
                className={`${field} resize-none`}
                value={p.instructions}
                maxLength={LIMITS.instructions}
                placeholder="e.g. Explain with real-life examples. Give me a quick check question after each concept."
                onChange={(e) => set("instructions", e.target.value)}
              />
            </div>
          </fieldset>
        </div>

        <div className="flex items-center justify-end gap-2 border-t border-gray-100 px-6 py-3">
          <button type="button" onClick={onClose} className="rounded-full px-4 py-2 text-sm font-medium text-gray-600 transition hover:bg-gray-100">
            Cancel
          </button>
          <button
            type="button"
            onClick={() => void save()}
            disabled={saving}
            className="rounded-full bg-violet-600 px-5 py-2 text-sm font-semibold text-white shadow-md shadow-violet-600/30 transition hover:bg-violet-500 disabled:opacity-60"
          >
            {saving ? "Saving…" : "Save"}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}
