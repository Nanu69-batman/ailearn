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
const ACCENTS: ReadonlyArray<{ avatar: string; bar: string; chip: string }> = [
  { avatar: "from-violet-500 to-indigo-500", bar: "from-violet-500 via-indigo-500 to-blue-500", chip: "bg-violet-50 text-violet-700" },
  { avatar: "from-sky-500 to-cyan-500", bar: "from-sky-500 via-cyan-500 to-teal-400", chip: "bg-sky-50 text-sky-700" },
  { avatar: "from-emerald-500 to-teal-500", bar: "from-emerald-500 via-teal-500 to-cyan-500", chip: "bg-emerald-50 text-emerald-700" },
  { avatar: "from-amber-500 to-orange-500", bar: "from-amber-400 via-orange-500 to-rose-500", chip: "bg-amber-50 text-amber-700" },
  { avatar: "from-rose-500 to-pink-500", bar: "from-rose-500 via-pink-500 to-fuchsia-500", chip: "bg-rose-50 text-rose-700" },
  { avatar: "from-fuchsia-500 to-purple-500", bar: "from-fuchsia-500 via-purple-500 to-violet-500", chip: "bg-fuchsia-50 text-fuchsia-700" },
  { avatar: "from-blue-500 to-violet-500", bar: "from-blue-500 via-indigo-500 to-violet-500", chip: "bg-blue-50 text-blue-700" },
  { avatar: "from-teal-500 to-emerald-500", bar: "from-teal-400 via-emerald-500 to-lime-500", chip: "bg-teal-50 text-teal-700" },
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

function initials(name: string): string {
  const words = name.trim().split(/\s+/).filter(Boolean);
  if (words.length === 0) return "?";
  if (words.length === 1) return words[0].slice(0, 2).toUpperCase();
  return (words[0][0] + words[1][0]).toUpperCase();
}

export interface SubjectCardProps {
  subject: Subject;
  onRequestDelete: (subject: Subject) => void;
  /** Briefly highlight a freshly created card. */
  highlight?: boolean;
}

export default function SubjectCard({
  subject,
  onRequestDelete,
  highlight = false,
}: SubjectCardProps) {
  const accent = subjectAccent(subject.name);
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement | null>(null);
  const menuButtonRef = useRef<HTMLButtonElement | null>(null);
  const count = subject.resource_count ?? 0;

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

  return (
    <div
      className={[
        "group relative h-full rounded-2xl border bg-white shadow-sm transition duration-200",
        "hover:-translate-y-0.5 hover:border-violet-200 hover:shadow-md motion-reduce:transform-none",
        "focus-within:border-violet-200",
        highlight ? "border-violet-300 ring-4 ring-violet-100" : "border-gray-200",
        // Keep the open menu above neighbouring cards.
        menuOpen ? "z-30" : "",
      ].join(" ")}
    >
      <Link
        href={subjectPath(subject.id)}
        className="flex h-full flex-col overflow-hidden rounded-2xl focus:outline-none focus-visible:ring-2 focus-visible:ring-violet-500 focus-visible:ring-offset-2"
      >
        <span
          aria-hidden="true"
          className={`h-1.5 w-full bg-gradient-to-r ${accent.bar}`}
        />
        <span className="flex flex-1 flex-col gap-3 p-5">
          <span className="flex items-center gap-3 pr-8">
            <span
              aria-hidden="true"
              className={`flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-gradient-to-br text-sm font-bold tracking-wide text-white shadow-sm ${accent.avatar}`}
            >
              {initials(subject.name)}
            </span>
            <span className="min-w-0">
              <span className="block truncate text-base font-semibold text-gray-900 transition group-hover:text-violet-700">
                {subject.name}
              </span>
              <span className={`mt-0.5 inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-medium ${accent.chip}`}>
                <svg
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  className="h-3 w-3"
                  aria-hidden="true"
                >
                  <path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8zM14 2v6h6" />
                </svg>
                {count === 1 ? "1 resource" : `${count} resources`}
              </span>
            </span>
          </span>

          <span
            className={
              subject.description
                ? "line-clamp-2 text-sm leading-relaxed text-gray-500"
                : "text-sm italic text-gray-400"
            }
          >
            {subject.description || "No description yet."}
          </span>

          <span className="mt-auto flex items-center justify-between border-t border-gray-100 pt-3 text-sm">
            <span className="font-medium text-violet-600">
              {count === 0 ? "Add materials" : "Start learning"}
            </span>
            <svg
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
              className="h-4 w-4 text-violet-500 transition group-hover:translate-x-0.5"
              aria-hidden="true"
            >
              <path d="M5 12h14M13 6l6 6-6 6" />
            </svg>
          </span>
        </span>
      </Link>

      {/* Sibling of the link (never nested), so it can't trigger navigation. */}
      <div ref={menuRef} className="absolute right-3 top-5">
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
          className="rounded-lg p-1.5 text-gray-400 transition hover:bg-gray-100 hover:text-gray-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-violet-500"
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
            <button
              type="button"
              role="menuitem"
              data-menu-first
              onClick={(e) => {
                e.preventDefault();
                e.stopPropagation();
                setMenuOpen(false);
                onRequestDelete(subject);
              }}
              className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm text-red-600 transition hover:bg-red-50 focus:bg-red-50 focus:outline-none"
            >
              <svg
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.8"
                strokeLinecap="round"
                strokeLinejoin="round"
                className="h-4 w-4"
                aria-hidden="true"
              >
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
      <div className="h-1.5 w-full animate-pulse bg-gray-200" />
      <div className="flex animate-pulse flex-col gap-3 p-5">
        <div className="flex items-center gap-3">
          <div className="h-11 w-11 rounded-xl bg-gray-200" />
          <div className="flex flex-1 flex-col gap-2">
            <div className="h-4 w-2/3 rounded bg-gray-200" />
            <div className="h-3 w-1/3 rounded-full bg-gray-100" />
          </div>
        </div>
        <div className="h-3 w-full rounded bg-gray-100" />
        <div className="h-3 w-4/5 rounded bg-gray-100" />
        <div className="mt-2 border-t border-gray-100 pt-3">
          <div className="h-3 w-24 rounded bg-gray-100" />
        </div>
      </div>
    </div>
  );
}
