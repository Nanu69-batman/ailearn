"use client";

import { useEffect, useRef, useState } from "react";
import type { ChatMessage, ChatMode } from "../lib/api";
import {
  formatApiError,
  getMessages,
  isSessionExpiredError,
  postChat,
} from "../lib/api";
import { mockChatAnswer, mockHistory } from "../lib/fixtures";
import MessageList from "./MessageList";
import ModeSelector from "./ModeSelector";
import styles from "./ChatPanel.module.css";

interface ChatPanelProps {
  sessionId: string;
  sessionToken: string;
  videoId: string;
  getCurrentTimeMs: () => number;
  onSeek: (startMs: number) => void;
  offline: boolean;
  onRejoin?: () => void;
}

function newId(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }
  return `msg-${Date.now()}-${Math.floor(Math.random() * 1e6)}`;
}

const SESSION_EXPIRED_COPY = "Session expired — please rejoin to continue.";

export default function ChatPanel({
  sessionId,
  sessionToken,
  videoId,
  getCurrentTimeMs,
  onSeek,
  offline,
  onRejoin,
}: ChatPanelProps) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [historyState, setHistoryState] = useState<"loading" | "ready" | "error">("loading");
  const [historyError, setHistoryError] = useState<string | null>(null);
  const [mode, setMode] = useState<ChatMode>("qa");
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [sendError, setSendError] = useState<string | null>(null);
  // Distinct 401 state: session binding rejected — must rejoin, never a
  // silent mock fallback.
  const [sessionExpired, setSessionExpired] = useState(false);
  // Persistent banner state: once we fall back to mock data (offline prop or
  // a live-API failure), the panel stays visibly marked until remount.
  const [backendUnreachable, setBackendUnreachable] = useState(offline);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let cancelled = false;
    async function load(): Promise<void> {
      setHistoryState("loading");
      setHistoryError(null);
      setSessionExpired(false);
      if (offline) {
        setMessages(mockHistory);
        setHistoryState("ready");
        return;
      }
      try {
        const history = await getMessages(sessionId, sessionToken);
        if (!cancelled) {
          setMessages(history);
          setHistoryState("ready");
        }
      } catch (err) {
        if (!cancelled) {
          if (isSessionExpiredError(err)) {
            // 401 = session binding rejected — distinct rejoin state.
            setSessionExpired(true);
            setHistoryError(SESSION_EXPIRED_COPY);
          } else {
            setHistoryError(formatApiError(err));
          }
          setHistoryState("error");
        }
      }
    }
    void load();
    return () => {
      cancelled = true;
    };
  }, [sessionId, sessionToken, offline]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, sending]);

  async function handleSend(e: React.FormEvent): Promise<void> {
    e.preventDefault();
    const text = input.trim();
    if (!text || sending) return;
    const atMs = getCurrentTimeMs();
    const userMsg: ChatMessage = {
      id: newId(),
      role: "user",
      content: text,
      citations: [],
      created_at: new Date().toISOString(),
    };
    setMessages((prev) => [...prev, userMsg]);
    setInput("");
    setSending(true);
    setSendError(null);
    try {
      const res = offline
        ? mockChatAnswer(mode, text)
        : await postChat({
            session_id: sessionId,
            session_token: sessionToken,
            video_id: videoId,
            current_time_ms: atMs,
            message: text,
            mode,
          });
      const assistantMsg: ChatMessage = {
        id: newId(),
        role: "assistant",
        content: res.refused ? `I can't answer from watched content yet.\n\n${res.answer}` : res.answer,
        citations: res.citations,
        created_at: new Date().toISOString(),
      };
      setMessages((prev) => [...prev, assistantMsg]);
    } catch (err) {
      if (isSessionExpiredError(err)) {
        // 401 = session binding rejected — distinct rejoin state, no mock
        // fallback so the user cannot mistake it for a live answer.
        setSessionExpired(true);
        setSendError(SESSION_EXPIRED_COPY);
        return;
      }
      // Graceful fallback: still answer from fixtures so the demo works
      // without a live backend, but surface the error.
      const fallback = mockChatAnswer(mode, text);
      const assistantMsg: ChatMessage = {
        id: newId(),
        role: "assistant",
        content: `${fallback.answer}\n\n(offline fallback — live API unreachable)`,
        citations: fallback.citations,
        created_at: new Date().toISOString(),
      };
      setMessages((prev) => [...prev, assistantMsg]);
      setSendError(formatApiError(err));
      setBackendUnreachable(true);
    } finally {
      setSending(false);
    }
  }

  return (
    <section className={styles.panel} aria-label="Chat with video">
      <div className={styles.header}>
        <h2 className={styles.title}>Ask this video</h2>
        {offline && <span className={styles.offline}>offline mock</span>}
      </div>

      <ModeSelector value={mode} onChange={setMode} disabled={sending} />

      {sessionExpired && (
        <div className={styles.error} role="alert">
          {SESSION_EXPIRED_COPY}
          {onRejoin && (
            <div style={{ marginTop: "0.5rem" }}>
              <button type="button" onClick={onRejoin}>
                Rejoin session
              </button>
            </div>
          )}
        </div>
      )}

      {backendUnreachable && !sessionExpired && (
        <div className={styles.error} role="alert">
          Backend unreachable — showing mock data.
        </div>
      )}

      {historyState === "loading" && (
        <div className={styles.status} role="status">
          Loading conversation…
        </div>
      )}
      {historyState === "error" && (
        <div className={styles.error} role="alert">
          Could not load history: {historyError}
        </div>
      )}
      {(historyState === "ready" || messages.length > 0) && (
        <MessageList messages={messages} loading={sending} onSeek={onSeek} />
      )}

      {sendError && !sessionExpired && (
        <div className={styles.error} role="alert">
          Live API error (answered from mock): {sendError}
        </div>
      )}
      {sendError && sessionExpired && (
        <div className={styles.error} role="alert">
          {sendError}
        </div>
      )}

      <form className={styles.form} onSubmit={(e) => void handleSend(e)}>
        <label className={styles.srOnly} htmlFor="chat-input">
          Ask a question about the video
        </label>
        <input
          id="chat-input"
          className={styles.input}
          type="text"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Pause, then ask… e.g. what did that term mean?"
          disabled={sending}
          autoComplete="off"
        />
        <button type="submit" className={styles.send} disabled={sending || input.trim().length === 0}>
          {sending ? "…" : "Send"}
        </button>
      </form>
      <div ref={bottomRef} />
    </section>
  );
}
