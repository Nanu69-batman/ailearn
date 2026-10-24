"use client";

import { useCallback, useEffect, useState } from "react";

/** Where the Malai window sits; the lesson window takes the rest. */
export type MalaiSide = "left" | "right" | "top" | "bottom";

export const OPPOSITE: Record<MalaiSide, MalaiSide> = {
  left: "right",
  right: "left",
  top: "bottom",
  bottom: "top",
};

export function isStacked(side: MalaiSide): boolean {
  return side === "top" || side === "bottom";
}

/** The lesson window's share of the workspace, per orientation. */
export const MIN_SPLIT = 0.28;
export const MAX_SPLIT = 0.72;
const DEFAULT_SPLIT = { row: 0.6, col: 0.52 };

const KEY = "ailearn.lesson.layout";

interface Stored {
  side: MalaiSide;
  row: number;
  col: number;
  panelCollapsed: boolean;
}

const DEFAULTS: Stored = { side: "right", ...DEFAULT_SPLIT, panelCollapsed: false };

function clampSplit(n: number): number {
  return Math.min(MAX_SPLIT, Math.max(MIN_SPLIT, n));
}

function read(): Stored {
  try {
    const raw = window.localStorage.getItem(KEY);
    const v = raw ? (JSON.parse(raw) as Partial<Stored>) : {};
    return {
      side: v.side && v.side in OPPOSITE ? v.side : DEFAULTS.side,
      row: typeof v.row === "number" ? clampSplit(v.row) : DEFAULTS.row,
      col: typeof v.col === "number" ? clampSplit(v.col) : DEFAULTS.col,
      panelCollapsed: v.panelCollapsed === true,
    };
  } catch {
    // Storage unavailable or corrupt -> defaults.
    return DEFAULTS;
  }
}

/**
 * The lesson page's window layout: which side Malai is docked to, how the
 * space is split, and whether the chapters panel is collapsed. Remembered per
 * browser, so the page opens the way the learner left it.
 */
export function useLessonLayout() {
  const [state, setState] = useState<Stored>(DEFAULTS);

  // Read after mount: the server render has no storage to agree with.
  useEffect(() => setState(read()), []);

  const update = useCallback((patch: Partial<Stored>): void => {
    setState((prev) => {
      const next = { ...prev, ...patch };
      try {
        window.localStorage.setItem(KEY, JSON.stringify(next));
      } catch {
        // Storage unavailable -> this visit only.
      }
      return next;
    });
  }, []);

  const stacked = isStacked(state.side);
  return {
    side: state.side,
    stacked,
    split: stacked ? state.col : state.row,
    panelCollapsed: state.panelCollapsed,
    setSide: useCallback((side: MalaiSide) => update({ side }), [update]),
    setSplit: useCallback(
      (n: number) => update(stacked ? { col: clampSplit(n) } : { row: clampSplit(n) }),
      [update, stacked],
    ),
    resetSplit: useCallback(
      () => update(stacked ? { col: DEFAULT_SPLIT.col } : { row: DEFAULT_SPLIT.row }),
      [update, stacked],
    ),
    /** Lesson and Malai trade places, keeping the orientation. */
    swap: useCallback(() => update({ side: OPPOSITE[state.side] }), [update, state.side]),
    /** Side by side <-> stacked, keeping Malai second (or first). */
    toggleOrientation: useCallback(() => {
      const turn: Record<MalaiSide, MalaiSide> = { right: "bottom", bottom: "right", left: "top", top: "left" };
      update({ side: turn[state.side] });
    }, [update, state.side]),
    setPanelCollapsed: useCallback((panelCollapsed: boolean) => update({ panelCollapsed }), [update]),
  };
}
