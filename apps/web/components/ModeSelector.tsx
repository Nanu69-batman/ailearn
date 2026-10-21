"use client";

import type { ChatMode } from "../lib/api";

const MODES: Array<{ value: ChatMode; label: string; hint: string }> = [
  { value: "qa", label: "Ask", hint: "Q&A at current timestamp" },
  { value: "explain_simple", label: "Explain simply", hint: "Plain-language version" },
  { value: "analogy", label: "Analogy", hint: "Concept via analogy" },
  { value: "summarize", label: "Summarize", hint: "Recap learned-so-far" },
  { value: "compare", label: "Compare", hint: "Two concepts side by side" },
];

interface ModeSelectorProps {
  value: ChatMode;
  onChange: (mode: ChatMode) => void;
  disabled?: boolean;
}

export default function ModeSelector({ value, onChange, disabled }: ModeSelectorProps) {
  return (
    <div className="mb-3 flex flex-wrap gap-1.5" role="radiogroup" aria-label="Answer mode">
      {MODES.map((m) => (
        <label
          key={m.value}
          className={`cursor-pointer select-none rounded-full border px-3 py-1 text-xs font-medium transition has-[:focus-visible]:ring-2 has-[:focus-visible]:ring-violet-300 ${
            value === m.value
              ? "border-violet-600 bg-violet-600 text-white shadow-sm shadow-violet-600/20"
              : "border-gray-200 bg-white text-gray-600 hover:border-violet-200 hover:bg-violet-50 hover:text-violet-700"
          } ${disabled ? "cursor-not-allowed opacity-60" : ""}`}
          title={m.hint}
        >
          <input
            type="radio"
            name="chat-mode"
            value={m.value}
            checked={value === m.value}
            disabled={disabled}
            onChange={() => onChange(m.value)}
            className="sr-only"
          />
          {m.label}
        </label>
      ))}
    </div>
  );
}
