"use client";

import { useEffect, useId, useRef, type ReactNode } from "react";
import { createPortal } from "react-dom";

/**
 * Minimal accessible modal used by the subject dialogs.
 *
 * - role="dialog" + aria-modal, labelled by its title
 * - Escape and backdrop click close it (unless `busy`)
 * - focus moves to `[data-autofocus]` (or the panel) on open, Tab is trapped
 *   inside, and focus returns to the opener on close
 */
export interface SubjectModalProps {
  open: boolean;
  title: string;
  description?: ReactNode;
  onClose: () => void;
  /** While true the dialog cannot be dismissed (a request is in flight). */
  busy?: boolean;
  /** Visual tone of the header icon. */
  tone?: "violet" | "red";
  icon?: ReactNode;
  children: ReactNode;
}

const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), textarea:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])';

export default function SubjectModal({
  open,
  title,
  description,
  onClose,
  busy = false,
  tone = "violet",
  icon,
  children,
}: SubjectModalProps) {
  const panelRef = useRef<HTMLDivElement | null>(null);
  const titleId = useId();
  const descId = useId();
  // Latest values without re-running the open/close effect on every render.
  const onCloseRef = useRef(onClose);
  const busyRef = useRef(busy);
  useEffect(() => {
    onCloseRef.current = onClose;
    busyRef.current = busy;
  }, [onClose, busy]);

  useEffect(() => {
    if (!open) return;
    const opener = document.activeElement as HTMLElement | null;
    const panel = panelRef.current;
    const target =
      panel?.querySelector<HTMLElement>("[data-autofocus]") ?? panel;
    // Next frame so the element is painted before it is focused.
    const raf = window.requestAnimationFrame(() => target?.focus());

    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";

    function onKeyDown(event: KeyboardEvent): void {
      if (event.key === "Escape") {
        event.preventDefault();
        if (!busyRef.current) onCloseRef.current();
        return;
      }
      if (event.key !== "Tab" || !panel) return;
      const focusable = Array.from(
        panel.querySelectorAll<HTMLElement>(FOCUSABLE),
      ).filter((el) => el.offsetParent !== null);
      if (focusable.length === 0) {
        event.preventDefault();
        panel.focus();
        return;
      }
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      const active = document.activeElement;
      if (event.shiftKey && (active === first || !panel.contains(active))) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && active === last) {
        event.preventDefault();
        first.focus();
      }
    }
    document.addEventListener("keydown", onKeyDown);

    return () => {
      window.cancelAnimationFrame(raf);
      document.removeEventListener("keydown", onKeyDown);
      document.body.style.overflow = previousOverflow;
      if (opener && document.contains(opener)) opener.focus();
    };
  }, [open]);

  if (!open) return null;

  const iconTone =
    tone === "red"
      ? "bg-red-50 text-red-600 ring-red-100"
      : "bg-violet-50 text-violet-600 ring-violet-100";

  // Portalled to <body>: opened from the Malai sidebar, it would otherwise sit
  // in the sidebar's layer, under the page beside it.
  return createPortal(
    <div className="fixed inset-0 z-[80] flex items-end justify-center p-4 sm:items-center">
      <div
        aria-hidden="true"
        className="subject-modal-fade absolute inset-0 bg-gray-900/40 backdrop-blur-[2px]"
        onClick={() => {
          if (!busy) onClose();
        }}
      />
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={description ? descId : undefined}
        tabIndex={-1}
        className="subject-modal-pop relative w-full max-w-md rounded-2xl border border-gray-200 bg-white p-6 shadow-xl outline-none"
      >
        <div className="flex items-start gap-4">
          {icon && (
            <span
              className={`flex h-11 w-11 shrink-0 items-center justify-center rounded-xl ring-4 ${iconTone}`}
              aria-hidden="true"
            >
              {icon}
            </span>
          )}
          <div className="min-w-0 flex-1">
            <h2 id={titleId} className="text-lg font-semibold text-gray-900">
              {title}
            </h2>
            {description && (
              <div id={descId} className="mt-1 text-sm leading-relaxed text-gray-500">
                {description}
              </div>
            )}
          </div>
          <button
            type="button"
            onClick={onClose}
            disabled={busy}
            aria-label="Close dialog"
            className="-m-1 rounded-lg p-1.5 text-gray-400 transition hover:bg-gray-100 hover:text-gray-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-violet-500 disabled:opacity-40"
          >
            <svg
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              className="h-5 w-5"
              aria-hidden="true"
            >
              <path d="M18 6L6 18M6 6l12 12" />
            </svg>
          </button>
        </div>
        <div className="mt-5">{children}</div>
      </div>
      {/* Scoped keyframes: no global CSS or Tailwind config changes needed. */}
      <style>{`
        @keyframes subject-modal-fade { from { opacity: 0 } to { opacity: 1 } }
        @keyframes subject-modal-pop {
          from { opacity: 0; transform: translateY(8px) scale(.98) }
          to { opacity: 1; transform: none }
        }
        .subject-modal-fade { animation: subject-modal-fade .15s ease-out }
        .subject-modal-pop { animation: subject-modal-pop .18s ease-out }
        @media (prefers-reduced-motion: reduce) {
          .subject-modal-fade, .subject-modal-pop { animation: none }
        }
      `}</style>
    </div>,
    document.body,
  );
}
