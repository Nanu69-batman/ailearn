"use client";

import type { ChatMode } from "../lib/api";
import styles from "./ModeSelector.module.css";

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
    <div className={styles.group} role="radiogroup" aria-label="Answer mode">
      {MODES.map((m) => (
        <label
          key={m.value}
          className={`${styles.chip} ${value === m.value ? styles.active : ""}`}
          title={m.hint}
        >
          <input
            type="radio"
            name="chat-mode"
            value={m.value}
            checked={value === m.value}
            disabled={disabled}
            onChange={() => onChange(m.value)}
            className={styles.radio}
          />
          {m.label}
        </label>
      ))}
    </div>
  );
}
