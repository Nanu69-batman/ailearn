/**
 * Locate a cited passage inside a PDF page's text layer.
 *
 * The cited text was extracted server-side (pypdf) and the text layer comes
 * from pdf.js, so the two disagree on whitespace, line breaks, hyphenation and
 * ligatures. Both sides are therefore reduced to lowercase letters and digits
 * only (NFKD first, so "ﬁ" -> "fi" and accents drop), and the match runs on
 * that skeleton. Every kept character remembers which text item and offset it
 * came from, so a skeleton range maps straight back to spans on the page.
 */

export interface ItemRange {
  /** Start offset within the item's string (inclusive). */
  from: number;
  /** End offset within the item's string (exclusive). */
  to: number;
}

interface Skeleton {
  text: string;
  item: number[];
  offset: number[];
}

const KEEP = /[\p{L}\p{N}]/u;
const ANCHOR = 32;

function skeletonOf(parts: string[]): Skeleton {
  const out: Skeleton = { text: "", item: [], offset: [] };
  const chars: string[] = [];
  parts.forEach((part, i) => {
    for (let j = 0; j < part.length; j++) {
      for (const ch of part[j].normalize("NFKD").toLowerCase()) {
        if (!KEEP.test(ch) || /\p{M}/u.test(ch)) continue;
        chars.push(ch);
        out.item.push(i);
        out.offset.push(j);
      }
    }
  });
  out.text = chars.join("");
  return out;
}

/** [start, end) in the page skeleton, or null when nothing credible matches. */
function locate(page: string, needle: string): [number, number] | null {
  if (!needle || !page) return null;
  const exact = page.indexOf(needle);
  if (exact !== -1) return [exact, exact + needle.length];
  if (needle.length <= ANCHOR) return null;

  // Not verbatim: find the first and last anchor-sized windows of the
  // passage that do occur, in order, and highlight between them.
  const windows: number[] = [];
  for (let i = 0; i + ANCHOR <= needle.length; i += ANCHOR) windows.push(i);
  windows.push(needle.length - ANCHOR);

  let start = -1;
  let startW = 0;
  for (const w of windows) {
    const at = page.indexOf(needle.slice(w, w + ANCHOR));
    if (at !== -1) {
      start = Math.max(0, at - w);
      startW = w;
      break;
    }
  }
  if (start === -1) return null;
  for (let k = windows.length - 1; k >= 0; k--) {
    const w = windows[k];
    if (w < startW) break;
    const at = page.indexOf(needle.slice(w, w + ANCHOR), start);
    if (at !== -1) {
      const end = Math.min(page.length, at + ANCHOR + (needle.length - w - ANCHOR));
      // Reject a "match" stretched far beyond the passage's own length.
      if (end - start <= needle.length * 1.5) return [start, end];
      break;
    }
  }
  return [start, Math.min(page.length, start + needle.length)];
}

/**
 * Per-item character ranges to highlight for `passage` on a page whose text
 * items are `items` (indices match the text layer's item indices). Empty when
 * the passage is not on this page.
 */
export function highlightRanges(items: string[], passage: string): Map<number, ItemRange> {
  const page = skeletonOf(items);
  const needle = skeletonOf([passage]).text;
  const found = locate(page.text, needle);
  const ranges = new Map<number, ItemRange>();
  if (!found) return ranges;
  for (let k = found[0]; k < found[1]; k++) {
    const i = page.item[k];
    const o = page.offset[k];
    const r = ranges.get(i);
    if (!r) ranges.set(i, { from: o, to: o + 1 });
    else {
      r.from = Math.min(r.from, o);
      r.to = Math.max(r.to, o + 1);
    }
  }
  return ranges;
}

export function escapeHtml(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}
