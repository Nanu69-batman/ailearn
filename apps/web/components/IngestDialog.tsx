"use client";

import { useEffect, useRef, type ReactNode } from "react";
import type { IngestSource } from "./IngestProgress";

interface IngestDialogProps {
  open: boolean;
  onClose: () => void;
  tab: IngestSource;
  onTabChange: (tab: IngestSource) => void;
  /** Which sources have a job in flight (drives the tab spinners + note). */
  busy: Record<IngestSource, boolean>;
  youtube: ReactNode;
  file: ReactNode;
}

const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

const TABS: Array<{ id: IngestSource; label: string; icon: ReactNode }> = [
  {
    id: "youtube",
    label: "YouTube link",
    icon: (
      <svg viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
        <rect x="2.5" y="5" width="19" height="14" rx="4" />
        <path d="m10 9.5 5 2.5-5 2.5z" fill="currentColor" />
      </svg>
    ),
  },
  {
    id: "file",
    label: "Upload file",
    icon: (
      <svg viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
        <path d="M12 16V4" />
        <path d="m7 9 5-5 5 5" />
        <path d="M20 16v3a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1v-3" />
      </svg>
    ),
  },
];

/**
 * "Add content" modal with two tabs. Both panels stay MOUNTED for the life of
 * the page — closed or on the other tab, they are only hidden — so an upload
 * that is mid-flight keeps polling after the learner closes the dialog. The
 * subject page mirrors that job as a pending card in the content list.
 */
export default function IngestDialog({
  open,
  onClose,
  tab,
  onTabChange,
  busy,
  youtube,
  file,
}: IngestDialogProps) {
  const panelRef = useRef<HTMLDivElement>(null);
  const returnFocusRef = useRef<HTMLElement | null>(null);
  const anyBusy = busy.youtube || busy.file;

  // Focus the active tab's first field on open; give focus back on close.
  useEffect(() => {
    if (!open) return;
    returnFocusRef.current = document.activeElement as HTMLElement | null;
    const id = window.requestAnimationFrame(() => {
      const panel = panelRef.current?.querySelector<HTMLElement>(
        `[data-ingest-panel="${tab}"]`,
      );
      const target =
        panel?.querySelector<HTMLElement>("input:not([disabled])") ??
        panelRef.current?.querySelector<HTMLElement>("[data-dialog-close]");
      target?.focus();
    });
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      window.cancelAnimationFrame(id);
      document.body.style.overflow = prevOverflow;
      returnFocusRef.current?.focus?.();
    };
    // Only on open/close — switching tabs must not steal focus back.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  useEffect(() => {
    if (!open) return;
    function onKey(e: KeyboardEvent): void {
      if (e.key === "Escape") {
        e.preventDefault();
        onClose();
        return;
      }
      if (e.key !== "Tab" || !panelRef.current) return;
      const nodes = Array.from(
        panelRef.current.querySelectorAll<HTMLElement>(FOCUSABLE),
      ).filter((n) => n.offsetParent !== null || n === document.activeElement);
      if (nodes.length === 0) return;
      const first = nodes[0];
      const last = nodes[nodes.length - 1];
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  return (
    <div
      className={open ? "fixed inset-0 z-50 flex items-end justify-center p-0 sm:items-center sm:p-4" : "hidden"}
      aria-hidden={open ? undefined : true}
    >
      <div
        className="absolute inset-0 bg-gray-900/40 backdrop-blur-[2px]"
        onClick={onClose}
        aria-hidden="true"
      />
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="ingest-dialog-title"
        className="relative flex max-h-[90vh] w-full max-w-lg flex-col overflow-hidden rounded-t-2xl border border-gray-200 bg-white shadow-xl sm:rounded-2xl"
      >
        <div className="flex items-start justify-between gap-4 px-6 pb-4 pt-5">
          <div>
            <h2 id="ingest-dialog-title" className="text-lg font-semibold text-gray-900">
              Add content
            </h2>
            <p className="mt-0.5 text-sm text-gray-500">
              Malai indexes it so you can ask questions with cited answers.
            </p>
          </div>
          <button
            type="button"
            data-dialog-close
            onClick={onClose}
            className="-mr-2 rounded-lg p-2 text-gray-400 transition hover:bg-gray-100 hover:text-gray-600 focus:outline-none focus-visible:ring-2 focus-visible:ring-violet-300"
            aria-label="Close"
          >
            <svg viewBox="0 0 24 24" className="h-5 w-5" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true">
              <path d="M6 6l12 12M18 6 6 18" />
            </svg>
          </button>
        </div>

        <div className="px-6">
          <div role="tablist" aria-label="Content source" className="grid grid-cols-2 gap-1 rounded-xl bg-gray-100 p-1">
            {TABS.map((t) => {
              const selected = tab === t.id;
              return (
                <button
                  key={t.id}
                  type="button"
                  role="tab"
                  id={`ingest-tab-${t.id}`}
                  aria-selected={selected}
                  aria-controls={`ingest-panel-${t.id}`}
                  onClick={() => onTabChange(t.id)}
                  className={`flex items-center justify-center gap-2 rounded-lg px-3 py-2 text-sm font-medium transition focus:outline-none focus-visible:ring-2 focus-visible:ring-violet-300 ${
                    selected
                      ? "bg-white text-violet-700 shadow-sm"
                      : "text-gray-500 hover:text-gray-700"
                  }`}
                >
                  {t.icon}
                  {t.label}
                  {busy[t.id] && (
                    <span className="inline-block h-3 w-3 animate-spin rounded-full border-2 border-violet-400 border-t-transparent" aria-label="in progress" />
                  )}
                </button>
              );
            })}
          </div>
        </div>

        <div className="overflow-y-auto px-6 py-5">
          <div
            role="tabpanel"
            id="ingest-panel-youtube"
            aria-labelledby="ingest-tab-youtube"
            data-ingest-panel="youtube"
            hidden={tab !== "youtube"}
          >
            <p className="mb-3 text-sm text-gray-600">
              Paste a YouTube link — we fetch the transcript and make it askable.
            </p>
            {youtube}
          </div>
          <div
            role="tabpanel"
            id="ingest-panel-file"
            aria-labelledby="ingest-tab-file"
            data-ingest-panel="file"
            hidden={tab !== "file"}
          >
            {file}
          </div>
        </div>

        <div className="flex items-center justify-between gap-3 border-t border-gray-100 bg-gray-50 px-6 py-3">
          <p className="text-xs text-gray-500" role={anyBusy ? "status" : undefined}>
            {anyBusy
              ? "You can close this — processing keeps running and shows in your content list."
              : "New content appears in this subject automatically."}
          </p>
          <button
            type="button"
            onClick={onClose}
            className="shrink-0 rounded-xl border border-gray-200 bg-white px-4 py-1.5 text-sm font-medium text-gray-700 transition hover:bg-gray-50"
          >
            {anyBusy ? "Hide" : "Done"}
          </button>
        </div>
      </div>
    </div>
  );
}
