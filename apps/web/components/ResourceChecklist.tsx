"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";
import Link from "next/link";
import type { CourseResource } from "@/lib/api";
import { resourceIcon, resourceKindLabel, toSelected } from "@/lib/learn";
import {
  effectiveAllowance,
  setChatAllowed,
  setSubjectCompletion,
  resetChatAllowance,
  useChatAllowances,
  useSubjectCompletion,
  useWatchProgress,
  watchFraction,
  type WatchProgress,
} from "@/lib/progress";
import { formatClock } from "@/components/PlayerControls";
import SubjectModal from "@/components/SubjectModal";

/**
 * Resource checklists:
 *
 * - SubjectChecklist: a subject's resources, ticked off for the whole subject
 *   (every chat in it follows). Ticking asks for confirmation first.
 * - ResourceChooser: what one chat may read, beside its input bar. It starts
 *   from the subject's ticks; changes here stay in this chat unless the
 *   learner says they are for the whole subject.
 */

export function CircleCheck({ checked, className = "" }: { checked: boolean; className?: string }) {
  return (
    <span
      aria-hidden="true"
      className={`flex h-5 w-5 shrink-0 items-center justify-center rounded-full border-2 transition ${
        checked ? "border-emerald-500 bg-emerald-500 text-white" : "border-gray-300 bg-white text-transparent"
      } ${className}`}
    >
      <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" className="h-3 w-3">
        <path d="M5 10.5l3.2 3L15 7" />
      </svg>
    </span>
  );
}

/** "Watched 4:32 of 10:55" for a video with progress; null otherwise. */
export function watchLabel(w: WatchProgress | undefined): string | null {
  if (!w || w.ms <= 0) return null;
  const f = watchFraction(w);
  if (f === null) return `Watched to ${formatClock(w.ms / 1000)}`;
  if (f >= 1) return "Watched to the end";
  return `Watched ${formatClock(w.ms / 1000)} of ${formatClock(w.durationMs / 1000)}`;
}

function WatchBar({ w }: { w: WatchProgress | undefined }) {
  const f = watchFraction(w);
  if (f === null || f <= 0) return null;
  return (
    <span className="mt-1 block h-1 w-full max-w-[10rem] overflow-hidden rounded-full bg-gray-100" aria-hidden="true">
      <span className="block h-full rounded-full bg-violet-500" style={{ width: `${Math.round(f * 100)}%` }} />
    </span>
  );
}

function isVideo(r: CourseResource): boolean {
  return r.type === "video" || r.provider === "youtube";
}

// ---------------------------------------------------------------------------
// Subject checklist
// ---------------------------------------------------------------------------

export function SubjectChecklist({
  subjectId,
  subjectName,
  resources,
  openHref,
}: {
  subjectId: string;
  subjectName: string;
  resources: CourseResource[];
  /** Where a resource opens. */
  openHref: (resourceId: string) => string;
}) {
  const done = useSubjectCompletion(subjectId);
  const watch = useWatchProgress();
  const [confirming, setConfirming] = useState<CourseResource | null>(null);

  const row =
    "flex min-w-0 flex-1 items-center gap-3 rounded-xl px-2 py-2 text-left transition hover:bg-violet-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-violet-300";

  return (
    <>
      <ul className="flex flex-col gap-0.5 p-2">
        {resources.map((r) => {
          const checked = done.includes(r.id);
          const w = isVideo(r) ? watch[r.id] : undefined;
          const label = watchLabel(w);
          const body = (
            <>
              <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-gray-100 text-base" aria-hidden="true">
                {resourceIcon(r)}
              </span>
              <span className="min-w-0 flex-1">
                <span className={`block truncate text-sm font-medium ${checked ? "text-emerald-800" : "text-gray-900"}`}>
                  {r.title ?? r.id}
                </span>
                <span className="block text-xs text-gray-500">
                  {resourceKindLabel(r)}
                  {checked ? " · Completed" : label ? ` · ${label}` : ""}
                </span>
                {!checked && <WatchBar w={w} />}
              </span>
            </>
          );
          return (
            <li
              key={r.id}
              className={`flex items-center gap-1 rounded-xl transition ${checked ? "bg-emerald-50/70 opacity-70 hover:opacity-100" : ""}`}
            >
              <button
                type="button"
                role="checkbox"
                aria-checked={checked}
                aria-label={checked ? `Mark ${r.title ?? r.id} not completed` : `Mark ${r.title ?? r.id} completed`}
                title={checked ? "Completed — click to undo" : "Mark completed"}
                onClick={() => (checked ? setSubjectCompletion(subjectId, [r.id], false) : setConfirming(r))}
                className="ml-1 rounded-full p-1.5 transition hover:bg-white focus:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400"
              >
                <CircleCheck checked={checked} />
              </button>
              {toSelected(r) ? (
                <Link href={openHref(r.id)} className={row}>
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
      <ConfirmComplete
        resource={confirming}
        subjectName={subjectName}
        onCancel={() => setConfirming(null)}
        onConfirm={() => {
          if (confirming) setSubjectCompletion(subjectId, [confirming.id], true);
          setConfirming(null);
        }}
      />
    </>
  );
}

function ConfirmComplete({
  resource,
  subjectName,
  onCancel,
  onConfirm,
}: {
  resource: CourseResource | null;
  subjectName: string;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  return (
    <SubjectModal
      open={resource !== null}
      onClose={onCancel}
      title="Mark as completed?"
      description={
        <>
          <span className="font-medium text-gray-800">{resource?.title ?? resource?.id}</span> will be marked
          completed for the whole of {subjectName}.
        </>
      }
      icon={<CheckIcon />}
    >
      <p className="rounded-xl border border-gray-200 bg-gray-50 px-3.5 py-3 text-sm text-gray-600">
        This affects <span className="font-medium text-gray-800">every chat in {subjectName}</span>: Malai will read
        this resource in all of them, including chats where you had turned it off.
      </p>
      <div className="mt-5 flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
        <button type="button" data-autofocus onClick={onCancel} className={secondaryBtn}>
          Cancel
        </button>
        <button type="button" onClick={onConfirm} className={primaryBtn}>
          OK
        </button>
      </div>
    </SubjectModal>
  );
}

const secondaryBtn =
  "rounded-xl border border-gray-200 bg-white px-4 py-2.5 text-sm font-medium text-gray-700 shadow-sm transition hover:bg-gray-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-violet-500 focus-visible:ring-offset-2";
const primaryBtn =
  "rounded-xl bg-violet-600 px-4 py-2.5 text-sm font-semibold text-white shadow-sm transition hover:bg-violet-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-violet-500 focus-visible:ring-offset-2";

function CheckIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="h-5 w-5">
      <circle cx="12" cy="12" r="9" />
      <path d="M8 12.5l2.7 2.5L16 9.5" />
    </svg>
  );
}

// ---------------------------------------------------------------------------
// Chat resource chooser
// ---------------------------------------------------------------------------

/**
 * The resources one chat may read, as a button beside the input bar that
 * opens a checklist. `chatKey` is the chat's id, or the draft key for a chat
 * not created yet.
 */
export function ResourceChooser({
  chatKey,
  subjectId,
  subjectName,
  resources,
  up = true,
  className = "",
}: {
  chatKey: string;
  subjectId: string;
  subjectName: string;
  resources: CourseResource[];
  /** Open the list upward (the input bar is at the bottom). */
  up?: boolean;
  className?: string;
}) {
  const done = useSubjectCompletion(subjectId);
  const allowances = useChatAllowances();
  const watch = useWatchProgress();
  const own = allowances[chatKey];
  const ids = resources.map((r) => r.id);
  const allowed = effectiveAllowance(ids, done, own);
  const customised = Boolean(own && (own.add.length || own.remove.length));
  const [open, setOpen] = useState(false);
  /** A resource being ticked: ask whether it is for this chat or the subject. */
  const [asking, setAsking] = useState<CourseResource | null>(null);
  const rootRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (!rootRef.current?.contains(e.target as Node)) {
        setOpen(false);
        setAsking(null);
      }
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setOpen(false);
        setAsking(null);
      }
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const toggle = (r: CourseResource): void => {
    if (allowed.includes(r.id)) {
      setChatAllowed(chatKey, subjectId, r.id, false);
      return;
    }
    // Already completed for the subject (unticked only here): just this chat.
    if (done.includes(r.id)) {
      setChatAllowed(chatKey, subjectId, r.id, true);
      return;
    }
    setAsking(r);
  };

  let list: ReactNode;
  if (resources.length === 0) {
    list = <p className="px-3 py-4 text-sm text-gray-500">No resources in {subjectName} yet.</p>;
  } else {
    list = (
      <ul className="max-h-72 overflow-y-auto p-1.5">
        {resources.map((r) => {
          const on = allowed.includes(r.id);
          const label = isVideo(r) ? watchLabel(watch[r.id]) : null;
          return (
            <li key={r.id}>
              <button
                type="button"
                role="checkbox"
                aria-checked={on}
                onClick={() => toggle(r)}
                className={`flex w-full items-center gap-2.5 rounded-lg px-2 py-1.5 text-left transition hover:bg-violet-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-violet-300 ${
                  on ? "opacity-80" : ""
                }`}
              >
                <CircleCheck checked={on} />
                <span className="min-w-0 flex-1">
                  <span className={`block truncate text-sm ${on ? "text-emerald-800" : "text-gray-800"}`}>{r.title ?? r.id}</span>
                  <span className="block truncate text-[11px] text-gray-500">
                    {resourceKindLabel(r)}
                    {done.includes(r.id) ? " · completed in subject" : label ? ` · ${label}` : ""}
                  </span>
                </span>
              </button>
              {asking?.id === r.id && (
                <div className="mx-2 mb-2 mt-1 rounded-lg border border-violet-200 bg-violet-50 p-2.5 text-xs text-gray-700" role="group" aria-label="Apply to">
                  <p className="mb-2">Mark completed for…</p>
                  <div className="flex flex-wrap gap-1.5">
                    <button
                      type="button"
                      autoFocus
                      onClick={() => {
                        setChatAllowed(chatKey, subjectId, r.id, true);
                        setAsking(null);
                      }}
                      className="rounded-md bg-white px-2.5 py-1 font-medium text-violet-700 ring-1 ring-violet-200 transition hover:bg-violet-100"
                    >
                      This chat only
                    </button>
                    <button
                      type="button"
                      onClick={() => {
                        setSubjectCompletion(subjectId, [r.id], true);
                        setChatAllowed(chatKey, subjectId, r.id, true);
                        setAsking(null);
                      }}
                      className="rounded-md bg-violet-600 px-2.5 py-1 font-medium text-white transition hover:bg-violet-700"
                      title={`Affects every chat in ${subjectName}`}
                    >
                      The whole subject
                    </button>
                    <button type="button" onClick={() => setAsking(null)} className="px-1.5 py-1 text-gray-500 hover:text-gray-800">
                      Cancel
                    </button>
                  </div>
                  <p className="mt-1.5 text-[11px] text-gray-500">The whole subject affects every chat in {subjectName}.</p>
                </div>
              )}
            </li>
          );
        })}
      </ul>
    );
  }

  return (
    <div ref={rootRef} className={`relative ${className}`}>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        aria-haspopup="dialog"
        title="Resources this chat can read"
        className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs font-medium transition focus:outline-none focus-visible:ring-2 focus-visible:ring-violet-400 ${
          allowed.length === 0
            ? "border-amber-200 bg-amber-50 text-amber-700 hover:bg-amber-100"
            : "border-gray-200 bg-white text-gray-600 hover:border-violet-200 hover:text-violet-700"
        }`}
      >
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="h-3.5 w-3.5" aria-hidden="true">
          <path d="M4 19.5A2.5 2.5 0 016.5 17H20V3H6.5A2.5 2.5 0 004 5.5v14zM4 19.5A2.5 2.5 0 006.5 22H20v-5" />
        </svg>
        {allowed.length}/{resources.length} resources
        {customised && <span className="h-1.5 w-1.5 rounded-full bg-violet-500" aria-label="changed for this chat" />}
      </button>
      {open && (
        <div
          role="dialog"
          aria-label="Resources this chat can read"
          className={`absolute left-0 z-40 w-[min(22rem,calc(100vw-2rem))] rounded-xl border border-gray-200 bg-white shadow-xl shadow-gray-900/10 ${
            up ? "bottom-full mb-2" : "top-full mt-2"
          }`}
        >
          <div className="flex items-center gap-2 border-b border-gray-100 px-3 py-2">
            <p className="flex-1 text-xs font-semibold text-gray-900">This chat reads</p>
            {customised && (
              <button
                type="button"
                onClick={() => resetChatAllowance(chatKey)}
                className="text-[11px] font-medium text-violet-700 hover:underline"
                title={`Follow ${subjectName}'s completed resources again`}
              >
                Reset to subject
              </button>
            )}
          </div>
          {list}
          <p className="border-t border-gray-100 px-3 py-2 text-[11px] text-gray-500">
            {allowed.length === 0
              ? "Nothing ticked: Malai answers from general knowledge and offers access when a question needs one of these."
              : `Starts from ${subjectName}'s completed resources. Changes here stay in this chat.`}
          </p>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Access prompt
// ---------------------------------------------------------------------------

/**
 * Shown under an answer when the materials that cover the question are not
 * readable in this chat: give the chat access (this chat, or the whole
 * subject), or leave it as it is. Allowing asks the question again.
 */
export function AccessPrompt({
  titles,
  subjectName,
  onAllowChat,
  onAllowSubject,
  onDismiss,
}: {
  /** Titles of the suggested materials. */
  titles: string[];
  subjectName: string;
  onAllowChat: () => void;
  onAllowSubject: () => void;
  onDismiss: () => void;
}) {
  const named =
    titles.length === 1
      ? `“${titles[0]}”`
      : `${titles.slice(0, -1).map((t) => `“${t}”`).join(", ")} and “${titles[titles.length - 1]}”`;
  return (
    <div role="group" aria-label="Give this chat access" className="rounded-2xl border border-violet-200 bg-violet-50/70 px-4 py-3 text-sm text-gray-700 shadow-sm">
      <p>
        This looks covered in <span className="font-medium text-gray-900">{named}</span>, which this chat can&apos;t read
        yet. Give it access?
      </p>
      <div className="mt-2.5 flex flex-wrap gap-2">
        <button
          type="button"
          onClick={onAllowChat}
          className="rounded-lg bg-white px-3 py-1.5 text-xs font-semibold text-violet-700 ring-1 ring-violet-200 transition hover:bg-violet-100 focus:outline-none focus-visible:ring-2 focus-visible:ring-violet-400"
        >
          Allow for this chat
        </button>
        <button
          type="button"
          onClick={onAllowSubject}
          title={`Marks it completed for ${subjectName}: every chat in it can read it`}
          className="rounded-lg bg-violet-600 px-3 py-1.5 text-xs font-semibold text-white transition hover:bg-violet-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-violet-400"
        >
          Allow for whole subject
        </button>
        <button
          type="button"
          onClick={onDismiss}
          className="rounded-lg px-3 py-1.5 text-xs font-medium text-gray-500 transition hover:bg-white hover:text-gray-800 focus:outline-none focus-visible:ring-2 focus-visible:ring-violet-400"
        >
          Don&apos;t allow
        </button>
      </div>
    </div>
  );
}


/**
 * Nothing the learner has covers the question: offer an answer from general
 * knowledge. Saying yes asks the same question again, allowing it.
 */
export function GeneralPrompt({ onYes, onNo }: { onYes: () => void; onNo: () => void }) {
  return (
    <div role="group" aria-label="Answer from general knowledge?" className="flex flex-wrap items-center gap-2 rounded-2xl border border-amber-200 bg-amber-50/70 px-4 py-3 text-sm text-gray-700 shadow-sm">
      <span className="mr-auto">Not in your materials. Answer from general knowledge?</span>
      <button
        type="button"
        onClick={onYes}
        className="rounded-lg bg-violet-600 px-3 py-1.5 text-xs font-semibold text-white transition hover:bg-violet-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-violet-400"
      >
        Answer from general knowledge
      </button>
      <button
        type="button"
        onClick={onNo}
        className="rounded-lg px-3 py-1.5 text-xs font-medium text-gray-500 transition hover:bg-white hover:text-gray-800 focus:outline-none focus-visible:ring-2 focus-visible:ring-violet-400"
      >
        No thanks
      </button>
    </div>
  );
}
