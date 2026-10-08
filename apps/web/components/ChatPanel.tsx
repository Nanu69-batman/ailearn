"use client";

import { useEffect, useRef, useState } from "react";
import type { ChatMessage, ChatMode } from "../lib/api";
import {
  ApiError,
  formatApiError,
  getMessages,
  isSessionExpiredError,
  postChat,
} from "../lib/api";
import { mockChatAnswer, mockHistory } from "../lib/fixtures";
import MessageList from "./MessageList";
import ModeSelector from "./ModeSelector";

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

/**
 * 404 `session not found` means the backend session is gone (server restarted
 * without persistence, or state was reset). This is NOT a connectivity
 * problem: the server answered. Answering from mocks here would be
 * indistinguishable from a real answer and would destroy trust in every
 * citation, so it gets the explicit rejoin state instead.
 */
function isSessionGoneError(err: unknown): boolean {
  return err instanceof ApiError && err.status === 404;
}

/** Session is unusable (401 or 404) -> explicit rejoin state, never a mock. */
function isFatalSessionError(err: unknown): boolean {
  return isSessionExpiredError(err) || isSessionGoneError(err);
}

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
          if (isFatalSessionError(err)) {
            // 401 = session binding rejected, 404 = session gone server-side.
            // Either way the session is unusable -> distinct rejoin state.
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
      if (isFatalSessionError(err)) {
        // 401 = session binding rejected, 404 = session gone server-side.
        // Distinct rejoin state, and NEVER a mock answer: a canned reply is
        // indistinguishable from a real grounded one.
        setSessionExpired(true);
        setBackendUnreachable(false);
        setSendError(
          isSessionGoneError(err)
            ? `${SESSION_EXPIRED_COPY} (the server no longer has this session — it was lost on restart, so no answer can be grounded)`
            : SESSION_EXPIRED_COPY,
        );
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
    <section className="flex flex-col rounded-xl border border-gray-200 bg-white p-4 shadow-sm" aria-label="Chat with video">
      <div className="mb-3 flex items-center justify-between">
        <h2 className="text-base font-semibold text-gray-900">Ask this video</h2>
        {offline && <span className="rounded-full border border-amber-200 bg-amber-50 px-2.5 py-0.5 text-xs text-amber-600">offline mock</span>}
      </div>

      <ModeSelector value={mode} onChange={setMode} disabled={sending} />

      {sessionExpired && (
        <div className="mt-3 rounded-xl border border-red-200 bg-red-50 p-3 text-sm text-red-600" role="alert">
          {SESSION_EXPIRED_COPY}
          {onRejoin && (
            <div className="mt-2">
              <button type="button" onClick={onRejoin} className="rounded-lg bg-red-100 px-3 py-1 text-xs text-red-700 hover:bg-red-200">
                Rejoin session
              </button>
            </div>
          )}
        </div>
      )}

      {backendUnreachable && !sessionExpired && (
        <div className="mt-3 rounded-xl border border-amber-200 bg-amber-50 p-3 text-sm text-amber-700" role="alert">
          Backend unreachable — showing mock data.
        </div>
      )}

      {historyState === "loading" && (
        <div className="mt-3 text-sm text-gray-400" role="status">
          Loading conversation…
        </div>
      )}
      {historyState === "error" && (
        <div className="mt-3 rounded-xl border border-red-200 bg-red-50 p-3 text-sm text-red-600" role="alert">
          Could not load history: {historyError}
        </div>
      )}
      {(historyState === "ready" || messages.length > 0) && (
        <MessageList messages={messages} loading={sending} onSeek={onSeek} />
      )}

      {sendError && !sessionExpired && (
        <div className="mt-3 rounded-xl border border-amber-200 bg-amber-50 p-3 text-sm text-amber-700" role="alert">
          Live API error (answered from mock): {sendError}
        </div>
      )}
      {sendError && sessionExpired && (
        <div className="mt-3 rounded-xl border border-red-200 bg-red-50 p-3 text-sm text-red-600" role="alert">
          {sendError}
        </div>
      )}

      <form className="mt-3 flex gap-2" onSubmit={(e) => void handleSend(e)}>
        <label className="sr-only" htmlFor="chat-input">
          Ask a question about the video
        </label>
        <input
          id="chat-input"
          className="flex-1 rounded-xl border border-gray-200 bg-gray-50 px-3 py-2 text-sm text-gray-800 placeholder-gray-400 outline-none transition focus:border-violet-400 focus:bg-white focus:ring-2 focus:ring-violet-200"
          type="text"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Pause, then ask… e.g. what did that term mean?"
          disabled={sending}
          autoComplete="off"
        />
        <button type="submit" className="rounded-xl bg-violet-600 px-4 py-2 text-sm font-medium text-white transition hover:bg-violet-700 disabled:opacity-40" disabled={sending || input.trim().length === 0}>
          {sending ? "…" : "Send"}
        </button>
      </form>
      <div ref={bottomRef} />
    </section>
  );
}
