"use client";

import { useEffect, useId, useMemo, useRef, useState } from "react";
import Link from "next/link";
import {
  chooseOption,
  enabledOptions,
  isOptionActive,
  saveModelSettings,
  useModelSettings,
  type ModelOption,
} from "@/lib/modelMode";

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

export function FreeBadge({ free }: { free: boolean | null }) {
  if (free !== true) return null;
  return <span className="shrink-0 rounded bg-emerald-50 px-1.5 py-px text-[10px] font-semibold text-emerald-700">Free</span>;
}

/**
 * The model chooser under the chat input. It lists only what is turned on in
 * Settings: the built-in free modes the learner kept, then the models turned
 * on from each of their own keys. "Free only" hides models that are not known
 * to be free. Opens upward over the composer, or downward (`down`).
 */
export default function ModelModePicker({ className = "", down = false }: { className?: string; down?: boolean }) {
  const settings = useModelSettings();
  const [open, setOpen] = useState(false);
  const [filter, setFilter] = useState("");
  const rootRef = useRef<HTMLDivElement | null>(null);
  const menuId = useId();

  const options = useMemo(() => enabledOptions(settings), [settings]);
  const active = options.find((o) => isOptionActive(settings, o));
  const label = active?.label ?? "Auto";

  const shown = useMemo(() => {
    const q = filter.trim().toLowerCase();
    return options.filter(
      (o) =>
        (!settings.freeOnly || o.free === true || isOptionActive(settings, o)) &&
        (!q || o.label.toLowerCase().includes(q) || o.hint.toLowerCase().includes(q)),
    );
  }, [options, filter, settings]);

  useEffect(() => {
    if (!open) return;
    setFilter("");
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

  // Group own-key models under their key's name.
  const groups: { title: string | null; items: ModelOption[] }[] = [];
  for (const o of shown) {
    const title = o.kind === "mode" ? null : o.keyLabel;
    const last = groups[groups.length - 1];
    if (last && last.title === title) last.items.push(o);
    else groups.push({ title, items: [o] });
  }

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
        {active?.kind === "own" && <span className="shrink-0 rounded bg-violet-50 px-1 text-[10px] font-semibold text-violet-700">Own key</span>}
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" className={`h-3 w-3 shrink-0 transition ${open ? "rotate-180" : ""}`} aria-hidden="true">
          <path d="M6 9l6 6 6-6" />
        </svg>
      </button>

      {open && (
        <div
          id={menuId}
          role="menu"
          className={`absolute left-0 z-40 ${down ? "top-full mt-2" : "bottom-full mb-2"} flex max-h-[min(34rem,70dvh)] w-80 max-w-[calc(100vw-2rem)] flex-col rounded-2xl border border-gray-200 bg-white p-1.5 shadow-xl`}
        >
          <div className="flex items-center gap-2 px-1.5 pb-1.5 pt-1">
            {options.length > 6 && (
              <input
                type="search"
                value={filter}
                onChange={(e) => setFilter(e.target.value)}
                placeholder="Search models"
                aria-label="Search models"
                className="min-w-0 flex-1 rounded-lg border border-gray-200 px-2.5 py-1.5 text-xs text-gray-900 outline-none focus:border-violet-500 focus:ring-2 focus:ring-violet-100"
              />
            )}
            <button
              type="button"
              role="menuitemcheckbox"
              aria-checked={settings.freeOnly}
              onClick={() => saveModelSettings({ freeOnly: !settings.freeOnly })}
              title="Show only models known to be free"
              className={`ml-auto shrink-0 rounded-full border px-2.5 py-1 text-xs font-medium transition ${
                settings.freeOnly
                  ? "border-emerald-300 bg-emerald-50 text-emerald-700"
                  : "border-gray-200 text-gray-600 hover:border-emerald-200 hover:text-emerald-700"
              }`}
            >
              {settings.freeOnly ? "✓ Free only" : "Free only"}
            </button>
          </div>

          <div className="min-h-0 overflow-y-auto">
            {groups.map((g) => (
              <div key={g.title ?? "_modes"} className={g.title ? "mt-1 border-t border-gray-100 pt-1" : ""}>
                {g.title && (
                  <p className="truncate px-3 pb-0.5 pt-1 text-[11px] font-semibold uppercase tracking-wide text-gray-400">{g.title}</p>
                )}
                {g.items.map((o) => {
                  const on = isOptionActive(settings, o);
                  return (
                    <button
                      key={o.value}
                      type="button"
                      role="menuitemradio"
                      aria-checked={on}
                      onClick={() => {
                        chooseOption(o);
                        setOpen(false);
                      }}
                      className="flex w-full items-start gap-2.5 rounded-xl px-3 py-2 text-left transition hover:bg-gray-50 focus:bg-gray-50 focus:outline-none"
                    >
                      <Check on={on} />
                      <span className="min-w-0 flex-1">
                        <span className="flex items-center gap-1.5">
                          <span className="truncate text-sm font-medium text-gray-900">{o.label}</span>
                          <FreeBadge free={o.free} />
                        </span>
                        <span className="block truncate text-xs text-gray-500">{o.hint}</span>
                      </span>
                    </button>
                  );
                })}
              </div>
            ))}
            {shown.length === 0 && (
              <p className="px-3 py-3 text-xs text-gray-500">
                {settings.freeOnly ? "No free models are turned on." : "No model matches."}
              </p>
            )}
          </div>

          <div className="mt-1 border-t border-gray-100 pt-1">
            <Link
              href="/settings"
              onClick={() => setOpen(false)}
              className="flex w-full items-center justify-between rounded-xl px-3 py-2 text-sm font-medium text-gray-700 transition hover:bg-gray-50 focus:bg-gray-50 focus:outline-none"
            >
              <span>
                Manage models
                <span className="ml-1.5 text-xs font-normal text-gray-500">keys &amp; on/off</span>
              </span>
              <span aria-hidden="true">→</span>
            </Link>
          </div>
        </div>
      )}
    </div>
  );
}
