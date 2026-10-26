"use client";

import { useEffect, useMemo, useState } from "react";
import type { ChatResponse } from "@/lib/api";
import type { VoiceModels } from "@/lib/voice";
import Select from "./Select";
import { useVoiceSession, type VoiceActivity, type VoiceStartFields } from "./useVoiceSession";

interface VoicePanelProps {
  models: VoiceModels;
  getStart: () => VoiceStartFields;
  getCurrentTimeMs?: () => number;
  /** A recap of the conversation (every 5 minutes and at the end), already saved to the chat. */
  onRecap: (response: ChatResponse) => void;
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
    <div className={`relative flex h-40 w-40 items-center justify-center ${live ? "" : "opacity-50 grayscale"}`} aria-hidden="true">
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
      {live && activity !== "thinking" && <span className={`orb-ring absolute inset-4 rounded-full ${o.ring}`} />}
      <span
        className={`${o.anim} block h-28 w-28 rounded-full bg-gradient-to-br shadow-xl shadow-violet-500/30 transition-all duration-500 ${o.core} ${
          activity === "speaking" ? "scale-110" : ""
        }`}
      >
        <span className="block h-full w-full rounded-full bg-[radial-gradient(circle_at_30%_25%,rgba(255,255,255,0.65),transparent_55%)]" />
      </span>
    </div>
  );
}

/** The voice conversation, laid over the chat. Mount it to connect; unmount (or End) to disconnect. */
export default function VoicePanel({ models, getStart, getCurrentTimeMs, onRecap, onClose, subtitle }: VoicePanelProps) {
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
  const { status, activity, inputText, outputText, error, end, restart } = useVoiceSession({
    model,
    voice,
    tone,
    getStart,
    getCurrentTimeMs,
    onRecap,
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

  return (
    <div role="dialog" aria-label="Voice conversation with Malai" className="flex h-full w-full flex-col bg-white">
      <div className="flex shrink-0 items-center gap-3 border-b border-gray-100 px-4 py-3">
        <div className="min-w-0 flex-1">
          <h2 className="text-sm font-semibold text-gray-900">Talk with Malai</h2>
          {subtitle && <p className="truncate text-xs text-gray-500">{subtitle}</p>}
        </div>
        <Select<string> className="w-64 max-w-[60%]" label="Voice model" value={model} options={options} onChange={(k) => { setModel(k); writePref(MODEL_KEY, k); }} />
      </div>
      {(voiceOptions.length > 0 || toneOptions.length > 0) && (
        <div className="flex shrink-0 flex-wrap items-center gap-2 border-b border-gray-100 px-4 py-2">
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

      <div className="flex min-h-0 flex-1 flex-col items-center gap-3 overflow-y-auto px-5 py-6 text-center">
        <Orb activity={activity} live={live} />
        <p className="text-sm font-medium text-violet-700" role="status" aria-live="polite">
          {label}
        </p>
        {error && (
          <p className="max-w-sm rounded-xl border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700" role="alert">
            {error}
          </p>
        )}
        <div className="w-full max-w-md space-y-3 text-left">
          {inputText && (
            <p className="rounded-2xl bg-violet-50 px-4 py-2.5 text-sm text-gray-800">
              <span className="mb-0.5 block text-[11px] font-medium uppercase tracking-wide text-violet-500">You</span>
              {inputText}
            </p>
          )}
          {outputText && (
            <p className="rounded-2xl border border-gray-200 bg-white px-4 py-2.5 text-sm text-gray-800">
              <span className="mb-0.5 block text-[11px] font-medium uppercase tracking-wide text-gray-400">Malai</span>
              {outputText}
            </p>
          )}
        </div>
        {live && !inputText && !outputText && <p className="text-xs text-gray-400">Ask about the lesson out loud. A recap is added to this chat when you end, and every 5 minutes.</p>}
      </div>

      <div className="flex shrink-0 items-center justify-center gap-3 border-t border-gray-100 px-4 py-3">
        {(status === "ended" || status === "error") && (
          <button
            type="button"
            onClick={restart}
            className="rounded-full border border-violet-200 bg-white px-5 py-2 text-sm font-medium text-violet-700 transition hover:bg-violet-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-violet-400"
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
          className="rounded-full bg-red-500 px-6 py-2 text-sm font-semibold text-white shadow-md shadow-red-500/30 transition hover:bg-red-600 focus:outline-none focus-visible:ring-2 focus-visible:ring-red-400 focus-visible:ring-offset-2"
        >
          End
        </button>
      </div>
    </div>
  );
}
