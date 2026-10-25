"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useSearchParams } from "next/navigation";
import {
  ApiError,
  MAIN_THREAD_ID,
  formatApiError,
  getMessages,
  streamChat,
  type ChatMessage,
  type ChatRequest,
} from "@/lib/api";
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
import { initials, subjectAccent } from "@/components/SubjectCard";
import { useSubjects } from "@/components/useSubjects";

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
}: {
  value: string;
  onChange: (v: string) => void;
  onSend: () => void;
  disabled: boolean;
  busy: boolean;
  placeholder: string;
  autoFocus?: boolean;
  inputRef: React.RefObject<HTMLTextAreaElement | null>;
}) {
  // Grow with the text, up to a cap.
  useEffect(() => {
    const el = inputRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 180)}px`;
  }, [value, inputRef]);

  const canSend = !disabled && !busy && value.trim().length > 0;

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        if (canSend) onSend();
      }}
      className="flex items-end gap-2 rounded-[1.75rem] border border-gray-200 bg-white py-2 pl-5 pr-2 shadow-lg shadow-violet-900/5 transition focus-within:border-violet-300 focus-within:ring-4 focus-within:ring-violet-100"
    >
      <label className="sr-only" htmlFor="malai-input">Message Malai</label>
      <textarea
        id="malai-input"
        ref={inputRef}
        rows={1}
        value={value}
        autoFocus={autoFocus}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
            e.preventDefault();
            if (canSend) onSend();
          }
        }}
        placeholder={placeholder}
        className="max-h-[180px] min-h-[2.75rem] flex-1 resize-none bg-transparent py-2.5 text-[15px] text-gray-900 placeholder-gray-400 outline-none"
      />
      <button
        type="submit"
        disabled={!canSend}
        aria-label="Send"
        className="flex h-12 w-12 shrink-0 items-center justify-center rounded-full bg-violet-600 text-white shadow-md shadow-violet-600/30 transition hover:bg-violet-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-violet-400 focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:bg-violet-300 disabled:shadow-none"
      >
        {busy ? (
          <span className="h-4 w-4 animate-spin rounded-full border-2 border-white border-t-transparent" aria-hidden="true" />
        ) : (
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" className="h-5 w-5" aria-hidden="true">
            <path d="M12 19V5M5 12l7-7 7 7" />
          </svg>
        )}
      </button>
    </form>
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

  useEffect(() => setMounted(true), []);

  const chat: MalaiChat | null = chatId ? chats.find((c) => c.id === chatId) ?? null : null;
  const known = (id: string | null) => (id && subjects.some((s) => s.id === id) ? id : null);
  const subjectId = chat ? known(chat.subjectId) : known(pageSubjectId);
  const subject = subjects.find((s) => s.id === subjectId) ?? null;

  // Load the open chat's history whenever the chat changes.
  const chatSession = chat ? `${chat.sessionId}|${chat.threadId}|${chat.sessionToken}` : null;
  useEffect(() => {
    setError(null);
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
          chat.sessionToken,
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
  }, [messages.length, preview?.text]);

  const send = useCallback(async () => {
    const text = draft.trim();
    if (!text || loading) return;
    if (subject && (subject.resource_count ?? 0) === 0) {
      setError(`“${subject.name}” has no materials yet. Add some to it, or start a chat outside the subject.`);
      return;
    }
    setError(null);
    setDraft("");
    setLoading(true);
    setPreview({ status: null, text: "" });
    setMessages((prev) => [
      ...prev,
      { id: newMessageId(), role: "user", content: text, citations: [], created_at: new Date().toISOString() },
    ]);

    const scope = allResourceIds(subjects);
    let current = chat;
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

      const ask = (c: MalaiChat) => {
        const req: ChatRequest = {
          session_id: c.sessionId,
          session_token: c.sessionToken,
          thread_id: c.threadId === MAIN_THREAD_ID ? undefined : c.threadId,
          current_time_ms: 0,
          message: text,
          mode: "qa",
          completed_resource_ids: doneResourceIds(subject ? [subject] : subjects),
          ...(subject ? { scope_resource_ids: subject.resource_ids ?? [] } : {}),
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
      updateChat(current.id, { updatedAt: Date.now(), preview: titleFrom(text) });
    } catch (err) {
      setError(err instanceof ApiError ? formatApiError(err) : err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
      setPreview(null);
    }
  }, [draft, loading, subject, subjects, chat]);

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
  const composer = (
    <Composer
      value={draft}
      onChange={setDraft}
      onSend={() => void send()}
      disabled={notReady}
      busy={loading}
      placeholder={subject ? `Ask about ${subject.name}…` : "Ask anything about your subjects…"}
      autoFocus={!chatId}
      inputRef={inputRef}
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
  const empty = chatId === null && messages.length === 0 && !loading;

  return (
    <div className="relative flex min-h-[calc(100dvh-7rem)] flex-col md:min-h-[calc(100dvh-5rem)]">
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
          <h2 id="subject-resources" className="border-b border-gray-100 px-5 py-3.5 text-sm font-semibold text-gray-900">
            Resources
          </h2>
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
