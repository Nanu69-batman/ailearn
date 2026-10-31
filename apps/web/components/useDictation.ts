"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { micErrorMessage, startMicCapture, voiceAccessToken, voiceWsUrl, type MicCapture } from "@/lib/voice";

export interface DictationSession {
  sessionId: string;
}

export type DictationState = "idle" | "connecting" | "recording";

interface Options {
  /** The chat's session; may create one first. Null: nothing to dictate into. */
  getSession: () => Promise<DictationSession | null>;
  /** A finished phrase, to append to the input. */
  onFinal: (text: string) => void;
}

const ERROR_MS = 6000;
/** After stop, how long to wait for the server to relay its last finals. */
const DRAIN_MS = 5000;
/** Audio held back until the server says ready (~3 s of 100 ms chunks). */
const MAX_PENDING = 30;

function closeMessage(code: number): string | null {
  if (code === 4401 || code === 4403 || code === 4404) return "Dictation was refused for this session. Rejoin and try again.";
  if (code === 4408) return "Dictation reached its time limit.";
  return null;
}

/** Dictation over WS /voice/transcribe: `interim` is the live partial, finals go to onFinal. */
export function useDictation({ getSession, onFinal }: Options) {
  const [state, setState] = useState<DictationState>("idle");
  const [interim, setInterim] = useState("");
  const [error, setError] = useState<string | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const micRef = useRef<MicCapture | null>(null);
  const drainRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const errTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const gen = useRef(0);
  const cb = useRef({ getSession, onFinal });
  cb.current = { getSession, onFinal };

  const fail = useCallback((message: string) => {
    setError(message);
    if (errTimer.current) clearTimeout(errTimer.current);
    errTimer.current = setTimeout(() => setError(null), ERROR_MS);
  }, []);

  /** Release the microphone and the socket. */
  const teardown = useCallback(() => {
    gen.current++;
    micRef.current?.stop();
    micRef.current = null;
    if (drainRef.current) clearTimeout(drainRef.current);
    drainRef.current = null;
    const ws = wsRef.current;
    wsRef.current = null;
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
    setInterim("");
    setState("idle");
  }, []);

  /** Stop listening; the server still relays any final it owes, then closes. */
  const stop = useCallback(() => {
    micRef.current?.stop();
    micRef.current = null;
    setInterim("");
    setState("idle");
    const ws = wsRef.current;
    if (!ws) return;
    if (ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: "stop" }));
      drainRef.current = setTimeout(teardown, DRAIN_MS);
    } else {
      teardown();
    }
  }, [teardown]);

  const start = useCallback(async () => {
    const url = voiceWsUrl("transcribe");
    if (!url) return fail("Dictation needs the live API.");
    teardown();
    setError(null);
    const mine = gen.current;
    setState("connecting");
    try {
      const session = await cb.current.getSession();
      if (gen.current !== mine) return;
      if (!session) {
        setState("idle");
        return fail("Dictation is not available here.");
      }
      const token = await voiceAccessToken();
      if (gen.current !== mine) return;

      const ws = new WebSocket(url);
      ws.binaryType = "arraybuffer";
      wsRef.current = ws;
      let ready = false;
      const pending: ArrayBuffer[] = [];

      ws.onopen = () =>
        ws.send(
          JSON.stringify({
            type: "start",
            session_id: session.sessionId,
            ...(token ? { access_token: token } : {}),
          }),
        );
      ws.onmessage = (e) => {
        if (typeof e.data !== "string") return;
        let msg: { type?: string; text?: string; message?: string };
        try {
          msg = JSON.parse(e.data);
        } catch {
          return;
        }
        if (msg.type === "ready") {
          ready = true;
          for (const chunk of pending.splice(0)) ws.send(chunk);
          setState((s) => (s === "connecting" ? "recording" : s));
        } else if (msg.type === "interim") {
          setInterim(msg.text ?? "");
        } else if (msg.type === "final") {
          setInterim("");
          if (msg.text) cb.current.onFinal(msg.text);
        } else if (msg.type === "error") {
          fail(msg.message || "Dictation failed.");
          teardown();
        } else if (msg.type === "closed") {
          teardown();
        }
      };
      ws.onerror = () => {
        if (gen.current === mine && wsRef.current === ws) {
          fail("Could not reach the dictation service.");
          teardown();
        }
      };
      ws.onclose = (e) => {
        if (gen.current !== mine || wsRef.current !== ws) return;
        const msg = closeMessage(e.code);
        if (msg) fail(msg);
        else if (!e.wasClean && micRef.current) fail("Dictation connection lost.");
        teardown();
      };

      const mic = await startMicCapture((chunk) => {
        if (ws.readyState !== WebSocket.OPEN) return;
        if (ready) ws.send(chunk);
        else if (pending.length < MAX_PENDING) pending.push(chunk);
      });
      if (gen.current !== mine) {
        mic.stop();
        return;
      }
      micRef.current = mic;
    } catch (err) {
      if (gen.current !== mine) return;
      teardown();
      fail(micErrorMessage(err));
    }
  }, [fail, teardown]);

  const toggle = useCallback(() => {
    if (state === "idle") void start();
    else stop();
  }, [state, start, stop]);

  useEffect(
    () => () => {
      teardown();
      if (errTimer.current) clearTimeout(errTimer.current);
    },
    [teardown],
  );

  return { state, interim, error, toggle, stop };
}
