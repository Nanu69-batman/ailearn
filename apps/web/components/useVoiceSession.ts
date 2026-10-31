"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { ChatResponse } from "@/lib/api";
import { personalizationForRequest } from "@/lib/personalization";
import {
  PcmPlayer,
  fetchVoiceModels,
  micErrorMessage,
  startMicCapture,
  voiceAccessToken,
  voiceWsUrl,
  type MicCapture,
  type VoiceModels,
} from "@/lib/voice";

/** The `start` fields a composer already sends to streamChat, minus model and tokens. */
export interface VoiceStartFields {
  session_id: string;
  video_id?: string;
  current_time_ms: number;
  thread_id?: string;
  completed_resource_ids?: string[];
  scope_resource_ids?: string[];
  general?: boolean;
  mode?: string;
}

export type VoiceStatus = "connecting" | "live" | "ended" | "error";
export type VoiceActivity = "listening" | "thinking" | "speaking";

/** One spoken turn, as it is transcribed; the same id is reported again as the text grows. */
export interface VoiceTurn {
  id: string;
  role: "user" | "assistant";
  text: string;
}

interface Options {
  model: string;
  /** Prebuilt voice name and speaking tone (GET /voice/models lists both). */
  voice?: string;
  tone?: string;
  getStart: () => VoiceStartFields;
  /** Present where a playhead exists: sent as `time` every second. */
  getCurrentTimeMs?: () => number;
  /** A recap of the conversation so far (every 5 minutes and at the end); already saved to the chat. */
  onRecap: (response: ChatResponse) => void;
  /** Each spoken turn, so the chat can show the conversation as messages. */
  onTurn?: (turn: VoiceTurn) => void;
}

const MAX_PENDING = 30;
/** After End, how long the socket stays open for the closing recap. */
const RECAP_WAIT_MS = 30_000;

/** Voice models from GET /voice/models; null while loading, or when voice is off or unavailable. */
export function useVoiceModels(active: boolean): VoiceModels | null {
  const [models, setModels] = useState<VoiceModels | null>(null);
  useEffect(() => {
    if (!active) {
      setModels(null);
      return;
    }
    let cancelled = false;
    void fetchVoiceModels().then((m) => {
      if (!cancelled) setModels(m);
    });
    return () => {
      cancelled = true;
    };
  }, [active]);
  return models;
}

/** One live conversation over WS /voice/live; connects on mount and on model change, cleans up on unmount. */
export function useVoiceSession({ model, voice, tone, getStart, getCurrentTimeMs, onRecap, onTurn }: Options) {
  const [status, setStatus] = useState<VoiceStatus>("connecting");
  const [activity, setActivity] = useState<VoiceActivity>("listening");
  const [inputText, setInputText] = useState("");
  const [outputText, setOutputText] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  const wsRef = useRef<WebSocket | null>(null);
  const stopRef = useRef<() => void>(() => {});
  const cb = useRef({ getStart, getCurrentTimeMs, onRecap, onTurn });
  cb.current = { getStart, getCurrentTimeMs, onRecap, onTurn };

  useEffect(() => {
    const url = voiceWsUrl("live");
    let closed = false;
    let mic: MicCapture | null = null;
    let player: PcmPlayer | null = null;
    let timer: ReturnType<typeof setInterval> | null = null;
    let ws: WebSocket | null = null;
    let ready = false;
    // After End: the mic and audio are off, the socket waits for the recap.
    let draining = false;
    let lastKind: "input" | "output" | null = null;
    let turn: VoiceTurn | null = null;
    // Final input segments of the current turn; an interim one is shown after them.
    let heard = "";
    const report = (role: VoiceTurn["role"], text: (prev: string) => string): string => {
      if (!turn || turn.role !== role) {
        if (role === "user") heard = "";
        turn = { id: `voice-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`, role, text: "" };
      }
      turn = { ...turn, text: text(turn.text) };
      if (turn.text.trim()) cb.current.onTurn?.(turn);
      return turn.text;
    };
    const pending: ArrayBuffer[] = [];

    setStatus("connecting");
    setActivity("listening");
    setInputText("");
    setOutputText("");
    setError(null);

    const silence = (): void => {
      if (timer) clearInterval(timer);
      timer = null;
      mic?.stop();
      mic = null;
      player?.close();
      player = null;
    };
    const cleanup = (): void => {
      closed = true;
      silence();
      // Ending (not unmounting mid-call): keep the socket for the closing recap.
      if (draining && ws) {
        const sock = ws;
        ws = null;
        wsRef.current = null;
        const done = window.setTimeout(() => sock.close(), RECAP_WAIT_MS);
        sock.onclose = () => window.clearTimeout(done);
        sock.onerror = () => window.clearTimeout(done);
        return;
      }
      if (ws) {
        ws.onmessage = null;
        ws.onclose = null;
        ws.onerror = null;
        try {
          ws.close();
        } catch {
          // already closed
        }
      }
      ws = null;
      wsRef.current = null;
    };
    const finish = (s: VoiceStatus, message?: string): void => {
      if (closed) return;
      cleanup();
      if (message) setError(message);
      setStatus(s);
    };
    stopRef.current = () => {
      if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: "stop" }));
        draining = true;
      }
      finish("ended");
    };

    async function run(): Promise<void> {
      if (!url) return finish("error", "Voice needs the live API.");
      try {
        // The panel was opened by a click, so the browser lets this context play.
        player = new PcmPlayer();
        const token = await voiceAccessToken();
        if (closed) return;
        const sock = new WebSocket(url);
        ws = sock;
        wsRef.current = sock;
        sock.binaryType = "arraybuffer";

        const personal = personalizationForRequest();
        sock.onopen = () =>
          sock.send(
            JSON.stringify({
              type: "start",
              model,
              ...(voice ? { voice } : {}),
              ...(tone ? { tone } : {}),
              ...cb.current.getStart(),
              ...(personal ? { personalization: personal } : {}),
              ...(token ? { access_token: token } : {}),
            }),
          );
        sock.onmessage = (e) => {
          if (typeof e.data !== "string") {
            player?.enqueue(e.data as ArrayBuffer);
            return;
          }
          let msg: Record<string, unknown>;
          try {
            msg = JSON.parse(e.data);
          } catch {
            return;
          }
          // The recap can arrive after End, when the rest is already torn down.
          if (msg.type === "recap") {
            if (msg.response) cb.current.onRecap(msg.response as ChatResponse);
            if (closed) sock.close();
            return;
          }
          if (closed) {
            if (msg.type === "closed") sock.close();
            return;
          }
          switch (msg.type) {
            case "ready":
              ready = true;
              for (const chunk of pending.splice(0)) sock.send(chunk);
              setStatus("live");
              break;
            case "state":
              setActivity(msg.state as VoiceActivity);
              break;
            case "input_transcript": {
              // A new turn starts once the model has spoken: clear the last one.
              if (lastKind === "output") {
                setOutputText("");
                setInputText("");
              }
              lastKind = "input";
              const t = String(msg.text ?? "").trim();
              const shown = report("user", () => {
                const full = [heard, t].filter(Boolean).join(" ");
                if (msg.final) heard = full;
                return full;
              });
              setInputText(shown);
              break;
            }
            case "output_transcript": {
              lastKind = "output";
              const t = String(msg.text ?? "");
              // Accumulated text replaces; a fragment is appended.
              setOutputText((prev) => (t.startsWith(prev) ? t : prev + t));
              report("assistant", (prev) => (t.startsWith(prev) ? t : prev + t));
              break;
            }
            case "interrupted":
              player?.flush();
              break;
            case "error":
              finish("error", String(msg.message || "Voice failed."));
              break;
            case "closed":
              finish("ended");
              break;
          }
        };
        sock.onerror = () => finish("error", "Could not reach the voice service.");
        sock.onclose = (e) => {
          if (e.code === 4401 || e.code === 4403 || e.code === 4404) {
            finish("error", "Voice was refused for this session. Rejoin and try again.");
          } else if (e.code === 4408) finish("ended", "The voice session reached its time limit.");
          else finish("ended");
        };

        timer = setInterval(() => {
          const t = cb.current.getCurrentTimeMs;
          if (t && ready && sock.readyState === WebSocket.OPEN) {
            sock.send(JSON.stringify({ type: "time", current_time_ms: t() }));
          }
        }, 1000);

        const m = await startMicCapture((chunk) => {
          if (sock.readyState !== WebSocket.OPEN) return;
          if (ready) sock.send(chunk);
          else if (pending.length < MAX_PENDING) pending.push(chunk);
        });
        if (closed) m.stop();
        else mic = m;
      } catch (err) {
        finish("error", micErrorMessage(err));
      }
    }
    void run();
    return cleanup;
  }, [model, voice, tone, attempt]);

  const end = useCallback(() => stopRef.current(), []);
  const restart = useCallback(() => setAttempt((n) => n + 1), []);
  /** A typed turn inside the voice session. */
  const sendText = useCallback((text: string) => {
    const ws = wsRef.current;
    if (ws && ws.readyState === WebSocket.OPEN && text.trim()) ws.send(JSON.stringify({ type: "text", text }));
  }, []);

  return { status, activity, inputText, outputText, error, end, restart, sendText };
}
