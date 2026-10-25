"use client";

import { Component, useRef, useState, type ComponentPropsWithoutRef, type ReactNode } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkMath from "remark-math";
import remarkGfm from "remark-gfm";
import rehypeKatex from "rehype-katex";
import { normalizeMath } from "../lib/mathText";

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
 * would silently break every inline fraction in every answer. `\(...\)` and
 * `\[...\]`, which models also write, are rewritten first (lib/mathText.ts).
 *
 * Note on display math: mdast-util-math only emits a FLOW (block) math node
 * when the `$$` fences sit on their own lines. A one-line `$$x$$` is parsed as
 * text math and renders inline-styled rather than centred. Both forms render
 * as real KaTeX; neither is dropped.
 *
 * Typography lives in the `.md` rules in app/globals.css (Tailwind's reset
 * strips list bullets, heading sizes and so on). remark-gfm adds tables,
 * strikethrough and task lists.
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

/** A fenced code block: language label and a copy button over the code. */
function CodeBlock({ children, ...rest }: ComponentPropsWithoutRef<"pre">) {
  const ref = useRef<HTMLPreElement>(null);
  const [copied, setCopied] = useState(false);
  // react-markdown puts the fence's language on the inner <code> as `language-x`.
  const child = Array.isArray(children) ? children[0] : children;
  const cls = (child as { props?: { className?: string } } | undefined)?.props?.className ?? "";
  const lang = /language-([\w+#-]+)/.exec(cls)?.[1] ?? "";

  function copy(): void {
    const text = ref.current?.textContent ?? "";
    void navigator.clipboard?.writeText(text).then(() => {
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    });
  }

  return (
    <div className="md-code">
      <div className="md-code-bar">
        <span>{lang || "code"}</span>
        <button type="button" onClick={copy} className="md-code-copy">
          {copied ? "Copied" : "Copy"}
        </button>
      </div>
      <pre ref={ref} {...rest}>
        {children}
      </pre>
    </div>
  );
}

const COMPONENTS: Components = {
  pre: ({ node: _node, ...props }) => <CodeBlock {...props} />,
  // Wide tables scroll inside the message instead of stretching it.
  table: ({ node: _node, ...props }) => (
    <div className="md-table">
      <table {...props} />
    </div>
  ),
  a: ({ node: _node, ...props }) => <a {...props} target="_blank" rel="noopener noreferrer" />,
};

export default function MarkdownBody({
  text,
  className = "",
}: {
  text: string;
  className?: string;
}) {
  return (
    <div className={`md break-words ${className}`}>
      <MarkdownFallback text={text}>
        <ReactMarkdown
          remarkPlugins={[remarkMath, remarkGfm]}
          rehypePlugins={[[rehypeKatex, KATEX_OPTIONS]]}
          components={COMPONENTS}
        >
          {normalizeMath(text)}
        </ReactMarkdown>
      </MarkdownFallback>
    </div>
  );
}
