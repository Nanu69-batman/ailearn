"use client";

import { Component, type ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import remarkMath from "remark-math";
import rehypeKatex from "rehype-katex";

/*
 * Markdown + KaTeX renderer for chat message bodies.
 *
 * Security posture (see scripts/contract-check.mjs, section 19):
 *   - `rehype-raw` is deliberately ABSENT. Message content is LLM output;
 *     without rehype-raw react-markdown escapes raw HTML, which is what keeps
 *     `<script>` / `<img onerror=...>` inert. Adding it re-opens XSS.
 *   - KaTeX runs with `throwOnError: false`, so `\href`, `\url`,
 *     `\includegraphics` and command execution stay disabled.
 *   - No `dangerouslySetInnerHTML` anywhere in this path. KaTeX output is
 *     built as hast and rendered by rehype-katex's own JSX runtime.
 *
 * Delimiters: remark-math is used with its DEFAULT options, which is the only
 * setting that supports both inline `$...$` and `$$...$$`. Do not "fix"
 * display math by passing `singleDollarTextMath: false` — that option
 * DISABLES inline single-dollar math (mdast-util-math readme: "If you turn
 * this off, you can still use two or more dollars for text math"), which
 * would silently break every inline fraction in every answer.
 *
 * Note on display math: mdast-util-math only emits a FLOW (block) math node
 * when the `$$` fences sit on their own lines. A one-line `$$x$$` is parsed as
 * text math and renders inline-styled rather than centred. Both forms render
 * as real KaTeX; neither is dropped.
 *
 * Failure behaviour: malformed `$...$` from the model degrades to the raw
 * source text in red (KaTeX errorColor) rather than throwing. If something
 * does throw anyway, <MarkdownFallback> renders the plain text so the chat
 * list can never go blank.
 */

const KATEX_OPTIONS = {
  // Never `true`: it would enable \href, \url and \includegraphics.
  throwOnError: false as const,
  errorColor: "#dc2626",
  strict: false,
  output: "htmlAndMathml" as const,
};

interface FallbackBoundaryProps {
  text: string;
  children: ReactNode;
}

class MarkdownFallback extends Component<
  FallbackBoundaryProps,
  { failed: boolean }
> {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  componentDidCatch() {
    // Swallowed on purpose: the whole point is that one malformed message
    // cannot take down the transcript.
  }

  render() {
    if (this.state.failed) {
      return (
        <div className="whitespace-pre-wrap break-words">{this.props.text}</div>
      );
    }
    return this.props.children;
  }
}

export default function MarkdownBody({
  text,
  className = "",
}: {
  text: string;
  className?: string;
}) {
  return (
    <div
      className={`text-sm leading-relaxed break-words [&>*:first-child]:mt-0 [&>*:last-child]:mb-0 space-y-2 ${className}`}
    >
      <MarkdownFallback text={text}>
        <ReactMarkdown
          remarkPlugins={[remarkMath]}
          rehypePlugins={[[rehypeKatex, KATEX_OPTIONS]]}
        >
          {text}
        </ReactMarkdown>
      </MarkdownFallback>
    </div>
  );
}
