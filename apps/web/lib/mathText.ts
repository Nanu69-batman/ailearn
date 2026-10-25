/**
 * Normalises the LaTeX delimiters models use into the ones remark-math reads.
 *
 * The prompt asks for `$…$` and `$$…$$`, but Gemini still writes `\(…\)` and
 * `\[…\]` now and then, which would show up as raw source. This rewrites only
 * those two delimiter pairs (code spans and fenced blocks are left alone); the
 * maths itself is still parsed and rendered by remark-math + rehype-katex.
 *
 * Display math is put on its own lines, because mdast-util-math only emits a
 * centred block when the `$$` fences stand alone.
 */
const DOLLARS = "$$";

export function normalizeMath(text: string): string {
  if (!text.includes("\\(") && !text.includes("\\[")) return text;
  // Split out fenced blocks and inline code; only prose is rewritten.
  const parts = text.split(/(```[\s\S]*?(?:```|$)|`[^`\n]*`)/);
  return parts
    .map((part, i) => {
      if (i % 2 === 1) return part;
      return part
        .replace(/\\\[([\s\S]+?)\\\]/g, (_, body: string) => `\n${DOLLARS}\n${body.trim()}\n${DOLLARS}\n`)
        .replace(/\\\(([\s\S]+?)\\\)/g, (_, body: string) => "$" + body.trim() + "$");
    })
    .join("");
}
