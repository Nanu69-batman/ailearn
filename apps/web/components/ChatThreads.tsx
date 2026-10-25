"use client";

import { useEffect, useRef, useState } from "react";
import { MAIN_THREAD_ID, type ChatThread } from "../lib/api";

/**
 * Chat switcher for the tutor panel: the current chat's name, a menu of the
 * session's chats (rename / delete inline), and "New chat".
 *
 * Purely presentational. ChatPanel owns the list, the selection and every API
 * call; this only renders them and reports intents. Deletion asks for an
 * inline confirmation rather than `window.confirm`, which blocks the page.
 */

interface ChatThreadsProps {
  threads: ChatThread[];
  currentId: string;
  disabled?: boolean;
  busy?: boolean;
  onSelect: (id: string) => void;
  onCreate: () => void;
  onRename: (id: string, title: string) => void;
  onDelete: (id: string) => void;
  /** Classes for the root row (spacing/flex in the parent). */
  className?: string;
}

export function threadLabel(t: ChatThread | undefined): string {
  if (!t) return "New chat";
  if (t.title) return t.title;
  return t.message_count === 0 ? "New chat" : "Untitled chat";
}

function ago(iso: string): string {
  const ms = Date.now() - new Date(iso).getTime();
  if (!Number.isFinite(ms) || ms < 60_000) return "just now";
  const m = Math.floor(ms / 60_000);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}

function Row({
  thread,
  current,
  disabled,
  onSelect,
  onRename,
  onDelete,
}: {
  thread: ChatThread;
  current: boolean;
  disabled?: boolean;
  onSelect: () => void;
  onRename: (title: string) => void;
  onDelete: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [draft, setDraft] = useState(thread.title ?? "");
  const inputRef = useRef<HTMLInputElement | null>(null);
  const isMain = thread.id === MAIN_THREAD_ID;

  useEffect(() => {
    if (editing) inputRef.current?.select();
  }, [editing]);

  if (editing) {
    return (
      <li className="px-2 py-1.5">
        <form
          className="flex items-center gap-1.5"
          onSubmit={(e) => {
            e.preventDefault();
            onRename(draft);
            setEditing(false);
          }}
        >
          <input
            ref={inputRef}
            value={draft}
            maxLength={80}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Escape") setEditing(false);
            }}
            aria-label="Chat name"
            className="min-w-0 flex-1 rounded-lg border border-violet-300 px-2 py-1 text-sm focus:outline-none focus:ring-2 focus:ring-violet-200"
          />
          <button type="submit" className="rounded-lg bg-violet-600 px-2 py-1 text-xs font-medium text-white hover:bg-violet-700">
            Save
          </button>
        </form>
      </li>
    );
  }

  if (confirming) {
    return (
      <li className="flex items-center gap-2 bg-red-50 px-3 py-2 text-xs text-red-700">
        <span className="min-w-0 flex-1">
          {isMain ? "Clear this chat's messages?" : "Delete this chat?"}
        </span>
        <button
          type="button"
          onClick={() => {
            onDelete();
            setConfirming(false);
          }}
          className="rounded-md bg-red-600 px-2 py-0.5 font-medium text-white hover:bg-red-700"
        >
          {isMain ? "Clear" : "Delete"}
        </button>
        <button type="button" onClick={() => setConfirming(false)} className="rounded-md px-2 py-0.5 hover:bg-red-100">
          Cancel
        </button>
      </li>
    );
  }

  return (
    <li className={`group flex items-center gap-1 px-1.5 ${current ? "bg-violet-50" : "hover:bg-gray-50"}`}>
      <button
        type="button"
        onClick={onSelect}
        disabled={disabled}
        aria-current={current ? "true" : undefined}
        className="min-w-0 flex-1 rounded-lg px-1.5 py-2 text-left disabled:cursor-not-allowed"
      >
        <span className={`block truncate text-sm ${current ? "font-semibold text-violet-800" : "text-gray-800"}`}>
          {threadLabel(thread)}
        </span>
        <span className="block text-[11px] text-gray-400">
          {thread.message_count === 0
            ? "No messages yet"
            : `${Math.ceil(thread.message_count / 2)} question${thread.message_count > 2 ? "s" : ""} · ${ago(thread.updated_at)}`}
        </span>
      </button>
      <button
        type="button"
        onClick={() => {
          setDraft(thread.title ?? "");
          setEditing(true);
        }}
        aria-label={`Rename ${threadLabel(thread)}`}
        title="Rename"
        className="rounded-md p-1.5 text-gray-400 opacity-60 transition hover:bg-white hover:text-violet-700 group-hover:opacity-100"
      >
        <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" className="h-3.5 w-3.5" aria-hidden="true">
          <path d="M13.5 3.5l3 3L7 16H4v-3l9.5-9.5Z" />
        </svg>
      </button>
      <button
        type="button"
        onClick={() => setConfirming(true)}
        disabled={disabled || (isMain && thread.message_count === 0)}
        aria-label={isMain ? "Clear this chat" : `Delete ${threadLabel(thread)}`}
        title={isMain ? "Clear" : "Delete"}
        className="rounded-md p-1.5 text-gray-400 opacity-60 transition hover:bg-white hover:text-red-600 group-hover:opacity-100 disabled:cursor-not-allowed disabled:opacity-30"
      >
        <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" className="h-3.5 w-3.5" aria-hidden="true">
          <path d="M4 6h12M8 6V4h4v2M6 6l1 10h6l1-10" />
        </svg>
      </button>
    </li>
  );
}

export default function ChatThreads({
  threads,
  currentId,
  disabled,
  busy,
  onSelect,
  onCreate,
  onRename,
  onDelete,
  className = "mb-3",
}: ChatThreadsProps) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement | null>(null);
  const current = threads.find((t) => t.id === currentId);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent): void => {
      if (!rootRef.current?.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent): void => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <div ref={rootRef} className={`relative flex items-center gap-2 ${className}`}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-haspopup="true"
        aria-expanded={open}
        className="flex min-w-0 flex-1 items-center gap-2 rounded-xl border border-gray-200 bg-gray-50 px-3 py-1.5 text-left text-sm text-gray-800 transition hover:border-violet-300 hover:bg-white"
      >
        <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" className="h-4 w-4 shrink-0 text-violet-500" aria-hidden="true">
          <path d="M16.5 10a6.5 6.5 0 0 1-9.4 5.8L3.5 16.5l.7-3.6A6.5 6.5 0 1 1 16.5 10Z" />
        </svg>
        <span className="min-w-0 flex-1 truncate font-medium">{threadLabel(current)}</span>
        {threads.length > 1 && (
          <span className="shrink-0 rounded-full bg-gray-200/80 px-1.5 text-[10px] font-semibold text-gray-600">
            {threads.length}
          </span>
        )}
        <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className={`h-4 w-4 shrink-0 text-gray-400 transition ${open ? "rotate-180" : ""}`} aria-hidden="true">
          <path d="m5.5 7.5 4.5 4.5 4.5-4.5" />
        </svg>
      </button>
      <button
        type="button"
        onClick={onCreate}
        disabled={disabled || busy}
        title="Start a new chat"
        className="flex shrink-0 items-center gap-1 rounded-xl bg-violet-600 px-3 py-1.5 text-sm font-medium text-white shadow-sm transition hover:bg-violet-700 disabled:cursor-not-allowed disabled:opacity-50"
      >
        <span aria-hidden="true" className="text-base leading-none">+</span>
        New chat
      </button>

      {open && (
        <div className="absolute inset-x-0 top-full z-30 mt-1.5 overflow-hidden rounded-xl border border-gray-200 bg-white shadow-lg">
          <p className="border-b border-gray-100 px-3 py-2 text-[10px] font-semibold uppercase tracking-wider text-gray-400">
            Your chats
          </p>
          <ul className="max-h-72 overflow-y-auto py-1">
            {threads.map((t) => (
              <Row
                key={t.id}
                thread={t}
                current={t.id === currentId}
                disabled={disabled}
                onSelect={() => {
                  onSelect(t.id);
                  setOpen(false);
                }}
                onRename={(title) => onRename(t.id, title)}
                onDelete={() => onDelete(t.id)}
              />
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
