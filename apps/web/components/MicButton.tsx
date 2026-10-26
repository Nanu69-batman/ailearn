"use client";

import type { DictationState } from "./useDictation";

interface MicButtonProps {
  state: DictationState;
  onClick: () => void;
  error?: string | null;
  disabled?: boolean;
  /** Matches the composer's send button. */
  size?: "md" | "lg";
}

/** The composer's dictation toggle: red and pulsing while recording; errors show briefly above it. */
export default function MicButton({ state, onClick, error, disabled, size = "md" }: MicButtonProps) {
  const recording = state === "recording";
  const connecting = state === "connecting";
  const dim = size === "lg" ? "h-12 w-12" : "h-10 w-10";
  return (
    <span className="relative flex shrink-0">
      <button
        type="button"
        onClick={onClick}
        disabled={disabled}
        aria-pressed={recording}
        aria-label={recording ? "Stop dictation" : connecting ? "Starting dictation" : "Dictate with your voice"}
        title={recording ? "Stop dictation" : "Dictate"}
        className={`relative flex ${dim} items-center justify-center rounded-full transition focus:outline-none focus-visible:ring-2 focus-visible:ring-violet-400 disabled:opacity-40 ${
          recording ? "bg-red-500 text-white shadow-md shadow-red-500/30 hover:bg-red-600" : "text-gray-400 hover:bg-gray-100 hover:text-gray-700"
        }`}
      >
        {recording && (
          <span className="absolute inset-0 rounded-full bg-red-400/50 motion-safe:animate-ping" aria-hidden="true" />
        )}
        {connecting ? (
          <span className="h-4 w-4 animate-spin rounded-full border-2 border-violet-500 border-t-transparent" aria-hidden="true" />
        ) : (
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="relative h-5 w-5" aria-hidden="true">
            <rect x="9" y="3" width="6" height="12" rx="3" />
            <path d="M5 11a7 7 0 0014 0M12 18v3" />
          </svg>
        )}
      </button>
      {error && (
        <span
          role="alert"
          className="absolute bottom-full right-0 z-30 mb-2 w-max max-w-[16rem] rounded-xl border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700 shadow-lg"
        >
          {error}
        </span>
      )}
    </span>
  );
}

/** The composer's voice-conversation button (opens the voice panel). */
export function VoiceButton({ onClick, disabled, size = "md" }: { onClick: () => void; disabled?: boolean; size?: "md" | "lg" }) {
  const dim = size === "lg" ? "h-12 w-12" : "h-10 w-10";
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      aria-label="Talk with Malai"
      title="Talk with Malai"
      className={`flex ${dim} shrink-0 items-center justify-center rounded-full text-violet-600 transition hover:bg-violet-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-violet-400 disabled:opacity-40`}
    >
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="h-5 w-5" aria-hidden="true">
        <path d="M4 10v4M8 6v12M12 3v18M16 7v10M20 10v4" />
      </svg>
    </button>
  );
}
