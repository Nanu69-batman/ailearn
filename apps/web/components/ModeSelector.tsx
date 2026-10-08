"use client";

import type { ChatMode } from "../lib/api";

const MODES: Array<{ value: ChatMode; label: string; hint: string }> = [
  { value: "qa", label: "Ask", hint: "Q&A at current timestamp" },
  { value: "explain_simple", label: "Explain simply", hint: "Plain-language version" },
  { value: "analogy", label: "Analogy", hint: "Concept via analogy" },
  { value: "summarize", label: "Summarize", hint: "Recap learned-so-far" },
  { value: "compare", label: "Compare", hint: "Two concepts side by side" },
  { value: "quiz", label: "Quiz", hint: "Short check from covered content" },
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
          className={
            value === m.value
              ? "cursor-pointer rounded-full border border-violet-300 bg-violet-50 px-3 py-1 text-xs font-medium text-violet-700"
              : "cursor-pointer rounded-full border border-gray-200 bg-white px-3 py-1 text-xs text-gray-500 transition hover:bg-gray-100"
          }
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
