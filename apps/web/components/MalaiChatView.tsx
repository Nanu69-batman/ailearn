"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useSearchParams } from "next/navigation";
import {
  ApiError,
  MAIN_THREAD_ID,
  formatApiError,
  getMessages,
  postQuizNote,
  streamChat,
  type ChatMessage,
  type ChatMode,
  type ChatResponse,
  type ChatRequest,
  type QuizScope,
} from "@/lib/api";
import { detectQuizIntent } from "@/lib/quizIntent";
import { LEARNER_NAME, resourceIcon, resourceKindLabel, toSelected } from "@/lib/learn";
import {
  addChat,
  allResourceIds,
  chatDate,
  chatPath,
  doneResourceIds,
  malaiChatPath,
  malaiLessonPath,
  malaiSubjectPath,
  newChatId,
  openThread,
  titleFrom,
  updateChat,
  useMalaiChats,
  useSubjectResources,
  type MalaiChat,
} from "@/lib/malai";
import Link from "next/link";
import MessageList from "@/components/MessageList";
import QuizPanel, { type QuizOutcome, type QuizPreset } from "@/components/QuizPanel";
import {
  SlashMenuList,
  commandQuestion,
  parseCommand,
  quizFromCommand,
  useSlashMenu,
  type SlashContext,
} from "@/components/SlashCommands";
import { initials, subjectAccent } from "@/components/SubjectCard";
import { useSubjects } from "@/components/useSubjects";
import { joinText, stripInterim, withInterim } from "@/lib/voice";
import MicButton, { VoiceButton } from "@/components/MicButton";
import VoicePanel from "@/components/VoicePanel";
import ModelModePicker from "@/components/ModelModePicker";
import AddContentDialog from "@/components/AddContentDialog";
import {
  DropOverlay,
  ImageButton,
  ImageChips,
  pastedImages,
  useChatImages,
  useImageDrop,
  withImageNote,
  type PendingImage,
} from "@/components/ChatImages";
import { useDictation, type DictationState } from "@/components/useDictation";
import { useVoiceModels, type VoiceStartFields, type VoiceTurn } from "@/components/useVoiceSession";

/**
 * /malai: chat with the tutor outside any one lesson.
 *
 * /malai?s=<subject> is that subject's page (like a project): its chats, its
 * resources, and a box that starts a new chat filed under it.
 *
 * A new chat shows the composer in the middle of the page; once the first
 * question is sent it moves to the bottom. The chat is created on that first
 * send (a server thread in the Malai session) and the URL becomes
 * /malai?c=<id> without a navigation, so the answer streaming in is never
 * interrupted.
 *
 * What Malai may read: the chat's subject's materials, or every subject's.
 * There is no playhead here, so videos follow the same strict gate as
 * everywhere else: only those marked done are readable; documents always are.
 */

const STARTERS: ReadonlyArray<{ label: string; prompt: string; icon: string }> = [
  { label: "Explain a concept simply", prompt: "Explain this concept simply: ", icon: "M2 5a2 2 0 012-2h5a3 3 0 013 3v15a2 2 0 00-2-2H2zM22 5a2 2 0 00-2-2h-5a3 3 0 00-3 3v15a2 2 0 012-2h8z" },
  { label: "Solve a problem step by step", prompt: "Solve this step by step: ", icon: "M3 12h3l3 8 4-16h8M14 13l5 5M19 13l-5 5" },
  { label: "Summarize this chapter", prompt: "Summarize the chapter on ", icon: "M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01" },
  { label: "Create practice questions", prompt: "Create practice questions on ", icon: "M12 22a10 10 0 110-20 10 10 0 010 20zM12 16a4 4 0 110-8 4 4 0 010 8zM12 12h.01" },
];

function newMessageId(): string {
  return `m_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`;
}

/** 401: binding rejected; 404: the server lost the session (restart). */
function sessionGone(err: unknown): boolean {
  return err instanceof ApiError && (err.status === 401 || err.status === 404);
}

function Composer({
  value,
  onChange,
  onSend,
  disabled,
  busy,
  placeholder,
  autoFocus,
  inputRef,
  slashContext,
  voice,
  images,
  pickerDown,
}: {
  /** The model chooser opens downward (a new chat or a subject's page). */
  pickerDown?: boolean;
  /** Images attached in the bar (chat context, never resources). */
  images?: { items: PendingImage[]; onFiles: (f: FileList | File[]) => void; onRemove: (key: string) => void; uploading: boolean };
  /** Dictation and the voice conversation; absent when voice is off. */
  voice?: { state: DictationState; error: string | null; onMic: () => void; onTalk: () => void };
  value: string;
  onChange: (v: string) => void;
  onSend: () => void;
  disabled: boolean;
  busy: boolean;
  placeholder: string;
  autoFocus?: boolean;
  inputRef: React.RefObject<HTMLTextAreaElement | null>;
  /** What this chat can read; the commands are worded for it. */
  slashContext: SlashContext;
}) {
  const slash = useSlashMenu(value, onChange, slashContext);
  // Grow with the text, up to a cap.
  useEffect(() => {
    const el = inputRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 180)}px`;
  }, [value, inputRef]);

  const canSend = !disabled && !busy && !images?.uploading && value.trim().length > 0;
  // Empty input: the voice conversation sits where Send goes. Typing swaps it
  // for Send, which is never greyed out (an empty send just does nothing).
  const showSend = busy || value.trim().length > 0 || !voice;

  return (
    <div>
    {images && <ImageChips items={images.items} onRemove={images.onRemove} />}
    <form
      onSubmit={(e) => {
        e.preventDefault();
        if (canSend) onSend();
      }}
      className="relative flex items-end gap-2 rounded-[1.75rem] border border-gray-200 bg-white py-2 pl-5 pr-2 shadow-lg shadow-violet-900/5 transition focus-within:border-violet-300 focus-within:ring-4 focus-within:ring-violet-100"
    >
      {slash.menu && (
        <SlashMenuList
          menu={slash.menu}
          active={slash.active}
          onPick={(i) => {
            slash.pick(i);
            inputRef.current?.focus();
          }}
          onHover={slash.setActive}
        />
      )}
      <label className="sr-only" htmlFor="malai-input">Message Malai</label>
      <textarea
        id="malai-input"
        ref={inputRef}
        rows={1}
        value={value}
        autoFocus={autoFocus}
        onChange={(e) => onChange(e.target.value)}
        onPaste={(e) => {
          const pasted = pastedImages(e);
          if (pasted.length && images) {
            e.preventDefault();
            images.onFiles(pasted);
          }
        }}
        onKeyDown={(e) => {
          if (slash.onKeyDown(e)) return;
          if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
            e.preventDefault();
            if (canSend) onSend();
          }
        }}
        placeholder={placeholder}
        autoComplete="off"
        role="combobox"
        aria-expanded={slash.menu !== null}
        aria-controls={slash.menu ? "slash-menu" : undefined}
        aria-activedescendant={slash.menu ? `slash-opt-${slash.active}` : undefined}
        className="max-h-[180px] min-h-[2.75rem] flex-1 resize-none bg-transparent py-2.5 text-[15px] text-gray-900 placeholder-gray-400 outline-none"
      />
      {images && <ImageButton size="lg" onFiles={images.onFiles} disabled={disabled || busy} />}
      {voice && (
        <MicButton size="lg" state={voice.state} error={voice.error} onClick={voice.onMic} disabled={disabled || busy} />
      )}
      {showSend ? (
      <button
        type="submit"
        aria-label="Send"
        className="flex h-12 w-12 shrink-0 items-center justify-center rounded-full bg-violet-600 text-white shadow-md shadow-violet-600/30 transition hover:bg-violet-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-violet-400 focus-visible:ring-offset-2"
      >
        {busy ? (
          <span className="h-4 w-4 animate-spin rounded-full border-2 border-white border-t-transparent" aria-hidden="true" />
        ) : (
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" className="h-5 w-5" aria-hidden="true">
            <path d="M12 19V5M5 12l7-7 7 7" />
          </svg>
        )}
      </button>
      ) : (
        voice && <VoiceButton size="lg" primary onClick={voice.onTalk} disabled={disabled} />
      )}
    </form>
    <ModelModePicker className="mt-1.5 pl-3" down={pickerDown} />
    </div>
  );
}

export default function MalaiChatView() {
  const params = useSearchParams();
  const chatId = params.get("c");
  const pageSubjectId = chatId ? null : params.get("s");
  const chats = useMalaiChats();
  const { subjects, state: subjectsState, error: subjectsError } = useSubjects();

  const [mounted, setMounted] = useState(false);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [history, setHistory] = useState<"idle" | "loading" | "ready" | "gone">("idle");
  const [loading, setLoading] = useState(false);
  const [preview, setPreview] = useState<{ status: string | null; text: string } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const inputRef = useRef<HTMLTextAreaElement | null>(null);
  /** The chat this view just created: its messages are already on screen. */
  const createdHere = useRef<string | null>(null);
  // The quiz panel beside the chat: the chat it belongs to (whose session it
  // uses and where its events are written), and whether it is showing. It
  // stays mounted while hidden, so an unfinished quiz survives a close.
  const [quizChat, setQuizChat] = useState<MalaiChat | null>(null);
  const [quizOpen, setQuizOpen] = useState(false);
  const [quizPreset, setQuizPreset] = useState<QuizPreset | null>(null);

  useEffect(() => setMounted(true), []);

  const chat: MalaiChat | null = chatId ? chats.find((c) => c.id === chatId) ?? null : null;
  const known = (id: string | null) => (id && subjects.some((s) => s.id === id) ? id : null);
  const subjectId = chat ? known(chat.subjectId) : known(pageSubjectId);
  const subject = subjects.find((s) => s.id === subjectId) ?? null;
  const slashContext: SlashContext = subject ? "subject" : "general";

  // Load the open chat's history whenever the chat changes.
  const chatSession = chat ? `${chat.sessionId}|${chat.threadId}` : null;
  useEffect(() => {
    setError(null);
    // Another chat, or a new one: the open quiz belongs to the old chat.
    if (chatId === null || chatId !== createdHere.current) {
      setQuizChat(null);
      setQuizOpen(false);
      setQuizPreset(null);
    }
    if (!chatId) {
      createdHere.current = null;
      setMessages([]);
      setHistory("idle");
      return;
    }
    if (chatId === createdHere.current) return;
    // Any other chat loads from the server, including a return to this one.
    createdHere.current = null;
    if (!chat) return;
    let cancelled = false;
    setMessages([]);
    setHistory("loading");
    void (async () => {
      try {
        const rows = await getMessages(
          chat.sessionId,
          chat.threadId === MAIN_THREAD_ID ? undefined : chat.threadId,
        );
        if (!cancelled) {
          setMessages(rows);
          setHistory("ready");
        }
      } catch (err) {
        if (cancelled) return;
        if (sessionGone(err)) setHistory("gone");
        else {
          setHistory("ready");
          setError(formatApiError(err));
        }
      }
    })();
    return () => {
      cancelled = true;
    };
    // `chatSession` captures everything about `chat` that matters here.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [chatId, chatSession]);

  // Keep the newest message in view.
  useEffect(() => {
    if (messages.length === 0) return;
    window.scrollTo({ top: document.documentElement.scrollHeight, behavior: "smooth" });
  }, [messages.length, messages[messages.length - 1]?.content, preview?.text]);

  // ---- Voice -------------------------------------------------------------
  // Dictation needs a session, so the first use in a new chat creates the chat
  // the same way a first question does. The voice panel's questions and
  // validated answers join the message list like typed ones.
  const voiceModels = useVoiceModels(true);
  const [voiceChat, setVoiceChat] = useState<MalaiChat | null>(null);

  const ensureChat = async (): Promise<MalaiChat> => {
    if (chat) return chat;
    const thread = await openThread(allResourceIds(subjects));
    const now = Date.now();
    const created: MalaiChat = {
      id: newChatId(),
      ...thread,
      title: "Voice chat",
      subjectId: subject?.id ?? null,
      preview: "",
      createdAt: now,
      updatedAt: now,
    };
    createdHere.current = created.id;
    addChat(created);
    window.history.replaceState(null, "", malaiChatPath(created.id));
    return created;
  };

  const dictation = useDictation({
    getSession: async () => {
      const c = await ensureChat();
      return { sessionId: c.sessionId };
    },
    onFinal: (t) => setDraft((prev) => joinText(prev, t)),
  });
  const stopDictation = dictation.stop;

  // Attaching an image, like dictating, needs the chat to exist first.
  // The chat an image went to, so the question goes to the same one even
  // before the address has caught up.
  const imageChat = useRef<MalaiChat | null>(null);
  useEffect(() => {
    if (imageChat.current && imageChat.current.id !== chatId) imageChat.current = null;
  }, [chatId]);
  const images = useChatImages(async () => {
    const c = await ensureChat();
    imageChat.current = c;
    return { sessionId: c.sessionId, threadId: c.threadId === MAIN_THREAD_ID ? undefined : c.threadId };
  });
  const clearImages = images.clear;
  const imageNames = images.readyNames;
  const drop = useImageDrop((f) => void images.add(f));

  const openVoice = async (): Promise<void> => {
    stopDictation();
    try {
      setVoiceChat(await ensureChat());
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  /** The same scope fields the typed question sends to streamChat. */
  const voiceStart = (): VoiceStartFields => {
    const c = voiceChat!;
    return {
      session_id: c.sessionId,
      thread_id: c.threadId === MAIN_THREAD_ID ? undefined : c.threadId,
      current_time_ms: 0,
      completed_resource_ids: doneResourceIds(subject ? [subject] : subjects),
      ...(subject ? { scope_resource_ids: subject.resource_ids ?? [] } : { general: true }),
    };
  };

  /** A spoken turn: shown in the chat like a typed message, updated in place as it is transcribed. */
  const voiceTurn = (turn: VoiceTurn): void => {
    setMessages((prev) => {
      const i = prev.findIndex((m) => m.id === turn.id);
      if (i >= 0) return prev.map((m, j) => (j === i ? { ...m, content: turn.text } : m));
      return [
        ...prev,
        { id: turn.id, role: turn.role, content: turn.text, citations: [], created_at: new Date().toISOString(), ...(turn.role === "assistant" ? { kind: "conversational" as const } : {}) },
      ];
    });
  };

  /** A voice recap: saved to the chat, after the spoken turns. */
  const voiceRecap = (res: ChatResponse): void => {
    setMessages((prev) => [
      ...prev,
      {
        id: newMessageId(),
        role: "assistant",
        content: res.answer,
        citations: res.citations,
        created_at: new Date().toISOString(),
        kind: res.mode,
      },
    ]);
    if (voiceChat) updateChat(voiceChat.id, { updatedAt: Date.now() });
  };

  const send = useCallback(async () => {
    const typed = draft.trim();
    if (!typed || loading) return;
    stopDictation();
    if (subject && (subject.resource_count ?? 0) === 0) {
      setError(`“${subject.name}” has no materials yet. Add some to it, or start a chat outside the subject.`);
      return;
    }
    // A slash command sets the answer style, or opens a quiz; so does a
    // message that asks for a quiz in so many words ("quiz me on entropy").
    const cmd = parseCommand(typed, slashContext);
    const intent = cmd ? null : detectQuizIntent(typed);
    const quizAsk = cmd?.command.quiz
      ? { ...quizFromCommand(cmd.command, cmd.rest), request: cmd.rest ? `Quiz me: ${cmd.rest}` : "Quiz me" }
      : intent
        ? { ...intent, request: typed }
        : null;
    const mode: ChatMode = cmd?.command.mode ?? "qa";
    const text = quizAsk ? quizAsk.request : cmd ? commandQuestion(cmd.command, cmd.rest) : typed;
    setError(null);
    setDraft("");
    if (!quizAsk) {
      setLoading(true);
      setPreview({ status: null, text: "" });
    }
    setMessages((prev) => [
      ...prev,
      { id: newMessageId(), role: "user", content: withImageNote(text, imageNames), citations: [], created_at: new Date().toISOString() },
    ]);
    clearImages();

    const scope = allResourceIds(subjects);
    let current = chat ?? imageChat.current;
    try {
      if (!current) {
        const thread = await openThread(scope);
        const now = Date.now();
        current = {
          id: newChatId(),
          ...thread,
          title: titleFrom(text),
          subjectId: subject?.id ?? null,
          preview: titleFrom(text),
          createdAt: now,
          updatedAt: now,
        };
        createdHere.current = current.id;
        addChat(current);
        // Same page, new address: no remount, so the answer keeps streaming.
        window.history.replaceState(null, "", malaiChatPath(current.id));
      }

      if (quizAsk) {
        // The quiz is written in the panel; the chat records it when it is.
        setQuizChat(current);
        setQuizPreset({ ...quizAsk, nonce: Date.now() });
        setQuizOpen(true);
        updateChat(current.id, { updatedAt: Date.now(), preview: titleFrom(text) });
        return;
      }

      const ask = (c: MalaiChat) => {
        const req: ChatRequest = {
          session_id: c.sessionId,
          thread_id: c.threadId === MAIN_THREAD_ID ? undefined : c.threadId,
          current_time_ms: 0,
          message: text,
          mode,
          completed_resource_ids: doneResourceIds(subject ? [subject] : subjects),
          // No subject: a general chat; materials are read only when asked for.
          ...(subject ? { scope_resource_ids: subject.resource_ids ?? [] } : { general: true }),
        };
        return streamChat(req, {
          onStatus: (status) => setPreview((p) => ({ status, text: p?.text ?? "" })),
          onText: (t) => setPreview((p) => ({ status: p?.status ?? null, text: t })),
        });
      };

      let res;
      try {
        res = await ask(current);
      } catch (err) {
        if (!sessionGone(err)) throw err;
        // The server lost this chat's session (restart). Carry on in a new
        // thread; the earlier messages stay on screen but are gone server-side.
        const thread = await openThread(scope, true);
        current = { ...current, ...thread };
        updateChat(current.id, thread);
        res = await ask(current);
      }

      setMessages((prev) => [
        ...prev,
        {
          id: newMessageId(),
          role: "assistant",
          content: res.answer,
          citations: res.citations,
          created_at: new Date().toISOString(),
          kind: res.refused ? "refused" : res.mode,
        },
      ]);
      updateChat(current.id, {
        updatedAt: Date.now(),
        preview: titleFrom(text),
        ...(res.thread_title ? { title: res.thread_title } : {}),
      });
    } catch (err) {
      setError(err instanceof ApiError ? formatApiError(err) : err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
      setPreview(null);
    }
  }, [draft, loading, subject, subjects, chat, slashContext, stopDictation, imageNames, clearImages]);

  /** What a quiz from this chat may draw from: the chat's own reach. */
  const quizScope: QuizScope | null = quizChat
    ? {
        thread_id: quizChat.threadId,
        completed_resource_ids: doneResourceIds(subject ? [subject] : subjects),
        ...(subject ? { scope_resource_ids: subject.resource_ids ?? [] } : { general: true }),
      }
    : null;

  const note = (content: string, refused = false): void =>
    setMessages((prev) => [
      ...prev,
      {
        id: newMessageId(),
        role: "assistant",
        content,
        citations: [],
        created_at: new Date().toISOString(),
        ...(refused ? { kind: "refused" as const } : {}),
      },
    ]);

  // Quiz events land in the chat: written by the server, so they are part of
  // the conversation Malai sees next turn. Not answering is fine.
  const quizStarted = async (request: string, count: number): Promise<void> => {
    if (!quizChat) return;
    try {
      const lines = await postQuizNote(
        { session_id: quizChat.sessionId, thread_id: quizChat.threadId, event: "started", request, count },
      );
      setMessages((prev) => [...prev, ...lines.filter((m) => m.role === "assistant")]);
    } catch {
      note(`I've made you a ${count}-question quiz. It's open beside the chat.`);
    }
  };
  const quizFinished = async (o: QuizOutcome): Promise<void> => {
    if (!quizChat) return;
    try {
      const lines = await postQuizNote(
        {
          session_id: quizChat.sessionId,
          thread_id: quizChat.threadId,
          event: "completed",
          count: o.count,
          score: o.score,
          to_review: o.toReview,
        },
      );
      setMessages((prev) => [...prev, ...lines]);
      updateChat(quizChat.id, { updatedAt: Date.now() });
    } catch {
      note(`**Quiz completed** — you scored **${o.score} / ${o.count}**.`);
    }
  };


  const start = (prompt: string) => {
    setDraft(prompt);
    requestAnimationFrame(() => {
      const el = inputRef.current;
      if (el) {
        el.focus();
        el.setSelectionRange(prompt.length, prompt.length);
      }
    });
  };

  const notReady = subjectsState !== "ready";
  const composer = voiceChat && voiceModels ? (
    <VoicePanel
      models={voiceModels}
      getStart={voiceStart}
      onRecap={voiceRecap}
      onTurn={voiceTurn}
      onClose={() => setVoiceChat(null)}
      subtitle={subject ? subject.name : "General"}
    />
  ) : (
    <Composer
      value={withInterim(draft, dictation.interim)}
      onChange={(v) => setDraft(stripInterim(v, draft, dictation.interim))}
      voice={
        voiceModels
          ? { state: dictation.state, error: dictation.error, onMic: dictation.toggle, onTalk: () => void openVoice() }
          : undefined
      }
      onSend={() => void send()}
      images={{ items: images.items, onFiles: (f) => void images.add(f), onRemove: images.remove, uploading: images.uploading }}
      disabled={notReady}
      busy={loading}
      placeholder={subject ? `Ask about ${subject.name}, or type / for commands…` : "Ask anything, or type / for commands…"}
      autoFocus={!chatId}
      inputRef={inputRef}
      slashContext={slashContext}
      pickerDown={chatId === null && messages.length === 0 && !loading}
    />
  );
  const notes = (
    <>
      {subjectsState === "error" && (
        <p className="mt-3 text-sm text-red-600" role="alert">Could not load your subjects: {subjectsError}</p>
      )}
      {error && (
        <p className="mt-3 rounded-xl border border-red-200 bg-red-50 px-4 py-2.5 text-sm text-red-700" role="alert">{error}</p>
      )}
    </>
  );

  const missing = mounted && chatId !== null && chat === null && chatId !== createdHere.current;
  const empty = chatId === null && messages.length === 0 && !loading && !voiceChat;

  return (
    <div
      className={`relative flex min-h-[calc(100dvh-7rem)] flex-col transition-[margin] duration-300 md:min-h-[calc(100dvh-5rem)] ${
        quizOpen ? "lg:mr-[27rem]" : ""
      }`}
      {...drop.bind}
    >
      <DropOverlay show={drop.dragging} fixed />
      {!(empty && subject) && (
        <header className="relative z-10 md:pr-16">
          <h1 className="text-2xl font-bold tracking-tight text-gray-900">Malai</h1>
          <p className="flex min-w-0 items-center gap-2 text-sm text-gray-500">
            <span className="truncate">{chat ? chat.title : "Your learning companion"}</span>
            {chat && subject && (
              <Link
                href={malaiSubjectPath(subject.id)}
                className="inline-flex shrink-0 items-center gap-1.5 rounded-full bg-violet-50 px-2.5 py-0.5 text-xs font-medium text-violet-700 transition hover:bg-violet-100"
              >
                <span className={`h-2 w-2 rounded-full ${subjectAccent(subject.name).avatar}`} aria-hidden="true" />
                {subject.name}
              </Link>
            )}
            {chat && !quizOpen && (
              <button
                type="button"
                onClick={() => {
                  setQuizChat(chat);
                  setQuizOpen(true);
                }}
                className="ml-auto inline-flex shrink-0 items-center gap-1.5 rounded-full border border-violet-200 bg-white px-3 py-1 text-xs font-medium text-violet-700 transition hover:bg-violet-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-violet-400"
              >
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="h-3.5 w-3.5" aria-hidden="true">
                  <path d="M12 22a10 10 0 110-20 10 10 0 010 20zM12 16a4 4 0 110-8 4 4 0 010 8zM12 12h.01" />
                </svg>
                Quiz
              </button>
            )}
          </p>
        </header>
      )}

      {empty && subject ? (
        <SubjectHome
          subjectId={subject.id}
          name={subject.name}
          chats={chats.filter((c) => c.subjectId === subject.id)}
          composer={composer}
          notes={notes}
        />
      ) : empty ? (
        <div className="relative flex flex-1 items-center justify-center py-10">
          <div aria-hidden="true" className="pointer-events-none absolute inset-0 overflow-hidden">
            <div className="absolute left-1/2 top-1/3 h-72 w-72 -translate-x-[80%] rounded-full bg-violet-200/40 blur-3xl" />
            <div className="absolute left-1/2 top-1/2 h-72 w-72 translate-x-[10%] rounded-full bg-indigo-200/30 blur-3xl" />
          </div>
          <div className="relative w-full max-w-3xl text-center">
            <p className="text-lg font-medium text-violet-700">
              Hello, {LEARNER_NAME} <span aria-hidden="true">👋</span>
            </p>
            <h2 className="mt-2 text-3xl font-bold tracking-tight text-gray-900 sm:text-[2.6rem] sm:leading-tight">
              How can I help you today?
            </h2>
            <p className="mt-3 text-base text-gray-500">
              Ask questions, get explanations, solve doubts, practice problems and more.
            </p>
            <div className="mt-8 text-left">{composer}</div>
            {notes}
            <div className="mt-6 flex flex-wrap justify-center gap-3">
              {STARTERS.map((s) => (
                <button
                  key={s.label}
                  type="button"
                  onClick={() => start(s.prompt)}
                  className="inline-flex items-center gap-2.5 rounded-full border border-violet-100 bg-white/70 px-4 py-2.5 text-sm text-gray-700 shadow-sm transition hover:border-violet-300 hover:bg-white hover:text-violet-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-violet-400"
                >
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="h-5 w-5 text-violet-600" aria-hidden="true">
                    <path d={s.icon} />
                  </svg>
                  {s.label}
                </button>
              ))}
            </div>
            <p className="mt-6 text-xs text-gray-400">
              Malai answers from your subjects&apos; documents and the videos you&apos;ve marked as done.
            </p>
          </div>
        </div>
      ) : (
        <>
          <div className="mx-auto w-full max-w-3xl flex-1 pb-6 pt-8">
            {missing ? (
              <p className="rounded-xl border border-gray-200 bg-white px-4 py-6 text-center text-sm text-gray-500">
                This chat is not in this browser. Start a new one from the sidebar.
              </p>
            ) : history === "loading" ? (
              <div role="status" className="flex items-center justify-center gap-2 py-10 text-sm text-gray-500">
                <span className="h-4 w-4 animate-spin rounded-full border-2 border-violet-500 border-t-transparent" aria-hidden="true" />
                Loading the conversation…
              </div>
            ) : (
              <>
                {history === "gone" && (
                  <p className="mb-4 rounded-xl border border-amber-200 bg-amber-50 px-4 py-2.5 text-sm text-amber-800">
                    The server no longer has this conversation&apos;s earlier messages (it restarted). You can keep asking here.
                  </p>
                )}
                <MessageList fill messages={messages} loading={loading} preview={preview} onSeek={() => {}} />
              </>
            )}
          </div>
          {!missing && (
            <div className="sticky bottom-0 z-10 -mx-4 bg-gradient-to-t from-gray-50 via-gray-50/95 to-transparent px-4 pb-2 pt-6 sm:-mx-6 sm:px-6 lg:-mx-10 lg:px-10">
              <div className="mx-auto w-full max-w-3xl">
                {composer}
                {notes}
              </div>
            </div>
          )}
        </>
      )}

      {quizChat && quizScope && (
        <aside
          aria-label="Quiz"
          aria-hidden={!quizOpen}
          className={`fixed inset-y-0 right-0 z-50 flex w-full flex-col border-l border-gray-200 bg-white shadow-2xl shadow-gray-900/10 transition-transform duration-300 ease-out sm:w-[26rem] ${
            quizOpen ? "translate-x-0" : "pointer-events-none translate-x-full"
          }`}
        >
          <div className="flex shrink-0 items-center gap-2 border-b border-gray-100 px-4 py-3">
            <h2 className="flex-1 text-sm font-semibold text-gray-900">Quiz</h2>
            <span className="truncate text-xs text-gray-400">{subject ? subject.name : "General"}</span>
            <button
              type="button"
              onClick={() => setQuizOpen(false)}
              aria-label="Close quiz"
              title="Close (your quiz is kept)"
              className="flex h-8 w-8 items-center justify-center rounded-full text-gray-400 transition hover:bg-gray-100 hover:text-gray-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-violet-400"
            >
              <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" className="h-4 w-4" aria-hidden="true">
                <path d="M5 5l10 10M15 5 5 15" />
              </svg>
            </button>
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto px-4 py-4">
            <QuizPanel
              key={quizChat.id}
              sessionId={quizChat.sessionId}
              getCurrentTimeMs={() => 0}
              scope={quizScope}
              subjectId={subject?.id ?? null}
              chatId={quizChat.id}
              sourceNote={
                subject
                  ? `From ${subject.name}: its documents and the videos you've marked done.`
                  : "From general knowledge on your topic. Mention your notes or lectures to quiz on your materials."
              }
              onSeek={() => {}}
              offline={false}
              preset={quizPreset}
              onStarted={(request, count) => void quizStarted(request, count)}
              onFailed={(_request, message) => note(`I couldn't write that quiz: ${message}`, true)}
              onFinished={(o) => void quizFinished(o)}
            />
          </div>
        </aside>
      )}
    </div>
  );
}

/**
 * A subject's page in Malai, like a project: start a chat filed under it,
 * reopen one of its chats, or open one of its resources beside Malai.
 */
function SubjectHome({
  subjectId,
  name,
  chats,
  composer,
  notes,
}: {
  subjectId: string;
  name: string;
  chats: MalaiChat[];
  composer: React.ReactNode;
  notes: React.ReactNode;
}) {
  const { resources, state, error } = useSubjectResources(subjectId);
  const [adding, setAdding] = useState(false);
  const [ingesting, setIngesting] = useState(false);
  const closeAdd = useCallback(() => setAdding(false), []);
  const card = "rounded-2xl border border-gray-200 bg-white shadow-sm";
  const row =
    "flex w-full items-center gap-3 rounded-xl px-3 py-2.5 text-left transition hover:bg-violet-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-violet-300";

  return (
    <div className="mx-auto w-full max-w-4xl pb-10 pt-2 md:pr-16">
      <div className="flex items-center gap-4">
        <span className={`flex h-14 w-14 shrink-0 items-center justify-center rounded-2xl text-lg font-bold text-white ${subjectAccent(name).avatar}`}>
          {initials(name)}
        </span>
        <div className="min-w-0">
          <h1 className="truncate text-2xl font-bold tracking-tight text-gray-900">{name}</h1>
          <p className="text-sm text-gray-500">
            {chats.length} chat{chats.length === 1 ? "" : "s"}
            {state === "ready" ? ` · ${resources.length} resource${resources.length === 1 ? "" : "s"}` : ""}
          </p>
        </div>
      </div>

      <div className="mt-6">{composer}</div>
      {notes}
      <p className="mt-2 px-2 text-xs text-gray-400">
        New chats here are filed under {name} and read only its materials.
      </p>

      <div className="mt-8 grid gap-5 lg:grid-cols-2">
        <section className={card} aria-labelledby="subject-chats">
          <h2 id="subject-chats" className="border-b border-gray-100 px-5 py-3.5 text-sm font-semibold text-gray-900">
            Chats
          </h2>
          {chats.length === 0 ? (
            <p className="px-5 py-6 text-sm text-gray-500">
              No chats yet. Ask something above, or open a resource and ask beside it.
            </p>
          ) : (
            <ul className="flex flex-col gap-0.5 p-2">
              {chats.map((c) => (
                <li key={c.id}>
                  <Link href={chatPath(c)} className={row}>
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-sm font-medium text-gray-900">{c.title}</span>
                      {c.preview && <span className="block truncate text-xs text-gray-500">{c.preview}</span>}
                    </span>
                    <span className="shrink-0 text-xs text-gray-400">{chatDate(c.updatedAt)}</span>
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </section>

        <section className={card} aria-labelledby="subject-resources">
          <div className="flex items-center gap-3 border-b border-gray-100 py-2.5 pl-5 pr-3">
            <h2 id="subject-resources" className="flex-1 text-sm font-semibold text-gray-900">
              Resources
            </h2>
            <button
              type="button"
              onClick={() => setAdding(true)}
              title={`Add content to ${name}`}
              className="inline-flex shrink-0 items-center gap-1.5 rounded-lg bg-violet-600 px-3 py-1.5 text-xs font-semibold text-white shadow-sm transition hover:bg-violet-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-violet-300"
            >
              <svg viewBox="0 0 24 24" className="h-3.5 w-3.5" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" aria-hidden="true">
                <path d="M12 5v14M5 12h14" />
              </svg>
              Add
              {ingesting && (
                <span className="inline-block h-3 w-3 animate-spin rounded-full border-2 border-white border-t-transparent" aria-label="Processing" />
              )}
            </button>
          </div>
          <AddContentDialog subjectId={subjectId} open={adding} onClose={closeAdd} onBusyChange={setIngesting} />
          {state === "loading" || state === "idle" ? (
            <p role="status" className="px-5 py-6 text-sm text-gray-500">Loading resources…</p>
          ) : state === "error" ? (
            <p role="alert" className="px-5 py-6 text-sm text-red-600">Could not load resources: {error}</p>
          ) : resources.length === 0 ? (
            <p className="px-5 py-6 text-sm text-gray-500">No resources in this subject yet.</p>
          ) : (
            <ul className="flex flex-col gap-0.5 p-2">
              {resources.map((r) => {
                const body = (
                  <>
                    <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-gray-100 text-base" aria-hidden="true">
                      {resourceIcon(r)}
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-sm font-medium text-gray-900">{r.title ?? r.id}</span>
                      <span className="block text-xs text-gray-500">{resourceKindLabel(r)}</span>
                    </span>
                  </>
                );
                return (
                  <li key={r.id}>
                    {toSelected(r) ? (
                      <Link href={malaiLessonPath(subjectId, r.id)} className={row}>
                        {body}
                      </Link>
                    ) : (
                      <div className={`${row} cursor-not-allowed opacity-60 hover:bg-transparent`} title="The original file is unavailable">
                        {body}
                      </div>
                    )}
                  </li>
                );
              })}
            </ul>
          )}
        </section>
      </div>
    </div>
  );
}
