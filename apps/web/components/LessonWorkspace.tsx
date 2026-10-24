"use client";

import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type CSSProperties,
  type KeyboardEvent as ReactKeyboardEvent,
  type PointerEvent as ReactPointerEvent,
  type ReactNode,
} from "react";
import { MAX_SPLIT, MIN_SPLIT, OPPOSITE, isStacked, type MalaiSide } from "./useLessonLayout";

type Win = "lesson" | "malai";

const LABEL: Record<Win, string> = { lesson: "Lesson", malai: "Malai" };

/** Pointer travel before a press on a grip becomes a drag. */
const DRAG_THRESHOLD = 6;

interface Drag {
  who: Win;
  startX: number;
  startY: number;
  x: number;
  y: number;
  started: boolean;
  zone: MalaiSide | null;
}

interface LessonWorkspaceProps {
  side: MalaiSide;
  /** The lesson window's share of the workspace, 0..1. */
  split: number;
  onSide: (side: MalaiSide) => void;
  onSplit: (split: number) => void;
  onResetSplit: () => void;
  /** Malai alone: the lesson window is hidden (kept mounted). */
  lessonHidden: boolean;
  /** The page is in immersive fullscreen: the windows lay themselves out. */
  immersive: boolean;
  /** Each window renders its own grip, so it sits in that window's header. */
  lesson: (grip: ReactNode) => ReactNode;
  malai: (grip: ReactNode) => ReactNode;
}

/** Malai's side after dropping `who` on `edge`. */
function sideAfterDrop(who: Win, edge: MalaiSide): MalaiSide {
  return who === "malai" ? edge : OPPOSITE[edge];
}

/** The edge nearest a point inside `rect`. */
function nearestEdge(rect: DOMRect, x: number, y: number): MalaiSide {
  const nx = (x - rect.left) / rect.width;
  const ny = (y - rect.top) / rect.height;
  const d: [MalaiSide, number][] = [
    ["left", nx],
    ["right", 1 - nx],
    ["top", ny],
    ["bottom", 1 - ny],
  ];
  return d.reduce((a, b) => (b[1] < a[1] ? b : a))[0];
}

/** Where each window lands for a given Malai side, as percentage boxes. */
function boxes(side: MalaiSide, split: number): Record<Win, CSSProperties> {
  const l = `${split * 100}%`;
  const m = `${(1 - split) * 100}%`;
  switch (side) {
    case "right":
      return { lesson: { left: 0, top: 0, width: l, height: "100%" }, malai: { right: 0, top: 0, width: m, height: "100%" } };
    case "left":
      return { malai: { left: 0, top: 0, width: m, height: "100%" }, lesson: { right: 0, top: 0, width: l, height: "100%" } };
    case "bottom":
      return { lesson: { left: 0, top: 0, width: "100%", height: l }, malai: { left: 0, bottom: 0, width: "100%", height: m } };
    case "top":
      return { malai: { left: 0, top: 0, width: "100%", height: m }, lesson: { left: 0, bottom: 0, width: "100%", height: l } };
  }
}

function GripIcon() {
  return (
    <svg viewBox="0 0 16 16" fill="currentColor" className="h-4 w-4" aria-hidden="true">
      <circle cx="5.5" cy="3.5" r="1.25" />
      <circle cx="10.5" cy="3.5" r="1.25" />
      <circle cx="5.5" cy="8" r="1.25" />
      <circle cx="10.5" cy="8" r="1.25" />
      <circle cx="5.5" cy="12.5" r="1.25" />
      <circle cx="10.5" cy="12.5" r="1.25" />
    </svg>
  );
}

/**
 * The lesson page's two windows: the lesson (player + chapters) and Malai.
 *
 * Malai docks to any edge. Drag a window by its grip and drop it near an edge:
 * it takes that side and the other window takes the rest. The divider between
 * them resizes. Below `lg` the windows simply stack, lesson first.
 *
 * Both windows keep one position in the React tree whatever the layout (only
 * CSS `order` and flex direction change), so moving them never remounts the
 * player or the chat: playback, the playhead and the conversation survive.
 */
export default function LessonWorkspace({
  side,
  split,
  onSide,
  onSplit,
  onResetSplit,
  lessonHidden,
  immersive,
  lesson,
  malai,
}: LessonWorkspaceProps) {
  const boxRef = useRef<HTMLDivElement>(null);
  const [drag, setDrag] = useState<Drag | null>(null);
  const [resizing, setResizing] = useState(false);
  const stacked = isStacked(side);
  const malaiFirst = side === "left" || side === "top";

  // Esc cancels a drag in progress.
  useEffect(() => {
    if (!drag?.started) return;
    const onKey = (e: KeyboardEvent): void => {
      if (e.key === "Escape") setDrag(null);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [drag?.started]);

  // ---- Moving a window --------------------------------------------------

  const onGripDown = (who: Win) => (e: ReactPointerEvent<HTMLButtonElement>): void => {
    if (e.button !== 0) return;
    // Capture: the pointer keeps reporting here even over the video iframe.
    e.currentTarget.setPointerCapture(e.pointerId);
    setDrag({ who, startX: e.clientX, startY: e.clientY, x: e.clientX, y: e.clientY, started: false, zone: null });
  };

  const onGripMove = (e: ReactPointerEvent<HTMLButtonElement>): void => {
    setDrag((d) => {
      if (!d) return d;
      const moved = Math.hypot(e.clientX - d.startX, e.clientY - d.startY) >= DRAG_THRESHOLD;
      if (!d.started && !moved) return d;
      const rect = boxRef.current?.getBoundingClientRect();
      return {
        ...d,
        x: e.clientX,
        y: e.clientY,
        started: true,
        zone: rect ? nearestEdge(rect, e.clientX, e.clientY) : null,
      };
    });
  };

  const onGripUp = (): void => {
    if (drag?.started && drag.zone) onSide(sideAfterDrop(drag.who, drag.zone));
    setDrag(null);
  };

  /** Keyboard docking: arrows send the focused window's grip to that edge. */
  const onGripKey = (who: Win) => (e: ReactKeyboardEvent<HTMLButtonElement>): void => {
    const edge: Record<string, MalaiSide> = {
      ArrowLeft: "left",
      ArrowRight: "right",
      ArrowUp: "top",
      ArrowDown: "bottom",
    };
    const to = edge[e.key];
    if (!to) return;
    e.preventDefault();
    onSide(sideAfterDrop(who, to));
  };

  const grip = (who: Win): ReactNode =>
    immersive ? null : (
      <button
        type="button"
        onPointerDown={onGripDown(who)}
        onPointerMove={onGripMove}
        onPointerUp={onGripUp}
        onPointerCancel={() => setDrag(null)}
        onKeyDown={onGripKey(who)}
        aria-label={`Move the ${LABEL[who]} window. Drag it to an edge, or press an arrow key.`}
        title="Drag to move this window to any edge"
        className="hidden h-8 w-6 shrink-0 cursor-grab touch-none items-center justify-center rounded-md text-gray-400 transition hover:bg-gray-100 hover:text-gray-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-violet-400 active:cursor-grabbing lg:flex"
      >
        <GripIcon />
      </button>
    );

  // ---- Resizing ----------------------------------------------------------

  const splitAt = useCallback(
    (x: number, y: number): number | null => {
      const rect = boxRef.current?.getBoundingClientRect();
      if (!rect) return null;
      const along = stacked ? (y - rect.top) / rect.height : (x - rect.left) / rect.width;
      // `along` is the share of whatever comes first; the split is the lesson's.
      return malaiFirst ? 1 - along : along;
    },
    [stacked, malaiFirst],
  );

  const onDividerDown = (e: ReactPointerEvent<HTMLDivElement>): void => {
    if (e.button !== 0) return;
    e.currentTarget.setPointerCapture(e.pointerId);
    setResizing(true);
  };
  const onDividerMove = (e: ReactPointerEvent<HTMLDivElement>): void => {
    if (!resizing) return;
    const n = splitAt(e.clientX, e.clientY);
    if (n !== null) onSplit(n);
  };
  const onDividerKey = (e: ReactKeyboardEvent<HTMLDivElement>): void => {
    // Arrows move the divider itself, whichever window is first.
    const grow: Record<string, number> = stacked ? { ArrowDown: 1, ArrowUp: -1 } : { ArrowRight: 1, ArrowLeft: -1 };
    const dir = grow[e.key];
    if (!dir) return;
    e.preventDefault();
    onSplit(split + (malaiFirst ? -dir : dir) * 0.03);
  };

  // While dragging or resizing, keep text from being selected and show the
  // right cursor everywhere (the pointer may leave the handle).
  const busy = resizing || Boolean(drag?.started);
  useEffect(() => {
    if (!busy) return;
    const prev = document.body.style.userSelect;
    document.body.style.userSelect = "none";
    return () => {
      document.body.style.userSelect = prev;
    };
  }, [busy]);

  // ---- Layout -------------------------------------------------------------

  const draggingWho = drag?.started ? drag.who : null;
  const preview = drag?.started && drag.zone ? sideAfterDrop(drag.who, drag.zone) : null;
  const previewBoxes = preview ? boxes(preview, split) : null;

  return (
    <div
      ref={boxRef}
      className={`relative flex min-h-0 flex-1 flex-col gap-5 lg:gap-0 ${stacked ? "lg:flex-col" : "lg:flex-row"} ${
        resizing ? (stacked ? "cursor-row-resize" : "cursor-col-resize") : ""
      }`}
      style={{ "--lesson-share": `${split * 100}%` } as CSSProperties}
    >
      <div
        className={`flex min-w-0 flex-col ${lessonHidden ? "hidden" : ""} ${
          immersive ? "" : "lg:min-h-0 lg:shrink-0 lg:grow-0 lg:basis-[var(--lesson-share)]"
        } ${malaiFirst ? "lg:order-3" : "lg:order-1"} ${
          draggingWho === "lesson" ? "opacity-0" : ""
        } ${resizing ? "pointer-events-none" : ""}`}
      >
        {lesson(grip("lesson"))}
      </div>

      {!lessonHidden && !immersive && (
        <div
          role="separator"
          tabIndex={0}
          aria-orientation={stacked ? "horizontal" : "vertical"}
          aria-label="Resize the lesson and Malai windows"
          aria-valuemin={Math.round(MIN_SPLIT * 100)}
          aria-valuemax={Math.round(MAX_SPLIT * 100)}
          aria-valuenow={Math.round(split * 100)}
          title="Drag to resize · double-click to reset"
          onPointerDown={onDividerDown}
          onPointerMove={onDividerMove}
          onPointerUp={() => setResizing(false)}
          onPointerCancel={() => setResizing(false)}
          onDoubleClick={onResetSplit}
          onKeyDown={onDividerKey}
          className={`group hidden shrink-0 touch-none items-center justify-center rounded-full focus:outline-none focus-visible:ring-2 focus-visible:ring-violet-400 lg:order-2 lg:flex ${
            stacked ? "h-5 w-full cursor-row-resize" : "h-full w-5 cursor-col-resize"
          }`}
        >
          <span
            className={`rounded-full transition-colors ${
              resizing ? "bg-violet-500" : "bg-gray-300 group-hover:bg-violet-400"
            } ${stacked ? "h-1 w-10" : "h-10 w-1"}`}
            aria-hidden="true"
          />
        </div>
      )}

      <div
        className={`flex min-w-0 flex-col ${immersive ? "" : "h-[85dvh] min-h-[480px] lg:h-auto lg:min-h-0 lg:flex-1"} ${
          malaiFirst ? "lg:order-1" : "lg:order-3"
        } ${draggingWho === "malai" ? "opacity-0" : ""} ${resizing ? "pointer-events-none" : ""}`}
      >
        {malai(grip("malai"))}
      </div>

      {/* Drop preview: where both windows land if the drag ends here. */}
      {drag?.started && (
        <div className="pointer-events-none absolute inset-0 z-30 hidden overflow-hidden rounded-2xl bg-gray-100/70 backdrop-blur-[2px] lg:block" aria-hidden="true">
          {previewBoxes &&
            (["lesson", "malai"] as const).map((w) => (
              <div
                key={w}
                className="absolute p-1.5 transition-all duration-150 ease-out"
                style={previewBoxes[w]}
              >
                <div
                  className={`flex h-full w-full items-center justify-center rounded-2xl border-2 text-sm font-semibold ${
                    w === drag.who
                      ? "border-dashed border-violet-400 bg-violet-100/80 text-violet-700"
                      : "border-gray-200 bg-white/90 text-gray-500 shadow-sm"
                  }`}
                >
                  {LABEL[w]}
                </div>
              </div>
            ))}
        </div>
      )}
      {drag?.started && (
        <div
          className="pointer-events-none fixed z-[80] flex items-center gap-1.5 rounded-xl bg-gray-900 px-3 py-1.5 text-xs font-medium text-white shadow-lg"
          style={{ left: drag.x + 14, top: drag.y + 14 }}
          aria-hidden="true"
        >
          <GripIcon />
          {LABEL[drag.who]}
          {preview && <span className="text-white/60">→ {drag.zone}</span>}
        </div>
      )}
    </div>
  );
}
