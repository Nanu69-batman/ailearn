"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import type { Subject } from "@/lib/api";
import { subjectPath } from "@/lib/learn";

/**
 * Accent palettes. Literal class strings so Tailwind's JIT keeps them.
 * Picked deterministically from the subject name, so a subject keeps its
 * colour across reloads and devices.
 */
const ACCENTS: ReadonlyArray<{ avatar: string; bar: string }> = [
  { avatar: "bg-sky-500", bar: "bg-sky-400" },
  { avatar: "bg-emerald-400", bar: "bg-emerald-400" },
  { avatar: "bg-rose-400", bar: "bg-rose-400" },
  { avatar: "bg-amber-400", bar: "bg-amber-400" },
  { avatar: "bg-violet-400", bar: "bg-violet-400" },
  { avatar: "bg-indigo-500", bar: "bg-indigo-400" },
  { avatar: "bg-teal-500", bar: "bg-teal-400" },
  { avatar: "bg-pink-400", bar: "bg-pink-400" },
];

function hashName(name: string): number {
  let h = 0;
  for (let i = 0; i < name.length; i++) {
    h = (h * 31 + name.charCodeAt(i)) | 0;
  }
  return Math.abs(h);
}

export function subjectAccent(name: string) {
  return ACCENTS[hashName(name.trim().toLowerCase()) % ACCENTS.length];
}

export function initials(name: string): string {
  const words = name.trim().split(/\s+/).filter(Boolean);
  if (words.length === 0) return "?";
  if (words.length === 1) return words[0].slice(0, 2).toUpperCase();
  return (words[0][0] + words[1][0]).toUpperCase();
}

function FileIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="h-3.5 w-3.5" aria-hidden="true">
      <path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8zM14 2v6h6" />
    </svg>
  );
}

function ArrowIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" className="h-4 w-4 transition group-hover:translate-x-0.5" aria-hidden="true">
      <path d="M5 12h14M13 6l6 6-6 6" />
    </svg>
  );
}

export function ProgressBar({ value }: { value: number }) {
  return (
    <span className="flex items-center gap-4">
      <span
        role="progressbar"
        aria-valuenow={value}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-label="Progress"
        className="h-2 flex-1 overflow-hidden rounded-full bg-gray-200"
      >
        <span className="block h-full rounded-full bg-violet-600 transition-[width] duration-500" style={{ width: `${value}%` }} />
      </span>
      <span className="w-10 text-right text-sm tabular-nums text-gray-600">{value}%</span>
    </span>
  );
}

export interface SubjectCardProps {
  subject: Subject;
  onRequestDelete: (subject: Subject) => void;
  /** Percent of resources marked done; omit to hide the progress bar. */
  progress?: number;
  /** True once the learner has opened a lesson in this subject. */
  started?: boolean;
  archived?: boolean;
  onToggleArchive?: (subject: Subject) => void;
  /** Compact single-row variant for the list view. */
  layout?: "grid" | "list";
  /** Briefly highlight a freshly created card. */
  highlight?: boolean;
}

export default function SubjectCard({
  subject,
  onRequestDelete,
  progress,
  started = false,
  archived = false,
  onToggleArchive,
  layout = "grid",
  highlight = false,
}: SubjectCardProps) {
  const accent = subjectAccent(subject.name);
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement | null>(null);
  const menuButtonRef = useRef<HTMLButtonElement | null>(null);
  const count = subject.resource_count ?? 0;
  const cta =
    count === 0 ? "Add materials" : started || (progress ?? 0) > 0 ? "Continue learning" : "Start learning";

  // Outside click / Escape close the menu.
  useEffect(() => {
    if (!menuOpen) return;
    menuRef.current
      ?.querySelector<HTMLElement>("[data-menu-first]")
      ?.focus();
    function onPointer(event: MouseEvent): void {
      if (!menuRef.current?.contains(event.target as Node)) setMenuOpen(false);
    }
    function onKey(event: KeyboardEvent): void {
      if (event.key === "Escape") {
        setMenuOpen(false);
        menuButtonRef.current?.focus();
      }
    }
    document.addEventListener("mousedown", onPointer);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onPointer);
      document.removeEventListener("keydown", onKey);
    };
  }, [menuOpen]);

  const avatar = (
    <span
      aria-hidden="true"
      className={`flex h-14 w-14 shrink-0 items-center justify-center rounded-xl text-lg font-bold tracking-wide text-white shadow-sm ${accent.avatar}`}
    >
      {initials(subject.name)}
    </span>
  );

  const resourceChip = (
    <span className="inline-flex items-center gap-1.5 rounded-full bg-gray-100 px-2.5 py-0.5 text-xs font-medium text-gray-600">
      <FileIcon />
      {count === 1 ? "1 resource" : `${count} resources`}
    </span>
  );

  const description = (
    <span
      className={
        subject.description
          ? "line-clamp-2 text-sm leading-relaxed text-gray-500"
          : "text-sm italic text-gray-400"
      }
    >
      {subject.description || "No description yet."}
    </span>
  );

  return (
    <div
      className={[
        "group relative h-full rounded-2xl border bg-white shadow-sm transition duration-200",
        "hover:-translate-y-0.5 hover:shadow-md motion-reduce:transform-none",
        highlight ? "border-violet-300 ring-4 ring-violet-100" : "border-gray-200",
        archived ? "opacity-70" : "",
        // Keep the open menu above neighbouring cards.
        menuOpen ? "z-30" : "",
      ].join(" ")}
    >
      <Link
        href={subjectPath(subject.id)}
        className="flex h-full flex-col overflow-hidden rounded-2xl focus:outline-none focus-visible:ring-2 focus-visible:ring-violet-500 focus-visible:ring-offset-2"
      >
        <span aria-hidden="true" className={`h-[3px] w-full ${accent.bar}`} />
        {layout === "list" ? (
          <span className="flex flex-col gap-4 p-4 pr-14 sm:flex-row sm:items-center sm:gap-6">
            <span className="flex min-w-0 flex-1 items-center gap-4">
              {avatar}
              <span className="flex min-w-0 flex-col gap-1">
                <span className="truncate text-base font-semibold text-gray-900 group-hover:text-violet-700">{subject.name}</span>
                <span className="truncate text-sm text-gray-500">{subject.description || "No description yet."}</span>
              </span>
            </span>
            <span className="shrink-0">{resourceChip}</span>
            {progress !== undefined && (
              <span className="w-full shrink-0 sm:w-56">
                <ProgressBar value={progress} />
              </span>
            )}
            <span className="inline-flex shrink-0 items-center gap-2 text-sm font-semibold text-violet-700">
              {cta}
              <ArrowIcon />
            </span>
          </span>
        ) : (
          <span className="flex flex-1 flex-col gap-4 p-6">
            <span className="flex items-start gap-4 pr-8">
              {avatar}
              <span className="flex min-w-0 flex-col items-start gap-2">
                <span className="block max-w-full truncate text-lg font-semibold text-gray-900 transition group-hover:text-violet-700">
                  {subject.name}
                </span>
                {resourceChip}
              </span>
            </span>
            {description}
            {progress !== undefined && <ProgressBar value={progress} />}
            <span className="mt-auto inline-flex items-center gap-2 pt-1 text-sm font-semibold text-violet-700">
              {cta}
              <ArrowIcon />
            </span>
          </span>
        )}
      </Link>

      {/* Sibling of the link (never nested), so it can't trigger navigation. */}
      <div ref={menuRef} className={layout === "list" ? "absolute right-3 top-1/2 -translate-y-1/2" : "absolute right-4 top-6"}>
        <button
          ref={menuButtonRef}
          type="button"
          aria-haspopup="menu"
          aria-expanded={menuOpen}
          aria-label={`More actions for ${subject.name}`}
          onClick={(e) => {
            e.preventDefault();
            e.stopPropagation();
            setMenuOpen((v) => !v);
          }}
          className="rounded-lg p-1.5 text-gray-500 transition hover:bg-gray-100 hover:text-gray-800 focus:outline-none focus-visible:ring-2 focus-visible:ring-violet-500"
        >
          <svg viewBox="0 0 24 24" fill="currentColor" className="h-5 w-5" aria-hidden="true">
            <circle cx="5" cy="12" r="1.75" />
            <circle cx="12" cy="12" r="1.75" />
            <circle cx="19" cy="12" r="1.75" />
          </svg>
        </button>
        {menuOpen && (
          <div
            role="menu"
            aria-label={`Actions for ${subject.name}`}
            className="absolute right-0 z-20 mt-1 w-44 overflow-hidden rounded-xl border border-gray-200 bg-white py-1 shadow-lg"
          >
            {onToggleArchive && (
              <button
                type="button"
                role="menuitem"
                data-menu-first
                onClick={(e) => {
                  e.preventDefault();
                  e.stopPropagation();
                  setMenuOpen(false);
                  onToggleArchive(subject);
                }}
                className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm text-gray-700 transition hover:bg-gray-50 focus:bg-gray-50 focus:outline-none"
              >
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" className="h-4 w-4" aria-hidden="true">
                  <path d="M21 8v13H3V8M1 3h22v5H1zM10 12h4" />
                </svg>
                {archived ? "Unarchive" : "Archive"}
              </button>
            )}
            <button
              type="button"
              role="menuitem"
              data-menu-first={onToggleArchive ? undefined : true}
              onClick={(e) => {
                e.preventDefault();
                e.stopPropagation();
                setMenuOpen(false);
                onRequestDelete(subject);
              }}
              className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm text-red-600 transition hover:bg-red-50 focus:bg-red-50 focus:outline-none"
            >
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" className="h-4 w-4" aria-hidden="true">
                <path d="M3 6h18M8 6V4a2 2 0 012-2h4a2 2 0 012 2v2M19 6l-1 14a2 2 0 01-2 2H8a2 2 0 01-2-2L5 6" />
              </svg>
              Delete subject
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

export function SubjectCardSkeleton() {
  return (
    <div
      aria-hidden="true"
      className="h-full overflow-hidden rounded-2xl border border-gray-200 bg-white shadow-sm"
    >
      <div className="h-[3px] w-full animate-pulse bg-gray-200" />
      <div className="flex animate-pulse flex-col gap-4 p-6">
        <div className="flex items-center gap-4">
          <div className="h-14 w-14 rounded-xl bg-gray-200" />
          <div className="flex flex-1 flex-col gap-2">
            <div className="h-4 w-2/3 rounded bg-gray-200" />
            <div className="h-3 w-1/3 rounded-full bg-gray-100" />
          </div>
        </div>
        <div className="h-3 w-full rounded bg-gray-100" />
        <div className="h-2 w-full rounded-full bg-gray-100" />
        <div className="h-3 w-28 rounded bg-gray-100" />
      </div>
    </div>
  );
}

/** Dashed "Add subject" tile that sits at the end of a subject grid. */
export function AddSubjectTile({ onClick, detailed = false }: { onClick: () => void; detailed?: boolean }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="group flex h-full min-h-[8rem] w-full flex-col items-center justify-center gap-2 rounded-2xl border-2 border-dashed border-gray-300 bg-white/40 p-6 text-center transition hover:border-violet-300 hover:bg-violet-50/40 focus:outline-none focus-visible:ring-2 focus-visible:ring-violet-500 focus-visible:ring-offset-2"
    >
      <span className="flex h-12 w-12 items-center justify-center rounded-xl bg-gray-100 text-gray-500 transition group-hover:bg-violet-100 group-hover:text-violet-700">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" className="h-6 w-6" aria-hidden="true">
          <path d="M12 5v14M5 12h14" />
        </svg>
      </span>
      <span className={detailed ? "text-base font-semibold text-gray-800" : "text-sm font-medium text-gray-700"}>Add subject</span>
      {detailed && (
        <span className="max-w-[16rem] text-sm text-gray-500">
          Create a new subject to organize your learning materials.
        </span>
      )}
    </button>
  );
}
