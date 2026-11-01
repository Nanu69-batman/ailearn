"use client";

import { useEffect, useState } from "react";

/**
 * The line beside the typing dots while Malai works on an answer. It opens
 * with the server's real status ("Reading the lesson"), then rotates through
 * lighter study-themed lines so a longer wait feels alive. Purely cosmetic:
 * the honest status always comes first, and nothing here claims progress.
 */
const LINES = [
  "Flipping through your notes",
  "Sharpening the pencil",
  "Consulting the margins",
  "Rewinding the good part",
  "Connecting the dots",
  "Dusting off the whiteboard",
  "Double-checking the sources",
  "Hunting for the right example",
  "Underlining the important bits",
  "Finding the clearest way to say it",
  "Cross-referencing the chapters",
  "Asking the textbook nicely",
  "Doing the homework so you don't have to",
  "Untangling the jargon",
  "Pacing around the library",
  "Brewing a fresh explanation",
  "Checking the footnotes",
  "Lining up the citations",
];

const ROTATE_MS = 2600;

function shuffled<T>(items: readonly T[]): T[] {
  const a = [...items];
  for (let i = a.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [a[i], a[j]] = [a[j], a[i]];
  }
  return a;
}

export default function ThinkingLine({ status }: { status: string | null | undefined }) {
  // A fresh order per answer, so it doesn't open with the same joke every time.
  const [order] = useState(() => shuffled(LINES));
  const [tick, setTick] = useState(0);

  useEffect(() => {
    const id = window.setInterval(() => setTick((t) => t + 1), ROTATE_MS);
    return () => window.clearInterval(id);
  }, []);

  // A new server status is news: show it before going back to the rotation.
  useEffect(() => setTick(0), [status]);

  const text = tick === 0 && status ? status : order[(tick - (status ? 1 : 0)) % order.length];

  return (
    <span key={text} className="inline-block animate-[shell-fade_300ms_ease-out] text-xs text-gray-500 motion-reduce:animate-none">
      {text}…
    </span>
  );
}
