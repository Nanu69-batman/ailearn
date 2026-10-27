"use client";

import { useEffect, useMemo, useState } from "react";
import type { ChatResponse } from "@/lib/api";
import type { VoiceModels } from "@/lib/voice";
import Select from "./Select";
import { useVoiceSession, type VoiceActivity, type VoiceStartFields, type VoiceTurn } from "./useVoiceSession";

interface VoicePanelProps {
  models: VoiceModels;
  getStart: () => VoiceStartFields;
  getCurrentTimeMs?: () => number;
  /** A recap of the conversation (every 5 minutes and at the end), already saved to the chat. */
  onRecap: (response: ChatResponse) => void;
  /** Each spoken turn, shown in the chat as a message. */
  onTurn: (turn: VoiceTurn) => void;
  onClose: () => void;
  /** Where the learner is talking, for the heading ("this video"). */
  subtitle?: string;
}

const MODEL_KEY = "ailearn.voice_model";
const VOICE_KEY = "ailearn.voice_name";
const TONE_KEY = "ailearn.voice_tone";

function readPref(key: string): string | null {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

function writePref(key: string, value: string): void {
  try {
    window.localStorage.setItem(key, value);
  } catch {
    // Storage unavailable -> the choice lasts for this panel only.
  }
}

const ORB: Record<VoiceActivity, { label: string; core: string; ring: string; anim: string }> = {
  listening: {
    label: "Listening…",
    core: "from-violet-300 via-violet-500 to-indigo-500",
    ring: "bg-violet-300/40",
    anim: "orb-breathe",
  },
  thinking: {
    label: "Thinking…",
    core: "from-indigo-300 via-sky-400 to-violet-500",
    ring: "bg-sky-300/40",
    anim: "orb-think",
  },
  speaking: {
    label: "Speaking…",
    core: "from-fuchsia-300 via-violet-500 to-purple-600",
    ring: "bg-fuchsia-300/40",
    anim: "orb-speak",
  },
};

/** A calm orb: breathes while listening, swirls while thinking, pulses while speaking. */
function Orb({ activity, live }: { activity: VoiceActivity; live: boolean }) {
  const o = ORB[activity];
  return (
    <div className={`relative flex h-12 w-12 shrink-0 items-center justify-center ${live ? "" : "opacity-50 grayscale"}`} aria-hidden="true">
      <style>{`
        @keyframes orb-breathe { 0%,100% { transform: scale(0.94); } 50% { transform: scale(1.04); } }
        @keyframes orb-think { 0% { transform: rotate(0deg) scale(0.98); } 50% { transform: rotate(180deg) scale(1.03); } 100% { transform: rotate(360deg) scale(0.98); } }
        @keyframes orb-speak { 0%,100% { transform: scale(0.96); } 25% { transform: scale(1.08); } 60% { transform: scale(1.0); } 80% { transform: scale(1.1); } }
        @keyframes orb-ring { 0% { transform: scale(0.9); opacity: 0.7; } 100% { transform: scale(1.5); opacity: 0; } }
        .orb-breathe { animation: orb-breathe 4.5s ease-in-out infinite; }
        .orb-think { animation: orb-think 2.6s linear infinite; }
        .orb-speak { animation: orb-speak 1.3s ease-in-out infinite; }
        .orb-ring { animation: orb-ring 2.4s ease-out infinite; }
        @media (prefers-reduced-motion: reduce) {
          .orb-breathe, .orb-think, .orb-speak, .orb-ring { animation: none; }
        }
      `}</style>
      {live && activity !== "thinking" && <span className={`orb-ring absolute inset-1 rounded-full ${o.ring}`} />}
      <span
        className={`${o.anim} block h-9 w-9 rounded-full bg-gradient-to-br shadow-lg shadow-violet-500/30 transition-all duration-500 ${o.core} ${
          activity === "speaking" ? "scale-110" : ""
        }`}
      >
        <span className="block h-full w-full rounded-full bg-[radial-gradient(circle_at_30%_25%,rgba(255,255,255,0.65),transparent_55%)]" />
      </span>
    </div>
  );
}

/** The voice conversation bar, in place of the composer; the turns go to the chat. Mount to connect, unmount (or End) to disconnect. */
export default function VoicePanel({ models, getStart, getCurrentTimeMs, onRecap, onTurn, onClose, subtitle }: VoicePanelProps) {
  const options = useMemo(() => models.dialog.map((m) => ({ value: m.key, label: m.label })), [models]);
  const voiceOptions = useMemo(
    () => (models.voices ?? []).map((v) => ({ value: v.name, label: `${v.name} · ${v.description}` })),
    [models],
  );
  const toneOptions = useMemo(() => (models.tones ?? []).map((t) => ({ value: t.key, label: t.label })), [models]);
  const [model, setModel] = useState<string>(() => {
    const saved = readPref(MODEL_KEY);
    return models.dialog.find((m) => m.key === saved)?.key ?? models.dialog.find((m) => m.key === "live")?.key ?? models.dialog[0]?.key ?? "live";
  });
  const [voice, setVoice] = useState<string | undefined>(() => {
    const saved = readPref(VOICE_KEY);
    return models.voices?.find((v) => v.name === saved)?.name ?? models.default_voice;
  });
  const [tone, setTone] = useState<string | undefined>(() => {
    const saved = readPref(TONE_KEY);
    return models.tones?.find((t) => t.key === saved)?.key ?? models.default_tone;
  });
  const { status, activity, error, end, restart } = useVoiceSession({
    model,
    voice,
    tone,
    getStart,
    getCurrentTimeMs,
    onRecap,
    onTurn,
  });

  // Esc ends the conversation, like the End button.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !e.defaultPrevented) {
        end();
        onClose();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [end, onClose]);

  const live = status === "live";
  const label =
    status === "connecting" ? "Connecting…" : status === "live" ? ORB[activity].label : status === "ended" ? "Conversation ended" : "Voice unavailable";

  const [showSettings, setShowSettings] = useState(false);

  return (
    <div
      aria-label="Voice conversation with Malai"
      className="rounded-[1.5rem] border border-violet-200 bg-white px-3 py-2 shadow-lg shadow-violet-900/5"
    >
      <div className="flex items-center gap-3">
        <Orb activity={activity} live={live} />
        <div className="min-w-0 flex-1">
          <p className="text-sm font-medium text-violet-700" role="status" aria-live="polite">
            {label}
          </p>
          <p className="truncate text-xs text-gray-500">
            {live ? "Talk with Malai" : "Voice"}
            {subtitle ? ` · ${subtitle}` : ""}
          </p>
        </div>
        <button
          type="button"
          onClick={() => setShowSettings((v) => !v)}
          aria-expanded={showSettings}
          aria-label="Voice settings"
          title="Voice settings"
          className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full text-gray-400 transition hover:bg-gray-100 hover:text-gray-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-violet-400"
        >
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="h-5 w-5" aria-hidden="true">
            <path d="M4 21v-7M4 10V3M12 21v-9M12 8V3M20 21v-5M20 12V3M1 14h6M9 8h6M17 16h6" />
          </svg>
        </button>
        {(status === "ended" || status === "error") && (
          <button
            type="button"
            onClick={restart}
            className="shrink-0 rounded-full border border-violet-200 bg-white px-4 py-1.5 text-sm font-medium text-violet-700 transition hover:bg-violet-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-violet-400"
          >
            Start again
          </button>
        )}
        <button
          type="button"
          onClick={() => {
            end();
            onClose();
          }}
          className="shrink-0 rounded-full bg-red-500 px-5 py-1.5 text-sm font-semibold text-white shadow-md shadow-red-500/30 transition hover:bg-red-600 focus:outline-none focus-visible:ring-2 focus-visible:ring-red-400 focus-visible:ring-offset-2"
        >
          End
        </button>
      </div>
      {error && (
        <p className="mt-2 rounded-xl border border-red-200 bg-red-50 px-3 py-1.5 text-xs text-red-700" role="alert">
          {error}
        </p>
      )}
      {showSettings && (
        <div className="mt-2 flex flex-wrap items-center gap-2 border-t border-gray-100 pt-2">
          <Select<string> className="min-w-[10rem] flex-1" label="Voice model" value={model} options={options} onChange={(k) => { setModel(k); writePref(MODEL_KEY, k); }} />
          {voiceOptions.length > 0 && voice && (
            <Select<string>
              className="min-w-[10rem] flex-1"
              label="Voice"
              value={voice}
              options={voiceOptions}
              onChange={(v) => {
                setVoice(v);
                writePref(VOICE_KEY, v);
              }}
            />
          )}
          {toneOptions.length > 0 && tone && (
            <Select<string>
              className="min-w-[8rem] flex-1"
              label="Tone"
              value={tone}
              options={toneOptions}
              onChange={(t) => {
                setTone(t);
                writePref(TONE_KEY, t);
              }}
            />
          )}
        </div>
      )}
    </div>
  );
}
