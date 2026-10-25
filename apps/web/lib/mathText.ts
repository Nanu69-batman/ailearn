/**
 * Normalises the LaTeX layouts models write into the ones remark-math reads.
 *
 * remark-math only renders a centred display equation when the `$$` fences
 * stand on their own lines. Models routinely write `$$ … $$` on one line, or
 * mid-sentence, or use `\[ … \]` / `\( … \)`; all of those came out as small
 * inline maths jammed against the text. This rewrites only delimiters and
 * line layout (code spans and fenced blocks are left alone); the maths itself
 * is still parsed and rendered by remark-math + rehype-katex.
 */
const DOLLARS = "$$";

/** A leading KaTeX size command. Key equations are marked with `\large`. */
const SIZE = /^\\(tiny|scriptsize|footnotesize|small|normalsize|large|Large|LARGE|huge|Huge)(?![A-Za-z])\s*/;

/** Any enlarging size becomes `\large`, so a key equation stands out without
 *  dwarfing the answer; a shrinking one is dropped. */
function keySize(body: string): string {
  const m = SIZE.exec(body);
  if (!m) return body;
  const rest = body.slice(m[0].length);
  return /large|huge/i.test(m[1]) ? "\\large " + rest : rest;
}

/** `\[x\]` and `\(x\)` into dollar delimiters. */
function dollarDelimiters(prose: string): string {
  return prose
    // The newlines mark it as display, so displayBlocks never inlines it.
    .replace(/\\\[([\s\S]+?)\\\]/g, (_, body: string) => DOLLARS + "\n" + body.trim() + "\n" + DOLLARS)
    .replace(/\\\(([\s\S]+?)\\\)/g, (_, body: string) => "$" + body.trim() + "$");
}

/**
 * Every `$$…$$` in prose as a block: fences on their own lines, indented to
 * stay inside the list item or quote it was written in. Trailing punctuation
 * moves inside the equation so it does not become a paragraph of its own. A
 * short one inside a sentence ("where $$a$$ is…") was meant inline: it
 * becomes `$a$`.
 */
function displayBlocks(prose: string): string {
  return prose.replace(
    /\$\$([\s\S]+?)\$\$([.,;:])?/g,
    (_m, raw: string, punct: string | undefined, offset: number, whole: string) => {
      const body = keySize(raw.trim());
      if (!body) return _m;
      const lineStart = whole.lastIndexOf("\n", offset - 1) + 1;
      const lineEnd = whole.indexOf("\n", offset);
      const before = whole.slice(lineStart, offset);
      const after = whole.slice(offset + _m.length, lineEnd === -1 ? undefined : lineEnd);
      // Container prefix of this line: quote markers, then a list marker's width.
      const m = /^(\s*(?:>\s?)*)((?:[-*+]|\d+[.)])\s+)?/.exec(before) ?? ["", "", undefined];
      const indent = m[1] + " ".repeat(m[2]?.length ?? 0);
      const inSentence = before.length > m[0].length && after.trim().length > 0;
      if (inSentence && !raw.includes("\n") && body.length <= 24) {
        return "$" + body.replace(SIZE, "") + "$" + (punct ?? "");
      }
      const blank = "\n" + indent.trimEnd() + "\n" + indent;
      // Opening a list item or quote ("1. $$…"), the fence starts right there.
      const lead = before.length > m[0].length ? blank : "";
      const lines = (body + (punct ?? "")).split("\n").map((l) => indent + l.trim());
      return lead + DOLLARS + "\n" + lines.join("\n") + "\n" + indent + DOLLARS + blank;
    },
  );
}

export function normalizeMath(text: string): string {
  if (!/\$\$|\\\(|\\\[|\$\\(?:large|Large|LARGE|huge|Huge)/.test(text)) return text;
  // Split out fenced blocks and inline code; only prose is rewritten.
  const parts = text.split(/(```[\s\S]*?(?:```|$)|`[^`\n]*`)/);
  return parts
    .map((part, i) =>
      i % 2 === 1
        ? part
        // Inline maths keeps the line height: size commands are display-only.
        : displayBlocks(dollarDelimiters(part)).replace(/(^|[^$\\])\$\\(?:large|Large|LARGE|huge|Huge)(?![A-Za-z])\s*/g, "$1$"),
    )
    .join("");
}
