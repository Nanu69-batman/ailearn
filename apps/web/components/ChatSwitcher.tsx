"use client";

import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useRouter } from "next/navigation";
import { chatDate, chatPath, type MalaiChat } from "@/lib/malai";

/**
 * The split view's chat dropdown: jump to another of this subject's chats, or
 * start a new one. It lists only chats already in Malai's sidebar; renaming,
 * moving and deleting stay in the sidebar.
 *
 * The menu is portalled to <body> with fixed positioning, so the window it
 * sits in (which clips its overflow) cannot cut it off.
 */
export default function ChatSwitcher({
  chats,
  currentId,
  currentResourceId,
  newChatHref,
  className = "",
}: {
  /** The subject's chats, as listed in the sidebar (most recent first). */
  chats: MalaiChat[];
  currentId: string | null;
  /** The open resource: its own chats are marked. */
  currentResourceId: string;
  newChatHref: string;
  className?: string;
}) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const btnRef = useRef<HTMLButtonElement | null>(null);
  const menuRef = useRef<HTMLDivElement | null>(null);
  const [pos, setPos] = useState<{ top: number; left: number; width: number } | null>(null);
  const current = chats.find((c) => c.id === currentId) ?? null;

  useLayoutEffect(() => {
    if (!open) return;
    const place = () => {
      const r = btnRef.current?.getBoundingClientRect();
      if (!r) return;
      const width = Math.min(320, window.innerWidth - 16);
      setPos({ top: r.bottom + 6, left: Math.max(8, Math.min(r.left, window.innerWidth - width - 8)), width });
    };
    place();
    window.addEventListener("resize", place);
    window.addEventListener("scroll", place, true);
    return () => {
      window.removeEventListener("resize", place);
      window.removeEventListener("scroll", place, true);
    };
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      const t = e.target as Node;
      if (!btnRef.current?.contains(t) && !menuRef.current?.contains(t)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setOpen(false);
        btnRef.current?.focus();
      }
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const go = (href: string) => {
    setOpen(false);
    router.push(href);
  };

  const item =
    "flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-sm transition hover:bg-violet-50 focus:bg-violet-50 focus:outline-none";

  return (
    <>
      <button
        ref={btnRef}
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-haspopup="listbox"
        aria-expanded={open}
        title="Switch chat"
        className={`flex min-w-0 items-center gap-1.5 rounded-lg px-2 py-1.5 text-sm font-medium text-gray-700 transition hover:bg-gray-100 focus:outline-none focus-visible:ring-2 focus-visible:ring-violet-400 ${className}`}
      >
        <span className="truncate">{current ? current.title || "Untitled chat" : "New chat"}</span>
        <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className={`h-4 w-4 shrink-0 text-gray-400 transition ${open ? "rotate-180" : ""}`} aria-hidden="true">
          <path d="M5 8l5 5 5-5" />
        </svg>
      </button>
      {open &&
        pos &&
        createPortal(
          <div
            ref={menuRef}
            role="listbox"
            aria-label="Chats in this subject"
            className="fixed z-[80] max-h-[min(24rem,60vh)] overflow-y-auto rounded-xl border border-gray-200 bg-white p-1.5 shadow-xl shadow-gray-900/10"
            style={{ top: pos.top, left: pos.left, width: pos.width }}
          >
            <button type="button" role="option" aria-selected={currentId === null} onClick={() => go(newChatHref)} className={`${item} font-medium text-violet-700`}>
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" className="h-4 w-4 shrink-0" aria-hidden="true">
                <path d="M12 5v14M5 12h14" />
              </svg>
              New chat
            </button>
            {chats.length > 0 && <div className="my-1 border-t border-gray-100" />}
            {chats.map((c) => (
              <button
                key={c.id}
                type="button"
                role="option"
                aria-selected={c.id === currentId}
                onClick={() => (c.id === currentId ? setOpen(false) : go(chatPath(c)))}
                className={`${item} ${c.id === currentId ? "bg-violet-50" : ""}`}
              >
                <span className="min-w-0 flex-1">
                  <span className={`block truncate ${c.id === currentId ? "font-semibold text-violet-800" : "text-gray-800"}`}>
                    {c.title || "Untitled chat"}
                  </span>
                  <span className="block truncate text-[11px] text-gray-400">
                    {c.resourceId === currentResourceId ? "This resource" : c.resourceId ? "Another resource" : "Subject chat"} ·{" "}
                    {chatDate(c.updatedAt)}
                  </span>
                </span>
              </button>
            ))}
          </div>,
          document.body,
        )}
    </>
  );
}
