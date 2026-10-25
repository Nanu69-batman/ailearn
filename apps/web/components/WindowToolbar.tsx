"use client";

import type { ReactNode } from "react";

interface WindowToolbarProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** "video" or "resource": what the first toggle shows and hides. */
  resourceLabel: string;
  lessonShown: boolean;
  malaiShown: boolean;
  stacked: boolean;
  onToggleLesson: () => void;
  onToggleMalai: () => void;
  onToggleOrientation: () => void;
  onSwap: () => void;
}

function Tool({
  label,
  pressed,
  disabled,
  onClick,
  className = "",
  children,
}: {
  label: string;
  pressed?: boolean;
  disabled?: boolean;
  onClick: () => void;
  className?: string;
  children: ReactNode;
}) {
  return (
    <span className={`group/tool relative ${className}`}>
      <button
        type="button"
        onClick={onClick}
        disabled={disabled}
        aria-pressed={pressed}
        aria-label={label}
        className={`flex h-10 w-10 items-center justify-center rounded-full transition focus:outline-none focus-visible:ring-2 focus-visible:ring-violet-400 disabled:cursor-not-allowed disabled:opacity-35 ${
          pressed ? "bg-violet-100 text-violet-700" : "text-gray-600 hover:bg-gray-100 hover:text-gray-900"
        }`}
      >
        {children}
      </button>
      <span
        role="tooltip"
        className="pointer-events-none absolute bottom-full left-1/2 mb-2 -translate-x-1/2 whitespace-nowrap rounded-md bg-gray-900 px-2.5 py-1.5 text-xs font-medium text-white opacity-0 shadow transition group-hover/tool:opacity-100 group-focus-within/tool:opacity-100"
      >
        {label}
      </span>
    </span>
  );
}

const svg = {
  viewBox: "0 0 24 24",
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 2,
  strokeLinecap: "round" as const,
  strokeLinejoin: "round" as const,
  className: "h-5 w-5",
  "aria-hidden": true,
};

/**
 * The lesson page's window controls, floating at the bottom left: show or
 * hide the resource and Malai, side by side vs stacked, and swap. It folds
 * into one round button. Layout controls only exist where the windows dock
 * (`lg`); below that the windows simply stack.
 */
export default function WindowToolbar({
  open,
  onOpenChange,
  resourceLabel,
  lessonShown,
  malaiShown,
  stacked,
  onToggleLesson,
  onToggleMalai,
  onToggleOrientation,
  onSwap,
}: WindowToolbarProps) {
  const both = lessonShown && malaiShown;
  return (
    <div
      role="toolbar"
      aria-label="Windows"
      className="fixed bottom-4 left-4 z-30 flex items-center gap-1 rounded-full border border-gray-200 bg-white/95 p-1 shadow-xl shadow-gray-900/10 backdrop-blur lg:absolute lg:bottom-auto lg:left-0 lg:top-full lg:mt-3"
    >
      <button
        type="button"
        onClick={() => onOpenChange(!open)}
        aria-expanded={open}
        aria-label={open ? "Fold the window controls" : "Window controls"}
        title={open ? "Fold" : "Window controls"}
        className="flex h-10 w-10 items-center justify-center rounded-full bg-violet-600 text-white shadow-md shadow-violet-600/30 transition hover:bg-violet-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-violet-400 focus-visible:ring-offset-2"
      >
        {open ? (
          <svg {...svg}>
            <path d="M15 6l-6 6 6 6" />
          </svg>
        ) : (
          <svg {...svg}>
            <rect x="3" y="4" width="18" height="16" rx="2" />
            <path d="M12 4v16" />
          </svg>
        )}
      </button>

      {open && (
        <>
          <Tool label={lessonShown ? `Hide the ${resourceLabel}` : `Show the ${resourceLabel}`} pressed={lessonShown} onClick={onToggleLesson}>
            <svg {...svg}>
              <rect x="2.5" y="6" width="13" height="12" rx="2" />
              <path d="M15.5 10.5l6-3.5v10l-6-3.5" />
              {!lessonShown && <path d="M3 3l18 18" />}
            </svg>
          </Tool>
          <Tool label={malaiShown ? "Hide Malai" : "Show Malai"} pressed={malaiShown} onClick={onToggleMalai}>
            <svg {...svg}>
              <path d="M21 12a8 8 0 01-11.6 7.1L4 20l1-4.6A8 8 0 1121 12z" />
              {!malaiShown && <path d="M3 3l18 18" />}
            </svg>
          </Tool>
          <span className="mx-1 hidden h-6 w-px bg-gray-200 lg:block" aria-hidden="true" />
          <Tool
            label={stacked ? "Side by side" : "Stacked"}
            disabled={!both}
            onClick={onToggleOrientation}
            className="hidden lg:block"
          >
            <svg {...svg}>
              <rect x="3" y="4" width="18" height="16" rx="2" />
              {stacked ? <path d="M13 4v16" /> : <path d="M3 13h18" />}
            </svg>
          </Tool>
          <Tool label="Swap places" disabled={!both} onClick={onSwap} className="hidden lg:block">
            <svg {...svg} className={`h-5 w-5 ${stacked ? "rotate-90" : ""}`}>
              <path d="M4 8h15l-3.5-3.5M20 16H5l3.5 3.5" />
            </svg>
          </Tool>
        </>
      )}
    </div>
  );
}
