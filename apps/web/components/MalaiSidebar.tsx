"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { deleteThread, renameThread, type Subject } from "@/lib/api";
import { resourceIcon, resourceKindLabel, toSelected } from "@/lib/learn";
import {
  MALAI_PATH,
  chatDate,
  chatPath,
  malaiLessonPath,
  malaiSubjectPath,
  removeChat,
  updateChat,
  useMalaiChats,
  useSubjectResources,
  type MalaiChat,
} from "@/lib/malai";
import { initials, subjectAccent } from "@/components/SubjectCard";
import { useSubjects } from "@/components/useSubjects";

/**
 * The sidebar while Malai is open: its chats instead of the app's navigation.
 * Chats filed under a subject sit inside that subject; the rest are "Recent
 * chats". Every chat can be renamed, moved to (or out of) a subject, or deleted.
 * A subject's name opens its page; its "Resources" row lists its materials in
 * a flyout to the right, and each one opens beside Malai.
 */

export interface MalaiSidebarProps {
  collapsed: boolean;
  onToggleCollapse: () => void;
  /** Swap back to the app's main navigation. */
  onBack: () => void;
  /** Called after any navigation (closes the mobile drawer). */
  onNavigate?: () => void;
  /** The mobile drawer has its own close button and no collapse. */
  inDrawer?: boolean;
}

function Icon({ d, className = "h-5 w-5" }: { d: string; className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className={`${className} shrink-0`} aria-hidden="true">
      <path d={d} />
    </svg>
  );
}

const NEW_CHAT_ICON = "M12 20h9M16.5 3.5a2.1 2.1 0 013 3L7 19l-4 1 1-4z";
const BACK_ICON = "M19 12H5M12 19l-7-7 7-7";
const COLLAPSE_ICON = "M15 6l-6 6 6 6";
const EXPAND_ICON = "M9 6l6 6-6 6";

function ChatMenu({
  chat,
  subjects,
  onClose,
  anchor,
  onRename,
  onDeleted,
}: {
  chat: MalaiChat;
  subjects: Subject[];
  onClose: () => void;
  anchor: DOMRect;
  onRename: () => void;
  onDeleted: () => void;
}) {
  const ref = useRef<HTMLDivElement | null>(null);
  const [view, setView] = useState<"main" | "move" | "delete">("main");

  useEffect(() => {
    ref.current?.querySelector<HTMLElement>("[data-first]")?.focus();
  }, [view]);

  useEffect(() => {
    function onPointer(e: MouseEvent) {
      if (!ref.current?.contains(e.target as Node)) onClose();
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    document.addEventListener("mousedown", onPointer);
    document.addEventListener("keydown", onKey);
    window.addEventListener("resize", onClose);
    return () => {
      document.removeEventListener("mousedown", onPointer);
      document.removeEventListener("keydown", onKey);
      window.removeEventListener("resize", onClose);
    };
  }, [onClose]);

  // Fixed, so the scrolling sidebar never clips it.
  const top = Math.min(anchor.bottom + 4, window.innerHeight - 280);
  const left = Math.min(anchor.right - 224, window.innerWidth - 232);
  const item = "flex w-full items-center gap-2 px-3 py-2 text-left text-sm transition focus:outline-none";

  async function confirmDelete() {
    // The server copy goes first, best effort: a session lost on restart has
    // nothing left to delete, and the chat must still leave the list.
    try {
      await deleteThread(chat.sessionId, chat.threadId, chat.sessionToken);
    } catch {
      // Already gone server-side.
    }
    removeChat(chat.id);
    onDeleted();
  }

  return (
    <div
      ref={ref}
      role="menu"
      aria-label={`Actions for ${chat.title}`}
      style={{ top, left: Math.max(8, left) }}
      className="fixed z-[70] max-h-72 w-56 overflow-y-auto rounded-xl border border-gray-200 bg-white py-1 text-gray-700 shadow-xl"
    >
      {view === "main" && (
        <>
          <button type="button" role="menuitem" data-first className={`${item} hover:bg-gray-50 focus:bg-gray-50`} onClick={onRename}>
            <Icon d={NEW_CHAT_ICON} className="h-4 w-4" />
            Rename
          </button>
          {!chat.resourceId && (
            <button type="button" role="menuitem" className={`${item} hover:bg-gray-50 focus:bg-gray-50`} onClick={() => setView("move")}>
              <Icon d="M3 7a2 2 0 012-2h4l2 2h8a2 2 0 012 2v8a2 2 0 01-2 2H5a2 2 0 01-2-2z" className="h-4 w-4" />
              Move to subject
              <Icon d={EXPAND_ICON} className="ml-auto h-4 w-4 text-gray-400" />
            </button>
          )}
          <button type="button" role="menuitem" className={`${item} text-red-600 hover:bg-red-50 focus:bg-red-50`} onClick={() => setView("delete")}>
            <Icon d="M3 6h18M8 6V4a2 2 0 012-2h4a2 2 0 012 2v2M19 6l-1 14a2 2 0 01-2 2H8a2 2 0 01-2-2L5 6" className="h-4 w-4" />
            Delete
          </button>
        </>
      )}
      {view === "move" && (
        <>
          <button type="button" data-first className={`${item} text-xs font-semibold uppercase tracking-wide text-gray-400 hover:text-gray-700`} onClick={() => setView("main")}>
            <Icon d={COLLAPSE_ICON} className="h-3.5 w-3.5" />
            Move to subject
          </button>
          {subjects.map((s) => (
            <button
              key={s.id}
              type="button"
              role="menuitemradio"
              aria-checked={chat.subjectId === s.id}
              className={`${item} hover:bg-gray-50 focus:bg-gray-50`}
              onClick={() => {
                updateChat(chat.id, { subjectId: s.id });
                onClose();
              }}
            >
              <span className={`flex h-6 w-6 shrink-0 items-center justify-center rounded-md text-[10px] font-bold text-white ${subjectAccent(s.name).avatar}`}>
                {initials(s.name)}
              </span>
              <span className="truncate">{s.name}</span>
              {chat.subjectId === s.id && <Icon d="M20 6L9 17l-5-5" className="ml-auto h-4 w-4 text-violet-600" />}
            </button>
          ))}
          {subjects.length === 0 && <p className="px-3 py-2 text-sm text-gray-500">No subjects yet.</p>}
          {chat.subjectId && (
            <button
              type="button"
              className={`${item} border-t border-gray-100 hover:bg-gray-50 focus:bg-gray-50`}
              onClick={() => {
                updateChat(chat.id, { subjectId: null });
                onClose();
              }}
            >
              <Icon d="M18 6L6 18M6 6l12 12" className="h-4 w-4" />
              Remove from subject
            </button>
          )}
        </>
      )}
      {view === "delete" && (
        <div className="px-3 py-2">
          <p className="text-sm font-medium text-gray-900">Delete this chat?</p>
          <p className="mt-0.5 text-xs text-gray-500">Its messages are removed for good.</p>
          <div className="mt-3 flex gap-2">
            <button type="button" data-first className="flex-1 rounded-lg border border-gray-200 px-3 py-1.5 text-sm hover:bg-gray-50" onClick={() => setView("main")}>
              Cancel
            </button>
            <button type="button" className="flex-1 rounded-lg bg-red-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-red-700" onClick={() => void confirmDelete()}>
              Delete
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

function ChatRow({
  chat,
  active,
  subjects,
  compact,
  onOpen,
  onDeletedActive,
}: {
  chat: MalaiChat;
  active: boolean;
  subjects: Subject[];
  /** Inside a subject: title and date only. */
  compact?: boolean;
  onOpen: (chat: MalaiChat) => void;
  onDeletedActive: () => void;
}) {
  const [anchor, setAnchor] = useState<DOMRect | null>(null);
  const [renaming, setRenaming] = useState(false);
  const [draft, setDraft] = useState(chat.title);
  const inputRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    if (renaming) inputRef.current?.select();
  }, [renaming]);

  function commitRename() {
    const title = draft.replace(/\s+/g, " ").trim();
    setRenaming(false);
    if (!title || title === chat.title) return;
    updateChat(chat.id, { title });
    void renameThread(chat.sessionId, chat.threadId, title, chat.sessionToken).catch(() => {
      // The local title is what the list shows; the server copy is a courtesy.
    });
  }

  return (
    <li className="group/chat relative">
      {renaming ? (
        <div className="px-2 py-1.5">
          <input
            ref={inputRef}
            value={draft}
            maxLength={80}
            onChange={(e) => setDraft(e.target.value)}
            onBlur={commitRename}
            onKeyDown={(e) => {
              if (e.key === "Enter") commitRename();
              if (e.key === "Escape") {
                setDraft(chat.title);
                setRenaming(false);
              }
            }}
            aria-label="Chat name"
            className="w-full rounded-lg border border-violet-400 bg-white/10 px-2.5 py-1.5 text-sm text-white outline-none ring-2 ring-violet-500/40"
          />
        </div>
      ) : (
        <button
          type="button"
          onClick={() => onOpen(chat)}
          aria-current={active ? "page" : undefined}
          className={[
            "flex w-full flex-col rounded-xl py-2.5 pl-3 pr-9 text-left transition focus:outline-none focus-visible:ring-2 focus-visible:ring-violet-400",
            active ? "bg-violet-600/25 ring-1 ring-violet-500/40" : "hover:bg-white/5",
          ].join(" ")}
        >
          <span className="flex w-full items-baseline gap-2">
            <span className={`min-w-0 flex-1 truncate ${compact ? "text-[13px]" : "text-sm font-medium"} text-white`}>{chat.title}</span>
            <span className="shrink-0 text-[11px] text-gray-400 group-hover/chat:invisible">{chatDate(chat.updatedAt)}</span>
          </span>
          {!compact && chat.preview && <span className="mt-0.5 truncate text-xs text-gray-400">{chat.preview}</span>}
        </button>
      )}
      {!renaming && (
        <button
          type="button"
          aria-haspopup="menu"
          aria-expanded={anchor !== null}
          aria-label={`More actions for ${chat.title}`}
          onClick={(e) => setAnchor(anchor ? null : e.currentTarget.getBoundingClientRect())}
          className={[
            "absolute right-1.5 top-2 rounded-lg p-1 text-gray-400 transition hover:bg-white/10 hover:text-white focus:opacity-100 focus:outline-none focus-visible:ring-2 focus-visible:ring-violet-400",
            anchor ? "opacity-100" : "opacity-0 group-hover/chat:opacity-100",
          ].join(" ")}
        >
          <svg viewBox="0 0 24 24" fill="currentColor" className="h-4 w-4" aria-hidden="true">
            <circle cx="5" cy="12" r="1.75" />
            <circle cx="12" cy="12" r="1.75" />
            <circle cx="19" cy="12" r="1.75" />
          </svg>
        </button>
      )}
      {anchor && (
        <ChatMenu
          chat={chat}
          subjects={subjects}
          anchor={anchor}
          onClose={() => setAnchor(null)}
          onRename={() => {
            setAnchor(null);
            setDraft(chat.title);
            setRenaming(true);
          }}
          onDeleted={() => {
            setAnchor(null);
            if (active) onDeletedActive();
          }}
        />
      )}
    </li>
  );
}

/**
 * A subject's materials, opened to the right of its "Resources" row. Fixed
 * and portalled to <body>: the sidebar's own layer would otherwise put it
 * under the page beside it.
 */
function ResourcesFlyout({
  subjectId,
  subjectName,
  anchor,
  onClose,
  onOpen,
}: {
  subjectId: string;
  subjectName: string;
  anchor: DOMRect;
  onClose: () => void;
  onOpen: (resourceId: string) => void;
}) {
  const ref = useRef<HTMLDivElement | null>(null);
  const { resources, state, error } = useSubjectResources(subjectId);

  useEffect(() => {
    function onPointer(e: MouseEvent) {
      const target = e.target as Element;
      // A Resources row toggles the flyout itself.
      if (!ref.current?.contains(target) && !target.closest?.("[data-flyout-trigger]")) onClose();
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    // Wait a tick: the click that opened the flyout must not close it.
    const t = window.setTimeout(() => document.addEventListener("mousedown", onPointer), 0);
    document.addEventListener("keydown", onKey);
    window.addEventListener("resize", onClose);
    return () => {
      window.clearTimeout(t);
      document.removeEventListener("mousedown", onPointer);
      document.removeEventListener("keydown", onKey);
      window.removeEventListener("resize", onClose);
    };
  }, [onClose]);

  useEffect(() => {
    if (state === "ready") ref.current?.querySelector<HTMLElement>("[data-first]")?.focus();
  }, [state]);

  const width = 288;
  // To the right of the row; on a narrow screen, over the sidebar instead.
  const left = anchor.right + 8 + width <= window.innerWidth ? anchor.right + 8 : Math.max(8, window.innerWidth - width - 8);
  const top = Math.max(8, Math.min(anchor.top - 8, window.innerHeight - 360));

  return createPortal(
    <div
      ref={ref}
      role="menu"
      aria-label={`${subjectName} resources`}
      style={{ top, left, width }}
      className="fixed z-[70] flex max-h-[min(22rem,calc(100dvh-1rem))] flex-col overflow-hidden rounded-2xl border border-gray-200 bg-white text-gray-700 shadow-2xl"
    >
      <p className="border-b border-gray-100 px-4 py-2.5 text-xs font-semibold uppercase tracking-wide text-gray-400">
        {subjectName} · Resources
      </p>
      <div className="min-h-0 flex-1 overflow-y-auto p-1.5">
        {state === "loading" || state === "idle" ? (
          <p role="status" className="px-3 py-3 text-sm text-gray-500">Loading…</p>
        ) : state === "error" ? (
          <p role="alert" className="px-3 py-3 text-sm text-red-600">Could not load: {error}</p>
        ) : resources.length === 0 ? (
          <p className="px-3 py-3 text-sm text-gray-500">No resources in this subject yet.</p>
        ) : (
          resources.map((r, i) => {
            const openable = toSelected(r) !== null;
            return (
              <button
                key={r.id}
                type="button"
                role="menuitem"
                data-first={i === 0 ? true : undefined}
                disabled={!openable}
                title={openable ? `Open ${resourceKindLabel(r)}` : "The original file is unavailable"}
                onClick={() => onOpen(r.id)}
                className="flex w-full items-center gap-3 rounded-xl px-2.5 py-2 text-left transition hover:bg-violet-50 focus:bg-violet-50 focus:outline-none disabled:cursor-not-allowed disabled:opacity-50"
              >
                <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-gray-100 text-sm" aria-hidden="true">
                  {resourceIcon(r)}
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-sm font-medium text-gray-900">{r.title ?? r.id}</span>
                  <span className="block text-[11px] text-gray-500">{resourceKindLabel(r)}</span>
                </span>
              </button>
            );
          })
        )}
      </div>
    </div>,
    document.body,
  );
}

function SectionHeader({ label, open, onToggle }: { label: string; open: boolean; onToggle: () => void }) {
  return (
    <button
      type="button"
      onClick={onToggle}
      aria-expanded={open}
      className="flex w-full items-center justify-between rounded-lg px-1 py-1 text-[15px] font-semibold text-white transition hover:text-violet-200 focus:outline-none focus-visible:ring-2 focus-visible:ring-violet-400"
    >
      {label}
      <Icon d={open ? "M18 15l-6-6-6 6" : "M6 9l6 6 6-6"} className="h-4 w-4 text-gray-400" />
    </button>
  );
}

export default function MalaiSidebar({ collapsed, onToggleCollapse, onBack, onNavigate, inDrawer = false }: MalaiSidebarProps) {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const activeId = params.get("c");
  // The subject whose page, or one of whose resources, is open.
  const lessonMatch = pathname.match(/^\/malai\/lesson\/([^/]+)\//);
  const pageSubject = lessonMatch ? decodeURIComponent(lessonMatch[1]) : params.get("s");
  const [flyout, setFlyout] = useState<{ subjectId: string; anchor: DOMRect } | null>(null);
  const chats = useMalaiChats();
  const { subjects } = useSubjects();
  const [query, setQuery] = useState("");
  const [subjectsOpen, setSubjectsOpen] = useState(true);
  const [recentOpen, setRecentOpen] = useState(true);
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});

  const q = query.trim().toLowerCase();
  const matches = (c: MalaiChat) =>
    !q || c.title.toLowerCase().includes(q) || c.preview.toLowerCase().includes(q);

  const subjectIds = useMemo(() => new Set(subjects.map((s) => s.id)), [subjects]);
  // A chat whose subject was deleted falls back to Recent chats.
  const filed = (c: MalaiChat) => c.subjectId !== null && subjectIds.has(c.subjectId);
  const recent = chats.filter((c) => !filed(c) && matches(c));

  // Opening a chat inside a subject, or the subject itself, reveals it.
  const activeSubject = chats.find((c) => c.id === activeId)?.subjectId ?? pageSubject;
  useEffect(() => {
    if (activeSubject) setExpanded((e) => (e[activeSubject] ? e : { ...e, [activeSubject]: true }));
  }, [activeSubject]);

  const go = (href: string) => {
    setFlyout(null);
    router.push(href);
    onNavigate?.();
  };
  const open = (chat: MalaiChat) => go(chatPath(chat));
  const newChat = () => {
    router.push(MALAI_PATH);
    onNavigate?.();
  };

  const closeFlyout = useCallback(() => setFlyout(null), []);

  const iconButton =
    "flex h-9 w-9 shrink-0 items-center justify-center rounded-xl transition focus:outline-none focus-visible:ring-2 focus-visible:ring-violet-400";

  if (collapsed) {
    return (
      <div className="flex flex-col items-center gap-2 px-2 py-5">
        <button type="button" onClick={onToggleCollapse} aria-label="Expand sidebar" title="Expand sidebar" className={`${iconButton} text-gray-300 hover:bg-white/10 hover:text-white`}>
          <Icon d={EXPAND_ICON} />
        </button>
        <button type="button" onClick={newChat} aria-label="New chat" title="New chat" className={`${iconButton} bg-violet-600 text-white hover:bg-violet-500`}>
          <Icon d={NEW_CHAT_ICON} className="h-[18px] w-[18px]" />
        </button>
        <button type="button" onClick={onBack} aria-label="Back to main sidebar" title="Back to main sidebar" className={`${iconButton} text-gray-300 hover:bg-white/10 hover:text-white`}>
          <Icon d={BACK_ICON} />
        </button>
      </div>
    );
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="px-5 pb-4 pt-5">
        <div className="flex items-center justify-between gap-2">
          <button
            type="button"
            onClick={onBack}
            data-drawer-autofocus
            className="-ml-1 inline-flex items-center gap-2 rounded-lg px-1 py-1 text-sm text-gray-200 transition hover:text-white focus:outline-none focus-visible:ring-2 focus-visible:ring-violet-400"
          >
            <Icon d={BACK_ICON} className="h-4 w-4" />
            Back to main sidebar
          </button>
          <div className="flex items-center gap-1.5">
            <button type="button" onClick={newChat} aria-label="New chat" title="New chat" className={`${iconButton} bg-violet-600 text-white shadow-sm shadow-violet-900/40 hover:bg-violet-500`}>
              <Icon d={NEW_CHAT_ICON} className="h-[18px] w-[18px]" />
            </button>
            {!inDrawer && (
              <button type="button" onClick={onToggleCollapse} aria-label="Collapse sidebar" title="Collapse sidebar" className={`${iconButton} text-gray-400 hover:bg-white/10 hover:text-white`}>
                <Icon d={COLLAPSE_ICON} className="h-4 w-4" />
              </button>
            )}
          </div>
        </div>
        <p className="mt-3 text-xl font-bold text-white">Malai</p>
        <p className="text-sm text-gray-400">Your learning companion</p>
        <label className="relative mt-4 block">
          <span className="sr-only">Search conversations</span>
          <Icon d="M11 18a7 7 0 100-14 7 7 0 000 14zM20 20l-3.5-3.5" className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-400" />
          <input
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search conversations…"
            className="w-full rounded-xl border border-white/10 bg-white/5 py-2.5 pl-9 pr-3 text-sm text-white placeholder-gray-400 outline-none transition focus:border-violet-400 focus:ring-2 focus:ring-violet-500/30"
          />
        </label>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-3 pb-6">
        <section className="px-2">
          <SectionHeader label="Subjects" open={subjectsOpen} onToggle={() => setSubjectsOpen((v) => !v)} />
          {subjectsOpen && (
            <ul className="mt-2 flex flex-col gap-1">
              {subjects.map((s) => {
                const own = chats.filter((c) => c.subjectId === s.id && matches(c));
                if (q && own.length === 0) return null;
                const isOpen = expanded[s.id] || (q.length > 0 && own.length > 0);
                return (
                  <li key={s.id}>
                    <div
                      className={`flex items-center rounded-xl transition ${
                        pageSubject === s.id && !activeId ? "bg-violet-600/25 ring-1 ring-violet-500/40" : "hover:bg-white/5"
                      }`}
                    >
                      <button
                        type="button"
                        onClick={() => {
                          setExpanded((e) => ({ ...e, [s.id]: true }));
                          go(malaiSubjectPath(s.id));
                        }}
                        aria-current={pageSubject === s.id && !activeId ? "page" : undefined}
                        className="flex min-w-0 flex-1 items-center gap-3 rounded-xl py-1.5 pl-1 text-left focus:outline-none focus-visible:ring-2 focus-visible:ring-violet-400"
                      >
                        <span className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-lg text-xs font-bold text-white ${subjectAccent(s.name).avatar}`}>
                          {initials(s.name)}
                        </span>
                        <span className="min-w-0 flex-1 truncate text-sm text-white">{s.name}</span>
                        <span className="text-xs tabular-nums text-gray-400">{own.length}</span>
                      </button>
                      <button
                        type="button"
                        onClick={() => setExpanded((e) => ({ ...e, [s.id]: !isOpen }))}
                        aria-expanded={isOpen}
                        aria-label={`${isOpen ? "Hide" : "Show"} ${s.name}'s resources and chats`}
                        className="mx-1 rounded-lg p-1.5 text-gray-400 transition hover:bg-white/10 hover:text-white focus:outline-none focus-visible:ring-2 focus-visible:ring-violet-400"
                      >
                        <Icon d={isOpen ? "M18 15l-6-6-6 6" : "M6 9l6 6 6-6"} className="h-4 w-4" />
                      </button>
                    </div>
                    {isOpen && (
                      <ul className="mb-1 ml-[1.375rem] mt-1 flex flex-col gap-0.5 border-l border-white/10 pl-2">
                        <li>
                          <button
                            type="button"
                            aria-haspopup="menu"
                            data-flyout-trigger
                            aria-expanded={flyout?.subjectId === s.id}
                            onClick={(e) => {
                              const anchor = e.currentTarget.getBoundingClientRect();
                              setFlyout((f) => (f?.subjectId === s.id ? null : { subjectId: s.id, anchor }));
                            }}
                            className={`flex w-full items-center gap-2.5 rounded-xl px-3 py-2 text-left text-[13px] transition focus:outline-none focus-visible:ring-2 focus-visible:ring-violet-400 ${
                              flyout?.subjectId === s.id ? "bg-white/10 text-white" : "text-gray-200 hover:bg-white/5"
                            }`}
                          >
                            <Icon d="M3 7a2 2 0 012-2h4l2 2h8a2 2 0 012 2v8a2 2 0 01-2 2H5a2 2 0 01-2-2z" className="h-4 w-4 text-gray-400" />
                            <span className="flex-1">Resources</span>
                            <Icon d={EXPAND_ICON} className="h-4 w-4 text-gray-400" />
                          </button>
                        </li>
                        {own.map((c) => (
                          <ChatRow key={c.id} chat={c} compact active={c.id === activeId} subjects={subjects} onOpen={open} onDeletedActive={newChat} />
                        ))}
                        {own.length === 0 && (
                          <li className="px-3 py-1.5 text-xs text-gray-500">No chats yet. Open a resource or the subject to start one.</li>
                        )}
                      </ul>
                    )}
                  </li>
                );
              })}
              {subjects.length === 0 && <li className="px-1 py-1 text-xs text-gray-500">No subjects yet.</li>}
            </ul>
          )}
          {flyout && (
            <ResourcesFlyout
              key={flyout.subjectId}
              subjectId={flyout.subjectId}
              subjectName={subjects.find((x) => x.id === flyout.subjectId)?.name ?? "Subject"}
              anchor={flyout.anchor}
              onClose={closeFlyout}
              onOpen={(rid) => go(malaiLessonPath(flyout.subjectId, rid))}
            />
          )}
        </section>

        <div className="mx-2 my-4 border-t border-white/10" />

        <section className="px-2">
          <SectionHeader label="Recent chats" open={recentOpen} onToggle={() => setRecentOpen((v) => !v)} />
          {recentOpen && (
            <ul className="-mx-1 mt-2 flex flex-col gap-1">
              {recent.map((c) => (
                <ChatRow key={c.id} chat={c} active={c.id === activeId} subjects={subjects} onOpen={open} onDeletedActive={newChat} />
              ))}
              {recent.length === 0 && (
                <li className="px-2 py-1 text-xs text-gray-500">{q ? "No chats match your search." : "No chats yet. Ask Malai anything to start one."}</li>
              )}
            </ul>
          )}
        </section>
      </div>
    </div>
  );
}
