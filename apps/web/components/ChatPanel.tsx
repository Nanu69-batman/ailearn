"use client";

import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import type { ChatMessage, ChatMode, LiveFrameCapture } from "../lib/api";
import {
  ApiError,
  formatApiError,
  getMessages,
  isFrameQuestion,
  isSessionExpiredError,
  postChat,
  postChatWithFrame,
  streamChat,
  listThreads,
  createThread,
  renameThread,
  deleteThread,
  MAIN_THREAD_ID,
  type ChatThread,
} from "../lib/api";
import { mockChatAnswer, mockHistory } from "../lib/fixtures";
import MessageList from "./MessageList";
import ChatThreads from "./ChatThreads";
import type { CitationBadgeProps } from "./CitationBadge";
import ModeSelector from "./ModeSelector";
import QuizPanel from "./QuizPanel";

interface ChatPanelProps {
  sessionId: string;
  sessionToken: string;
  videoId: string;
  getCurrentTimeMs: () => number;
  /**
   * Freezes the frame currently on screen and the playhead position it was
   * painted from, in one synchronous tick. Supplied by the player page; a panel
   * without one (or a document, which has no video element) simply never
   * captures, and every question falls back to the ordinary path.
   */
  captureFrame?: () => Promise<LiveFrameCapture | null>;
  onSeek: (startMs: number) => void;
  /** The resource open beside the chat; its citations are shown there on click. */
  openDoc?: CitationBadgeProps["openDoc"];
  /**
   * Other materials the learner marked done. The chat reads the open
   * material plus these, and nothing else from the course.
   */
  completedResourceIds?: string[];
  offline: boolean;
  onRejoin?: () => void;
  /**
   * Fill the parent's height: the messages scroll inside the panel and the
   * composer stays at the bottom. The parent must give the panel a height.
   */
  fill?: boolean;
  /** Before the avatar, e.g. a drag handle. */
  headerLead?: ReactNode;
  /** Right end of the header, e.g. window buttons. */
  headerActions?: ReactNode;
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

/** The chat a learner last had open, per session; survives a reload. */
function threadKey(sessionId: string): string {
  return `ailearn.chat_thread.${sessionId}`;
}

function readThread(sessionId: string): string {
  try {
    return window.localStorage.getItem(threadKey(sessionId)) || MAIN_THREAD_ID;
  } catch {
    return MAIN_THREAD_ID;
  }
}

function writeThread(sessionId: string, threadId: string): void {
  try {
    window.localStorage.setItem(threadKey(sessionId), threadId);
  } catch {
    // Storage unavailable -> the choice lasts for this page only.
  }
}

function fmtMs(ms: number): string {
  const s = Math.max(0, Math.floor(ms / 1000));
  return `${Math.floor(s / 60)}:${(s % 60).toString().padStart(2, "0")}`;
}

export default function ChatPanel({
  sessionId,
  sessionToken,
  videoId,
  getCurrentTimeMs,
  captureFrame,
  onSeek,
  openDoc,
  completedResourceIds = [],
  offline,
  onRejoin,
  fill = false,
  headerLead,
  headerActions,
}: ChatPanelProps) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [historyState, setHistoryState] = useState<"loading" | "ready" | "error">("loading");
  const [historyError, setHistoryError] = useState<string | null>(null);
  const [mode, setMode] = useState<ChatMode>("qa");
  const [tab, setTab] = useState<"chat" | "quiz">("chat");
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  // The answer as it is being written (POST /chat/stream). Only a preview: the
  // validated response replaces it, and a refusal replaces it entirely.
  const [preview, setPreview] = useState<{ status: string | null; text: string } | null>(null);
  const [sendError, setSendError] = useState<string | null>(null);
  // Distinct 401 state: session binding rejected — must rejoin, never a
  // silent mock fallback.
  const [sessionExpired, setSessionExpired] = useState(false);
  // Persistent banner state: once we fall back to mock data (offline prop or
  // a live-API failure), the panel stays visibly marked until remount.
  const [backendUnreachable, setBackendUnreachable] = useState(offline);
  // The explicit player button, latched until the next send. This is trigger
  // #1 of the live-frame feature and it forces a capture on REGARDLESS of the
  // wording: "explain this" with the button pressed is still a question about
  // the frame on screen.
  const [frameLatched, setFrameLatched] = useState(false);
  // True only while a capture is in flight, so the button can report it.
  const [capturing, setCapturing] = useState(false);
  // A frame question that could NOT be answered from a live capture. Kept apart
  // from `sendError` because it is never answered from a mock: a rejected frame
  // is reported and nothing is substituted in its place.
  const [frameError, setFrameError] = useState<string | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  // ---- Chats -------------------------------------------------------------
  // Several chats per session, each its own history on the server. The
  // selection is per session and remembered locally; an id that no longer
  // exists (deleted elsewhere, server restarted) falls back to "main".
  const [threads, setThreads] = useState<ChatThread[]>([]);
  const [threadId, setThreadId] = useState<string>(MAIN_THREAD_ID);
  const [threadBusy, setThreadBusy] = useState(false);
  const [threadError, setThreadError] = useState<string | null>(null);

  useEffect(() => {
    setThreadId(readThread(sessionId));
  }, [sessionId]);

  const selectThread = useCallback(
    (id: string): void => {
      setThreadId(id);
      writeThread(sessionId, id);
      setSendError(null);
      setFrameError(null);
    },
    [sessionId],
  );

  const refreshThreads = useCallback(async (): Promise<ChatThread[] | null> => {
    if (offline) return null;
    try {
      const list = await listThreads(sessionId, sessionToken);
      setThreads(list);
      return list;
    } catch {
      return null; // the history load reports session problems
    }
  }, [offline, sessionId, sessionToken]);

  useEffect(() => {
    void refreshThreads().then((list) => {
      if (list && !list.some((t) => t.id === readThread(sessionId))) {
        selectThread(MAIN_THREAD_ID);
      }
    });
  }, [refreshThreads, selectThread, sessionId]);

  async function handleCreateThread(): Promise<void> {
    setThreadBusy(true);
    setThreadError(null);
    try {
      const t = await createThread(sessionId, sessionToken);
      setThreads((prev) => [t, ...prev]);
      selectThread(t.id);
      inputRef.current?.focus();
    } catch (err) {
      setThreadError(formatApiError(err));
    } finally {
      setThreadBusy(false);
    }
  }

  async function handleRenameThread(id: string, title: string): Promise<void> {
    setThreadError(null);
    try {
      const t = await renameThread(sessionId, id, title, sessionToken);
      setThreads((prev) => prev.map((x) => (x.id === id ? t : x)));
    } catch (err) {
      setThreadError(formatApiError(err));
    }
  }

  async function handleDeleteThread(id: string): Promise<void> {
    setThreadError(null);
    try {
      await deleteThread(sessionId, id, sessionToken);
      if (id === threadId) {
        if (id === MAIN_THREAD_ID) setMessages([]);
        else selectThread(MAIN_THREAD_ID);
      }
      await refreshThreads();
    } catch (err) {
      setThreadError(formatApiError(err));
    }
  }

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
        const history = await getMessages(
          sessionId,
          sessionToken,
          threadId === MAIN_THREAD_ID ? undefined : threadId,
        );
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
  }, [sessionId, sessionToken, offline, threadId]);

  useEffect(() => {
    // Filling a window: scroll the message box only, never the page around it.
    const box = scrollRef.current;
    if (box) box.scrollTo({ top: box.scrollHeight, behavior: "smooth" });
    else bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, sending, preview]);

  // Auto-grow the textarea up to ~6 lines, then scroll inside it.
  useEffect(() => {
    const el = inputRef.current;
    if (!el) return;
    el.style.height = "auto";
    const lineHeight = parseFloat(getComputedStyle(el).lineHeight) || 20;
    const max = lineHeight * 6 + 16;
    el.style.height = `${Math.min(el.scrollHeight, max)}px`;
    el.style.overflowY = el.scrollHeight > max ? "auto" : "hidden";
  }, [input]);

  function handleSubmit(e: React.FormEvent): void {
    e.preventDefault();
    void send(input);
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>): void {
    // Enter sends; Shift+Enter inserts a newline. Ignore Enter mid-IME
    // composition so CJK input is not sent half-typed.
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      void send(input);
    }
  }

  async function send(raw: string): Promise<void> {
    const text = raw.trim();
    if (!text || sending) return;
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
    setFrameError(null);

    // BOTH triggers for the live frame, decided before anything is sent:
    //   1. the explicit button, latched — it forces a capture REGARDLESS of
    //      the wording ("explain this" + button is still about the frame);
    //   2. a question that clearly asks about the current frame.
    // `forced` is read once here: setFrameLatched below re-renders, and the
    // value posted as force_frame must be the one the learner actually set.
    const forced = frameLatched;
    const wantFrame =
      !offline && Boolean(captureFrame) && (forced || isFrameQuestion(text));
    setFrameLatched(false);

    try {
      let res: Awaited<ReturnType<typeof postChat>> | null = null;

      if (wantFrame && captureFrame) {
        // Capture AFTER the submit, not before: captureFrame reads the playhead
        // and paints the pixels in one synchronous tick, so the two always
        // describe the same instant.
        setCapturing(true);
        let captured: LiveFrameCapture | null = null;
        try {
          captured = await captureFrame();
        } finally {
          setCapturing(false);
        }
        if (captured) {
          // atMs comes from the capture, NOT from getCurrentTimeMs(): only the
          // capture knows which instant the pixels belong to.
          res = await postChatWithFrame(captured.blob, {
            session_id: sessionId,
            session_token: sessionToken,
            video_id: videoId,
            current_time_ms: captured.atMs,
            message: text,
            mode,
            force_frame: forced,
            thread_id: threadId,
            completed_resource_ids: completedResourceIds,
          });
        }
      }

      if (!res) {
        // No frame was captured, or none was asked for. The ordinary path is
        // unchanged — this is what every pre-existing question still takes.
        res = offline
          ? mockChatAnswer(mode, text)
          : await streamChat(
              {
                session_id: sessionId,
                session_token: sessionToken,
                video_id: videoId,
                current_time_ms: getCurrentTimeMs(),
                message: text,
                mode,
                thread_id: threadId,
                completed_resource_ids: completedResourceIds,
              },
              {
                onStatus: (status) => setPreview((p) => ({ status, text: p?.text ?? "" })),
                onText: (t) => setPreview((p) => ({ status: p?.status ?? null, text: t })),
              },
            );
      }

      // A refusal is signalled by the bubble's style (kind "refused"), not by
      // prepended copy: the backend's refusal text is already honest about why.
      const pending = res.processing_pending === true;
      const pendingNote =
        pending && typeof res.processed_through_ms === "number"
          ? `\n\nThis part of the video is still being processed (ready up to ${fmtMs(res.processed_through_ms)}). Try again in a minute.`
          : pending
            ? "\n\nThis part of the video is still being processed. Try again in a minute."
            : "";
      const assistantMsg: ChatMessage = {
        id: newId(),
        role: "assistant",
        content: `${res.answer}${pendingNote}`,
        citations: res.citations,
        created_at: new Date().toISOString(),
        kind: res.refused ? "refused" : res.mode,
      };
      setMessages((prev) => [...prev, assistantMsg]);
      // Titles come from a chat's first question, and the list is ordered by
      // activity, so both may have changed.
      void refreshThreads();
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
      // A rejected live frame (out-of-range T, unreadable capture, a material
      // with no frame) lands here too. It is surfaced verbatim and NEVER
      // retried against a different timestamp or silently answered from the
      // nearest pre-indexed frame: a fallback would answer a question the
      // learner did not ask, from a moment they were not looking at.
      if (err instanceof ApiError && wantFrame) {
        setFrameError(
          `Frame question could not be answered from a live capture — ${formatApiError(err)}. No frame was read and nothing was substituted.`,
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
      setCapturing(false);
      setPreview(null);
    }
  }

  return (
    <section
      className={`flex flex-col rounded-xl border border-gray-200 bg-white p-4 shadow-sm ${fill ? "h-full min-h-0" : ""}`}
      aria-label="Chat with video"
    >
      <div className="mb-3 flex items-center justify-between gap-2">
        <div className="flex min-w-0 items-center gap-2.5">
          {headerLead}
          <div
            className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-gradient-to-br from-violet-500 to-fuchsia-500 text-sm font-semibold text-white shadow-sm"
            aria-hidden="true"
          >
            M
          </div>
          <div className="min-w-0">
            <h2 className="text-base font-semibold leading-tight text-gray-900">Malai · AI tutor</h2>
            <p className="truncate text-xs text-gray-500" title="Malai reads this material up to where you are, plus the materials you've marked done.">
              {completedResourceIds.length > 0
                ? `This lesson so far + ${completedResourceIds.length} completed material${completedResourceIds.length === 1 ? "" : "s"}`
                : "Answers come from what you've watched so far"}
            </p>
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          {offline && <span className="shrink-0 rounded-full border border-amber-200 bg-amber-50 px-2.5 py-0.5 text-xs text-amber-600">offline mock</span>}
          {headerActions}
        </div>
      </div>

      <div className="mb-3 flex rounded-xl bg-gray-100 p-1" role="tablist" aria-label="Tutor view">
        {(["chat", "quiz"] as const).map((t) => (
          <button
            key={t}
            type="button"
            role="tab"
            aria-selected={tab === t}
            onClick={() => setTab(t)}
            className={`flex-1 rounded-lg px-3 py-1.5 text-sm font-medium transition ${
              tab === t ? "bg-white text-violet-700 shadow-sm" : "text-gray-500 hover:text-gray-700"
            }`}
          >
            {t === "chat" ? "Chat" : "Quiz"}
          </button>
        ))}
      </div>

      {/* Both stay mounted so switching tabs keeps the chat and an unfinished quiz. */}
      <div className={tab === "quiz" ? (fill ? "min-h-0 flex-1 overflow-y-auto pr-1" : "") : "hidden"} role="tabpanel" aria-label="Quiz">
        <QuizPanel
          sessionId={sessionId}
          sessionToken={sessionToken}
          videoId={videoId}
          getCurrentTimeMs={getCurrentTimeMs}
          completedResourceIds={completedResourceIds}
          onSeek={onSeek}
          openDoc={openDoc}
          offline={offline || backendUnreachable}
        />
      </div>

      <div className={tab === "chat" ? "contents" : "hidden"} role="tabpanel" aria-label="Chat">
      {!offline && !sessionExpired && (
        <ChatThreads
          threads={threads}
          currentId={threadId}
          disabled={sending}
          busy={threadBusy}
          onSelect={selectThread}
          onCreate={() => void handleCreateThread()}
          onRename={(id, title) => void handleRenameThread(id, title)}
          onDelete={(id) => void handleDeleteThread(id)}
        />
      )}
      {threadError && (
        <p className="mb-2 rounded-lg bg-red-50 px-3 py-1.5 text-xs text-red-600" role="alert">
          {threadError}
        </p>
      )}

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

      <div ref={fill ? scrollRef : undefined} className={fill ? "mt-3 min-h-0 flex-1 overflow-y-auto pr-1" : "contents"}>
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
        <MessageList
          messages={messages}
          loading={sending}
          preview={preview}
          onSeek={onSeek}
          openDoc={openDoc}
          onSuggest={(text) => (/^quiz me/i.test(text) ? setTab("quiz") : void send(text))}
          suggestDisabled={sending || sessionExpired}
          fill={fill}
        />
      )}
      </div>

      {sendError && !sessionExpired && (
        <div className="mt-3 rounded-xl border border-amber-200 bg-amber-50 p-3 text-sm text-amber-700" role="alert">
          Live API error (answered from mock): {sendError}
        </div>
      )}
      {frameError && (
        <div className="mt-3 rounded-xl border border-amber-200 bg-amber-50 p-3 text-sm text-amber-700" role="alert">
          {frameError}
        </div>
      )}
      {sendError && sessionExpired && (
        <div className="mt-3 rounded-xl border border-red-200 bg-red-50 p-3 text-sm text-red-600" role="alert">
          {sendError}
        </div>
      )}

      <form className="mt-3 flex flex-col gap-2" onSubmit={handleSubmit}>
        <div className="flex items-end gap-2">
          <label className="sr-only" htmlFor="chat-input">
            Ask a question about the video
          </label>
          <textarea
            id="chat-input"
            ref={inputRef}
            rows={1}
            className="flex-1 resize-none overflow-hidden rounded-xl border border-gray-200 bg-gray-50 px-3 py-2 text-sm leading-5 text-gray-800 placeholder-gray-400 outline-none transition focus:border-violet-400 focus:bg-white focus:ring-2 focus:ring-violet-200"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Pause, then ask… e.g. what did that term mean?"
            disabled={sending}
            autoComplete="off"
          />
          <button type="submit" className="rounded-xl bg-violet-600 px-4 py-2 text-sm font-medium text-white transition hover:bg-violet-700 disabled:opacity-40" disabled={sending || input.trim().length === 0}>
            {sending ? "…" : "Send"}
          </button>
        </div>

        {/* Trigger #1 of the live frame: the explicit button. It LATCHES (it
            does not send by itself) so the learner can still type the question,
            and it forces a capture regardless of the wording. Trigger #2 is
            `isFrameQuestion` inside send(). Rendered only when a capture is
            actually possible: a document has no video element, and offering a
            frame button there would promise something that does not exist. */}
        {captureFrame && (
          <div className="flex items-center justify-between gap-2">
            <button
              type="button"
              aria-pressed={frameLatched}
              onClick={() => setFrameLatched((v) => !v)}
              disabled={sending || capturing}
              title="Send the exact frame on screen with your next question, whatever you type"
              className={
                frameLatched
                  ? "rounded-xl border border-violet-400 bg-violet-100 px-3 py-1.5 text-xs font-medium text-violet-700 transition hover:bg-violet-200 disabled:opacity-40"
                  : "rounded-xl border border-gray-200 bg-white px-3 py-1.5 text-xs font-medium text-gray-600 transition hover:border-violet-300 hover:bg-violet-50 disabled:opacity-40"
              }
            >
              {capturing ? "Reading frame…" : frameLatched ? "✓ This frame" : "Ask about this frame"}
            </button>
            <p className="text-[11px] leading-snug text-gray-400">
              {frameLatched
                ? "Your next question will be answered from the frame on screen."
                : "Or just ask “what am I looking at?”."}
            </p>
          </div>
        )}
      </form>
      </div>
      <div ref={bottomRef} />
    </section>
  );
}
