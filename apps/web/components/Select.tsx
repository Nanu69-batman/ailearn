"use client";

import { useEffect, useId, useRef, useState } from "react";

export interface SelectOption<T extends string> {
  value: T;
  label: string;
}

interface SelectProps<T extends string> {
  value: T;
  options: ReadonlyArray<SelectOption<T>>;
  onChange: (value: T) => void;
  /** Accessible name; also shown before the value when `prefix` is set. */
  label: string;
  prefix?: boolean;
  className?: string;
}

/**
 * The app's dropdown: a pill button and a styled list, instead of the
 * browser's native <select> menu. Keyboard: arrows/Home/End move, Enter or
 * Space picks, Esc closes, typing a letter jumps to the next match.
 */
export default function Select<T extends string>({ value, options, onChange, label, prefix, className = "" }: SelectProps<T>) {
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const rootRef = useRef<HTMLDivElement>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const listRef = useRef<HTMLUListElement>(null);
  const id = useId();
  const selectedIndex = Math.max(0, options.findIndex((o) => o.value === value));

  useEffect(() => {
    if (!open) return;
    const onDown = (e: PointerEvent) => {
      if (!rootRef.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("pointerdown", onDown);
    listRef.current?.focus();
    return () => document.removeEventListener("pointerdown", onDown);
  }, [open]);

  function openList(): void {
    setActive(selectedIndex);
    setOpen(true);
  }

  function pick(index: number): void {
    onChange(options[index].value);
    setOpen(false);
    buttonRef.current?.focus();
  }

  function onListKey(e: React.KeyboardEvent): void {
    const n = options.length;
    const moves: Record<string, number> = { ArrowDown: active + 1, ArrowUp: active - 1, Home: 0, End: n - 1 };
    if (e.key in moves) {
      e.preventDefault();
      setActive(Math.min(n - 1, Math.max(0, moves[e.key])));
    } else if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      pick(active);
    } else if (e.key === "Escape" || e.key === "Tab") {
      if (e.key === "Escape") e.preventDefault();
      setOpen(false);
      buttonRef.current?.focus();
    } else if (e.key.length === 1) {
      const k = e.key.toLowerCase();
      for (let i = 1; i <= n; i++) {
        const j = (active + i) % n;
        if (options[j].label.toLowerCase().startsWith(k)) {
          setActive(j);
          break;
        }
      }
    }
  }

  return (
    <div ref={rootRef} className={`relative ${className}`}>
      <button
        ref={buttonRef}
        type="button"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={open ? `${id}-list` : undefined}
        aria-label={`${label}: ${options[selectedIndex]?.label ?? ""}`}
        onClick={() => (open ? setOpen(false) : openList())}
        onKeyDown={(e) => {
          if (e.key === "ArrowDown" || e.key === "ArrowUp") {
            e.preventDefault();
            openList();
          }
        }}
        className={`flex w-full items-center gap-2 rounded-full border bg-white py-2 pl-4 pr-3 text-sm font-medium text-gray-700 shadow-sm outline-none transition hover:border-violet-200 focus-visible:ring-2 focus-visible:ring-violet-200 ${
          open ? "border-violet-400 ring-2 ring-violet-200" : "border-gray-200"
        }`}
      >
        {prefix && <span className="text-gray-400">{label}:</span>}
        <span className="flex-1 truncate text-left">{options[selectedIndex]?.label}</span>
        <svg
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
          className={`h-4 w-4 shrink-0 text-gray-500 transition-transform ${open ? "rotate-180" : ""}`}
          aria-hidden="true"
        >
          <path d="M6 9l6 6 6-6" />
        </svg>
      </button>

      {open && (
        <ul
          ref={listRef}
          id={`${id}-list`}
          role="listbox"
          tabIndex={-1}
          aria-label={label}
          aria-activedescendant={`${id}-opt-${active}`}
          onKeyDown={onListKey}
          className="absolute right-0 z-40 mt-2 min-w-full overflow-hidden rounded-2xl border border-gray-200 bg-white p-1.5 shadow-xl shadow-gray-900/10 outline-none"
        >
          {options.map((o, i) => {
            const selected = o.value === value;
            return (
              <li
                key={o.value}
                id={`${id}-opt-${i}`}
                role="option"
                aria-selected={selected}
                onMouseEnter={() => setActive(i)}
                onClick={() => pick(i)}
                className={`flex cursor-pointer items-center gap-3 whitespace-nowrap rounded-xl px-3 py-2 text-sm transition ${
                  i === active ? "bg-violet-50 text-violet-900" : "text-gray-700"
                } ${selected ? "font-semibold text-violet-700" : ""}`}
              >
                <span className="flex-1">{o.label}</span>
                <svg
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2.4"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  className={`h-4 w-4 text-violet-600 ${selected ? "" : "invisible"}`}
                  aria-hidden="true"
                >
                  <path d="M5 12l5 5L20 7" />
                </svg>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
