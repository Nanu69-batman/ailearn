"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";
import type { ChatMessage, ChatMode, ChatResponse, LiveFrameCapture } from "../lib/api";
import {
  ApiError,
  formatApiError,
  getMessages,
  isFrameQuestion,
  isSessionExpiredError,
  postChat,
  postChatWithFrame,
  postQuizNote,
  streamChat,
  createThread,
  MAIN_THREAD_ID,
} from "../lib/api";
import { mockChatAnswer, mockHistory } from "../lib/fixtures";
import { LEARNER_NAME } from "../lib/learn";
import MessageList from "./MessageList";
import { AccessPrompt } from "./ResourceChecklist";
import type { CitationBadgeProps } from "./CitationBadge";
import {
  SlashMenuList,
  commandQuestion,
  parseCommand,
  quizFromCommand,
  useSlashMenu,
} from "./SlashCommands";
import QuizPanel, { type QuizOutcome, type QuizPreset } from "./QuizPanel";
import { detectQuizIntent } from "../lib/quizIntent";
import { joinText, stripInterim, withInterim } from "../lib/voice";
import MicButton, { VoiceButton } from "./MicButton";
import VoicePanel from "./VoicePanel";
import ModelModePicker from "./ModelModePicker";
import {
  DropOverlay,
  ImageButton,
  ImageChips,
  pastedImages,
  useChatImages,
  useImageDrop,
  withImageNote,
} from "./ChatImages";
import { useDictation } from "./useDictation";
import { useVoiceModels, type VoiceStartFields, type VoiceTurn } from "./useVoiceSession";

interface ChatPanelProps {
  /** The subject a quiz written here is saved under. */
  subjectId?: string | null;
  sessionId: string;
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
  /**
   * The subject's materials this chat cannot read. When an answer needs one,
   * the chat asks whether to give it access (`onAllowAccess`), then asks again.
   */
  suggestResourceIds?: string[];
  /** Title of a suggested material, for the access prompt. */
  resourceTitle?: (id: string) => string;
  subjectName?: string;
  onAllowAccess?: (ids: string[], forSubject: boolean) => void;
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
  /** After the Chat/Quiz tabs: the chat dropdown (chats from Malai's sidebar). */
  chatPicker?: ReactNode;
  /**
   * The chat (thread) picked in Malai's sidebar. Chats are managed there
   * only; without one the panel starts a new chat.
   */
  openThreadId?: string | null;
  /** A question was sent in `threadId` (before its answer arrives). */
  onAsk?: (threadId: string, question: string) => void;
  /** The server named a chat after its first question. */
  onTitled?: (threadId: string, title: string) => void;
  /** A video (playhead, frames) rather than a document. Changes the wording. */
  isVideo?: boolean;
  /** Extra classes for the input bar's row, e.g. room for something floating over it. */
  composerClassName?: string;
}

interface Starter {
  label: string;
  prompt: string;
  icon: string;
  /** Opens the Quiz tab instead of asking. */
  quiz?: boolean;
}

const BOOK_ICON = "M2 5a2 2 0 012-2h5a3 3 0 013 3v15a2 2 0 00-2-2H2zM22 5a2 2 0 00-2-2h-5a3 3 0 00-3 3v15a2 2 0 012-2h8z";
const LIST_ICON = "M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01";
const TARGET_ICON = "M12 22a10 10 0 110-20 10 10 0 010 20zM12 16a4 4 0 110-8 4 4 0 010 8zM12 12h.01";

/** The empty chat's starters, as on the Malai page. */
const VIDEO_STARTERS: Starter[] = [
  { label: "Explain this concept", prompt: "Explain the last idea simply", icon: BOOK_ICON },
  { label: "What's on screen?", prompt: "What's on screen right now?", icon: "M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12zM12 15a3 3 0 100-6 3 3 0 000 6z" },
  { label: "Summarize this section", prompt: "Summarize what I've watched so far", icon: LIST_ICON },
  { label: "Create practice questions", prompt: "", icon: TARGET_ICON, quiz: true },
];
const DOCUMENT_STARTERS: Starter[] = [
  { label: "Explain the key idea", prompt: "Explain the key idea of this material simply", icon: BOOK_ICON },
  { label: "Summarize this", prompt: "Summarize this material", icon: LIST_ICON },
  { label: "Create practice questions", prompt: "", icon: TARGET_ICON, quiz: true },
];

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

function fmtMs(ms: number): string {
  const s = Math.max(0, Math.floor(ms / 1000));
  return `${Math.floor(s / 60)}:${(s % 60).toString().padStart(2, "0")}`;
}

export default function ChatPanel({
  subjectId = null,
  sessionId,
  videoId,
  getCurrentTimeMs,
  captureFrame,
  onSeek,
  openDoc,
  completedResourceIds = [],
  suggestResourceIds = [],
  resourceTitle = (id: string) => id,
  subjectName = "this subject",
  onAllowAccess,
  offline,
  onRejoin,
  fill = false,
  headerLead,
  headerActions,
  chatPicker,
  openThreadId,
  onAsk,
  onTitled,
  isVideo = true,
  composerClassName = "",
}: ChatPanelProps) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [historyState, setHistoryState] = useState<"loading" | "ready" | "error">("loading");
  const [historyError, setHistoryError] = useState<string | null>(null);
  /** Set by /quiz or a message asking for one: QuizPanel writes it now. */
  const [quizPreset, setQuizPreset] = useState<QuizPreset | null>(null);
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

  // ---- Voice -------------------------------------------------------------
  // Dictation fills the input; the voice panel is a live conversation whose
  // questions and validated answers land in the chat like typed ones.
  const voiceModels = useVoiceModels(!offline);
  const [voiceOpen, setVoiceOpen] = useState(false);
  const dictation = useDictation({
    getSession: async () => (sessionExpired ? null : { sessionId }),
    onFinal: (t) => setInput((prev) => joinText(prev, t)),
  });

  // ---- Chat --------------------------------------------------------------
  // Which chat this is comes from Malai's sidebar (`openThreadId`); chats are
  // created, renamed and deleted there only. Without one, a new server thread
  // is made here, and the first question files it in the sidebar (onAsk).
  // "" while that thread is being made: nothing loads or sends until then.
  const [threadId, setThreadId] = useState<string>("");
  const threadRef = useRef("");
  threadRef.current = threadId;
  /** The answer needed materials this chat cannot read; `resend` asks again once allowed. */
  const [accessAsk, setAccessAsk] = useState<{ ids: string[]; text: string } | null>(null);
  const [resend, setResend] = useState<string | null>(null);
  useEffect(() => setAccessAsk(null), [threadId]);

  // Images attached in the bar: context for this chat, never resources.
  const images = useChatImages(async () =>
    sessionExpired ? null : { sessionId, threadId: threadId === MAIN_THREAD_ID ? undefined : threadId },
  );
  const clearImages = images.clear;
  const drop = useImageDrop((f) => void images.add(f), !offline && !sessionExpired);
  // Another chat: its attachments are its own.
  useEffect(() => clearImages(), [threadId, clearImages]);
  useEffect(() => {
    if (openThreadId) {
      // The chat this panel just started comes back as openThreadId once its
      // first question puts it in the address: keep going, nothing to reset.
      if (openThreadId !== threadRef.current) {
        setThreadId(openThreadId);
        setSendError(null);
        setFrameError(null);
      }
      return;
    }
    if (offline) {
      setThreadId(MAIN_THREAD_ID);
      return;
    }
    let cancelled = false;
    setThreadId("");
    createThread(sessionId)
      .then((t) => {
        if (!cancelled) setThreadId(t.id);
      })
      .catch(() => {
        // The history load reports a lost session; otherwise chat in "main".
        if (!cancelled) setThreadId(MAIN_THREAD_ID);
      });
    return () => {
      cancelled = true;
    };
  }, [sessionId, offline, openThreadId]);

  useEffect(() => {
    if (!threadId) return;
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
  }, [sessionId, offline, threadId]);

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
  }, [input, dictation.interim]);

  /** A spoken turn: shown in the chat like a typed message, updated in place as it is transcribed. */
  function voiceTurn(turn: VoiceTurn): void {
    setMessages((prev) => {
      const i = prev.findIndex((m) => m.id === turn.id);
      if (i >= 0) return prev.map((m, j) => (j === i ? { ...m, content: turn.text } : m));
      return [
        ...prev,
        { id: turn.id, role: turn.role, content: turn.text, citations: [], created_at: new Date().toISOString(), ...(turn.role === "assistant" ? { kind: "conversational" as const } : {}) },
      ];
    });
  }

  /** A voice recap: saved to the chat, after the spoken turns. */
  function voiceRecap(res: ChatResponse): void {
    setMessages((prev) => [
      ...prev,
      {
        id: newId(),
        role: "assistant",
        content: res.answer,
        citations: res.citations,
        created_at: new Date().toISOString(),
        kind: res.mode,
      },
    ]);
  }

  /** The same scope fields the typed question sends to streamChat. */
  function voiceStart(): VoiceStartFields {
    return {
      session_id: sessionId,
      video_id: videoId,
      current_time_ms: getCurrentTimeMs(),
      thread_id: threadId,
      completed_resource_ids: completedResourceIds,
    };
  }

  function handleSubmit(e: React.FormEvent): void {
    e.preventDefault();
    // Send is never greyed out; an empty or not-yet-ready send does nothing.
    if (canSend) void send(input);
  }

  const slashContext = isVideo ? "video" : "document";
  const slash = useSlashMenu(input, setInput, slashContext);
  const menu = slash.menu;

  function pickFromMenu(index: number): void {
    slash.pick(index);
    inputRef.current?.focus();
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>): void {
    if (slash.onKeyDown(e)) return;
    // Enter sends; Shift+Enter inserts a newline. Ignore Enter mid-IME
    // composition so CJK input is not sent half-typed.
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      void send(input);
    }
  }

  async function send(raw: string): Promise<void> {
    const typed = raw.trim();
    if (!typed || !threadId || sending || images.uploading) return;
    setAccessAsk(null);
    dictation.stop();
    // A slash command sets the answer style, or opens a quiz; so does a
    // message that asks for a quiz in so many words ("quiz me on this").
    const cmd = parseCommand(typed, slashContext);
    const intent = cmd ? null : detectQuizIntent(typed);
    const quizAsk = cmd?.command.quiz
      ? { ...quizFromCommand(cmd.command, cmd.rest), request: cmd.rest ? `Quiz me: ${cmd.rest}` : "Quiz me" }
      : intent
        ? { ...intent, request: typed }
        : null;
    if (quizAsk) {
      setMessages((prev) => [
        ...prev,
        { id: newId(), role: "user", content: quizAsk.request, citations: [], created_at: new Date().toISOString() },
      ]);
      if (!offline) onAsk?.(threadId, quizAsk.request);
      setQuizPreset({ ...quizAsk, nonce: Date.now() });
      setInput("");
      setTab("quiz");
      return;
    }
    const mode: ChatMode = cmd?.command.mode ?? "qa";
    const text = cmd ? commandQuestion(cmd.command, cmd.rest) : typed;
    const userMsg: ChatMessage = {
      id: newId(),
      role: "user",
      content: withImageNote(text, images.readyNames),
      citations: [],
      created_at: new Date().toISOString(),
    };
    setMessages((prev) => [...prev, userMsg]);
    clearImages();
    setInput("");
    setSending(true);
    if (!offline) onAsk?.(threadId, text);
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
                video_id: videoId,
                current_time_ms: getCurrentTimeMs(),
                message: text,
                mode,
                thread_id: threadId,
                completed_resource_ids: completedResourceIds,
                ...(onAllowAccess && suggestResourceIds.length ? { suggest_resource_ids: suggestResourceIds } : {}),
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
      if (res.thread_title) onTitled?.(threadId, res.thread_title);
      const suggested = (res.suggested_resource_ids ?? []).filter((id) => suggestResourceIds.includes(id));
      if (onAllowAccess && suggested.length) setAccessAsk({ ids: suggested, text });
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

  // Asked again after access is given, once the new allowance has rendered.
  useEffect(() => {
    if (resend === null) return;
    setResend(null);
    void send(resend);
    // `send` is recreated every render; only a new `resend` should fire this.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [resend]);

  /** Malai's line in the chat, when the server could not record one. */
  function localNote(content: string, refused = false): void {
    setMessages((prev) => [
      ...prev,
      {
        id: newId(),
        role: "assistant",
        content,
        citations: [],
        created_at: new Date().toISOString(),
        ...(refused ? { kind: "refused" as const } : {}),
      },
    ]);
  }

  // A quiz asked for in the chat is recorded there: that it was written, and
  // its score once every answer is in. Not answering is fine; the chat
  // carries on either way.
  async function quizStarted(request: string, count: number): Promise<void> {
    const fallback = `I've made you a ${count}-question quiz. It's open in the Quiz tab — answer it whenever you like.`;
    if (offline) return localNote(fallback);
    try {
      const lines = await postQuizNote(
        { session_id: sessionId, thread_id: threadId, event: "started", request, count },
      );
      // The learner's line is already on screen.
      setMessages((prev) => [...prev, ...lines.filter((m) => m.role === "assistant")]);
    } catch {
      localNote(fallback);
    }
  }

  function quizFailed(_request: string, message: string): void {
    localNote(`I couldn't write that quiz: ${message}`, true);
  }

  async function quizFinished(outcome: QuizOutcome): Promise<void> {
    const fallback = `**Quiz completed** — you scored **${outcome.score} / ${outcome.count}**.`;
    if (offline) return localNote(fallback);
    try {
      const lines = await postQuizNote(
        {
          session_id: sessionId,
          thread_id: threadId,
          event: "completed",
          count: outcome.count,
          score: outcome.score,
          to_review: outcome.toReview,
        },
      );
      setMessages((prev) => [...prev, ...lines]);
    } catch {
      localNote(fallback);
    }
  }

  const extraDone = completedResourceIds.length;
  const starters = isVideo ? VIDEO_STARTERS : DOCUMENT_STARTERS;
  const showGreeting =
    tab === "chat" && historyState === "ready" && messages.length === 0 && !sending && !sessionExpired;
  const canSend = Boolean(threadId) && !sending && !images.uploading && input.trim().length > 0;
  const notice = "rounded-xl border px-3 py-2.5 text-sm";

  return (
    <section
      className={`relative flex flex-col overflow-hidden rounded-2xl bg-white ${fill ? "h-full min-h-0" : ""}`}
      aria-label="Chat with Malai"
      {...drop.bind}
    >
      <DropOverlay show={drop.dragging} />
      {/* Header: Chat/Quiz. Chats themselves are managed in Malai's sidebar. */}
      <div className="flex shrink-0 items-center gap-2 border-b border-gray-100 px-3 py-2.5">
        {headerLead}
        <div className="flex shrink-0 rounded-full bg-gray-100 p-0.5" role="tablist" aria-label="Tutor view">
          {(["chat", "quiz"] as const).map((t) => (
            <button
              key={t}
              type="button"
              role="tab"
              aria-selected={tab === t}
              onClick={() => setTab(t)}
              className={`rounded-full px-3.5 py-1.5 text-sm font-medium transition focus:outline-none focus-visible:ring-2 focus-visible:ring-violet-400 ${
                tab === t ? "bg-white text-violet-700 shadow-sm" : "text-gray-500 hover:text-gray-800"
              }`}
            >
              {t === "chat" ? "Chat" : "Quiz"}
            </button>
          ))}
        </div>
        {chatPicker && !offline ? <div className="flex min-w-0 flex-1">{chatPicker}</div> : <span className="flex-1" />}
        {offline && <span className="shrink-0 rounded-full border border-amber-200 bg-amber-50 px-2.5 py-0.5 text-xs text-amber-600">offline mock</span>}
        {headerActions}
      </div>

      {/* Both stay mounted so switching tabs keeps the chat and an unfinished quiz. */}
      <div
        className={tab === "quiz" ? `min-h-0 flex-1 overflow-y-auto px-4 py-3 ${fill ? "" : "max-h-[60vh]"}` : "hidden"}
        role="tabpanel"
        aria-label="Quiz"
      >
        <QuizPanel
          sessionId={sessionId}
          getCurrentTimeMs={getCurrentTimeMs}
          subjectId={subjectId}
          scope={{ video_id: videoId, completed_resource_ids: completedResourceIds, thread_id: threadId }}
          sourceNote={`From this ${isVideo ? "video up to where you are" : "material"}${
            extraDone > 0 ? ` + ${extraDone} completed material${extraDone === 1 ? "" : "s"}` : ""
          }.`}
          onSeek={onSeek}
          openDoc={openDoc}
          offline={offline || backendUnreachable}
          preset={quizPreset}
          onStarted={(request, count) => void quizStarted(request, count)}
          onFailed={quizFailed}
          onFinished={(o) => void quizFinished(o)}
        />
      </div>

      <div className={tab === "chat" ? "flex min-h-0 flex-1 flex-col" : "hidden"} role="tabpanel" aria-label="Chat">
        <div ref={scrollRef} className={`min-h-0 flex-1 overflow-y-auto px-4 pb-3 ${fill ? "" : "max-h-[60vh]"}`}>
          {sessionExpired && (
            <div className={`${notice} mt-3 border-red-200 bg-red-50 text-red-600`} role="alert">
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
            <div className={`${notice} mt-3 border-amber-200 bg-amber-50 text-amber-700`} role="alert">
              Backend unreachable — showing mock data.
            </div>
          )}
          {historyState === "loading" && (
            <div className="flex items-center justify-center gap-2 py-10 text-sm text-gray-500" role="status">
              <span className="h-4 w-4 animate-spin rounded-full border-2 border-violet-500 border-t-transparent" aria-hidden="true" />
              Loading the conversation…
            </div>
          )}
          {historyState === "error" && !sessionExpired && (
            <div className={`${notice} mt-3 border-red-200 bg-red-50 text-red-600`} role="alert">
              Could not load history: {historyError}
            </div>
          )}

          {showGreeting ? (
            <div className="flex min-h-full flex-col items-center justify-center px-2 py-6 text-center">
              <p className="text-sm font-medium text-violet-700">
                Hello, {LEARNER_NAME} <span aria-hidden="true">👋</span>
              </p>
              <h3 className="mt-1.5 text-xl font-bold tracking-tight text-gray-900 sm:text-2xl">
                How can I help you with this {isVideo ? "video" : "material"}?
              </h3>
              <p className="mt-1.5 text-sm text-gray-500">
                Ask questions, get explanations, solve doubts, practice problems and more.
                Type <kbd className="rounded border border-gray-200 bg-gray-50 px-1 font-sans text-xs text-gray-600">/</kbd> for commands.
              </p>
              <div className="mt-5 flex flex-wrap justify-center gap-2">
                {starters.map((s) => (
                  <button
                    key={s.label}
                    type="button"
                    disabled={sending || sessionExpired}
                    onClick={() => (s.quiz ? setTab("quiz") : void send(s.prompt))}
                    className="inline-flex items-center gap-2 rounded-full border border-violet-100 bg-violet-50/50 px-3.5 py-2 text-sm text-gray-700 transition hover:border-violet-300 hover:bg-white hover:text-violet-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-violet-400 disabled:opacity-50"
                  >
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="h-4 w-4 text-violet-600" aria-hidden="true">
                      <path d={s.icon} />
                    </svg>
                    {s.label}
                  </button>
                ))}
              </div>
            </div>
          ) : (
            (historyState === "ready" || messages.length > 0) && (
              <div className="pt-3">
                <MessageList
                  messages={messages}
                  loading={sending}
                  preview={preview}
                  onSeek={onSeek}
                  openDoc={openDoc}
                  onSuggest={(text) => void send(text)}
                  suggestDisabled={sending || sessionExpired}
                  fill
                />
                {accessAsk && onAllowAccess && !sending && (
                  <div className="mt-3">
                    <AccessPrompt
                      titles={accessAsk.ids.map(resourceTitle)}
                      subjectName={subjectName}
                      onAllowChat={() => {
                        onAllowAccess(accessAsk.ids, false);
                        setResend(accessAsk.text);
                        setAccessAsk(null);
                      }}
                      onAllowSubject={() => {
                        onAllowAccess(accessAsk.ids, true);
                        setResend(accessAsk.text);
                        setAccessAsk(null);
                      }}
                      onDismiss={() => setAccessAsk(null)}
                    />
                  </div>
                )}
              </div>
            )
          )}

          {sendError && !sessionExpired && (
            <div className={`${notice} mt-3 border-amber-200 bg-amber-50 text-amber-700`} role="alert">
              Live API error (answered from mock): {sendError}
            </div>
          )}
          {frameError && (
            <div className={`${notice} mt-3 border-amber-200 bg-amber-50 text-amber-700`} role="alert">
              {frameError}
            </div>
          )}
          {sendError && sessionExpired && (
            <div className={`${notice} mt-3 border-red-200 bg-red-50 text-red-600`} role="alert">
              {sendError}
            </div>
          )}
          <div ref={bottomRef} />
        </div>

        {/* Composer: the same pill as the Malai page; the voice bar takes its place during a conversation */}
        {voiceOpen && voiceModels ? (
          <div className={`shrink-0 px-4 pb-4 ${composerClassName}`}>
            <VoicePanel
              models={voiceModels}
              getStart={voiceStart}
              getCurrentTimeMs={isVideo ? getCurrentTimeMs : undefined}
              onRecap={voiceRecap}
              onTurn={voiceTurn}
              onClose={() => setVoiceOpen(false)}
              subtitle={`About this ${isVideo ? "video" : "material"}`}
            />
          </div>
        ) : (
        <form className={`shrink-0 px-4 pb-4 transition-[padding] ${composerClassName}`} onSubmit={handleSubmit}>
          <div className="relative">
          {menu && (
            <SlashMenuList menu={menu} active={slash.active} onPick={pickFromMenu} onHover={slash.setActive} />
          )}
          <ImageChips items={images.items} onRemove={images.remove} />
          <div className="flex items-end gap-2 rounded-[1.5rem] border border-gray-200 bg-white py-1.5 pl-2 pr-1.5 shadow-lg shadow-violet-900/5 transition focus-within:border-violet-300 focus-within:ring-4 focus-within:ring-violet-100">
            {/* Trigger #1 of the live frame: the explicit button. It LATCHES (it
                does not send by itself) so the learner can still type the
                question, and it forces a capture regardless of the wording.
                Trigger #2 is `isFrameQuestion` inside send(). Shown only for a
                video: a document has no frame, and offering one there would
                promise something that does not exist. */}
            {captureFrame && isVideo ? (
              <button
                type="button"
                aria-pressed={frameLatched}
                onClick={() => setFrameLatched((v) => !v)}
                disabled={sending || capturing}
                aria-label={frameLatched ? "Stop sending the frame on screen" : "Ask about the frame on screen"}
                title="Send the exact frame on screen with your next question, whatever you type"
                className={`mb-0.5 flex h-10 w-10 shrink-0 items-center justify-center rounded-full transition focus:outline-none focus-visible:ring-2 focus-visible:ring-violet-400 disabled:opacity-40 ${
                  frameLatched ? "bg-violet-100 text-violet-700" : "text-gray-400 hover:bg-gray-100 hover:text-gray-700"
                }`}
              >
                {capturing ? (
                  <span className="h-4 w-4 animate-spin rounded-full border-2 border-violet-500 border-t-transparent" aria-hidden="true" />
                ) : (
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="h-5 w-5" aria-hidden="true">
                    <path d="M3 8V6a2 2 0 012-2h2M17 4h2a2 2 0 012 2v2M21 16v2a2 2 0 01-2 2h-2M7 20H5a2 2 0 01-2-2v-2" />
                    <circle cx="12" cy="12" r="3" />
                  </svg>
                )}
              </button>
            ) : (
              <span className="w-2 shrink-0" aria-hidden="true" />
            )}
            <label className="sr-only" htmlFor="chat-input">
              Ask Malai about this {isVideo ? "video" : "material"}
            </label>
            <textarea
              id="chat-input"
              ref={inputRef}
              rows={1}
              className="min-h-[2.5rem] flex-1 resize-none overflow-hidden bg-transparent py-2.5 text-sm leading-5 text-gray-900 placeholder-gray-400 outline-none"
              value={withInterim(input, dictation.interim)}
              onChange={(e) => setInput(stripInterim(e.target.value, input, dictation.interim))}
              onKeyDown={handleKeyDown}
              onPaste={(e) => {
                const pasted = pastedImages(e);
                if (pasted.length && !offline && !sessionExpired) {
                  e.preventDefault();
                  void images.add(pasted);
                }
              }}
              placeholder={
                frameLatched
                  ? "Ask about the frame on screen…"
                  : `Ask about this ${isVideo ? "video" : "material"}, or type / for commands…`
              }
              disabled={sending}
              autoComplete="off"
              role="combobox"
              aria-expanded={menu !== null}
              aria-controls={menu ? "slash-menu" : undefined}
              aria-activedescendant={menu ? `slash-opt-${slash.active}` : undefined}
            />
            {!offline && !sessionExpired && (
              <ImageButton onFiles={(f) => void images.add(f)} disabled={sending} />
            )}
            {voiceModels && !sessionExpired && (
              <MicButton state={dictation.state} error={dictation.error} onClick={dictation.toggle} disabled={sending} />
            )}
            {/* Empty input: the voice conversation sits where Send goes. */}
            {voiceModels && !sessionExpired && !sending && !input.trim() ? (
              <VoiceButton
                primary
                onClick={() => {
                  dictation.stop();
                  setVoiceOpen(true);
                }}
              />
            ) : (
            <button
              type="submit"
              aria-label="Send"
              className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-violet-600 text-white shadow-md shadow-violet-600/30 transition hover:bg-violet-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-violet-400 focus-visible:ring-offset-2"
            >
              {sending ? (
                <span className="h-4 w-4 animate-spin rounded-full border-2 border-white border-t-transparent" aria-hidden="true" />
              ) : (
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" className="h-5 w-5" aria-hidden="true">
                  <path d="M12 19V5M5 12l7-7 7 7" />
                </svg>
              )}
            </button>
            )}
          </div>
          </div>
          <ModelModePicker className="mt-1 px-1" />
          {frameLatched && (
            <p className="mt-1.5 px-3 text-[11px] text-violet-700">
              Your next question will be answered from the frame on screen.
            </p>
          )}
        </form>
        )}
      </div>
    </section>
  );
}
