"use client";

import type { ChatMessage, Citation, MessageKind } from "../lib/api";
import CitationBadge from "./CitationBadge";
import MarkdownBody from "./MarkdownBody";

interface MessageListProps {
  messages: ChatMessage[];
  loading: boolean;
  onSeek: (startMs: number) => void;
  /** Sends a suggested prompt immediately (empty state only). */
  onSuggest?: (text: string) => void;
  /** Disables the suggestion chips, e.g. while a message is in flight. */
  suggestDisabled?: boolean;
}

const SUGGESTIONS = [
  "Summarize what I've watched",
  "Quiz me on this so far",
  "Explain the last idea simply",
  "What's on screen right now?",
];

function Avatar() {
  return (
    <div
      className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-gradient-to-br from-violet-500 to-fuchsia-500 text-xs font-semibold text-white shadow-sm"
      aria-hidden="true"
    >
      M
    </div>
  );
}

function KindPill({ kind }: { kind?: MessageKind }) {
  if (kind === "grounded") {
    return (
      <span className="mb-1.5 inline-flex items-center gap-1 rounded-full border border-emerald-200 bg-emerald-50 px-2 py-0.5 text-[11px] font-medium text-emerald-700">
        <span className="h-1.5 w-1.5 rounded-full bg-emerald-500" aria-hidden="true" />
        From the lesson
      </span>
    );
  }
  if (kind === "supplemental") {
    return (
      <span className="mb-1.5 inline-flex items-center gap-1 rounded-full border border-amber-200 bg-amber-50 px-2 py-0.5 text-[11px] font-medium text-amber-700">
        <span className="h-1.5 w-1.5 rounded-full bg-amber-500" aria-hidden="true" />
        General knowledge · not from the video
      </span>
    );
  }
  return null;
}

function Citations({ cites, onSeek }: { cites: Citation[]; onSeek: (ms: number) => void }) {
  if (cites.length === 0) return null;
  return (
    <div className="mt-3 border-t border-gray-200/70 pt-2">
      <div className="mb-1.5 text-[10px] font-semibold uppercase tracking-wider text-gray-400">Sources</div>
      <div className="flex flex-wrap gap-1.5">
        {cites.map((c) => (
          <CitationBadge key={c.chunk_id} citation={c} onSeek={onSeek} />
        ))}
      </div>
    </div>
  );
}

export default function MessageList({ messages, loading, onSeek, onSuggest, suggestDisabled }: MessageListProps) {
  if (messages.length === 0 && !loading) {
    return (
      <div className="mt-3 rounded-xl border border-dashed border-gray-300 p-5 text-center text-sm">
        <p className="font-medium text-gray-700">Hi! I&apos;m Malai, your tutor for this lesson.</p>
        <p className="mt-1 text-xs text-gray-500">
          Ask me anything about what you&apos;ve watched so far — or try one of these:
        </p>
        {onSuggest && (
          <div className="mt-4 flex flex-wrap justify-center gap-2">
            {SUGGESTIONS.map((s) => (
              <button
                key={s}
                type="button"
                onClick={() => onSuggest(s)}
                disabled={suggestDisabled}
                className="rounded-full border border-violet-200 bg-violet-50 px-3 py-1.5 text-xs font-medium text-violet-700 transition hover:border-violet-300 hover:bg-violet-100 disabled:opacity-40"
              >
                {s}
              </button>
            ))}
          </div>
        )}
      </div>
    );
  }
  return (
    <div className="mt-3 flex max-h-[60vh] flex-col gap-3 overflow-y-auto pr-1" aria-live="polite">
      {messages.map((m) =>
        m.role === "user" ? (
          <div
            key={m.id}
            className="max-w-[85%] self-end rounded-2xl rounded-br-sm bg-violet-600 px-4 py-2.5 text-sm text-white"
          >
            <span className="sr-only">You: </span>
            {/* Both roles render as markdown: assistant answers carry the LaTeX,
                and a user can type it too. */}
            <MarkdownBody text={m.content} />
          </div>
        ) : (
          <div key={m.id} className="flex max-w-[90%] items-start gap-2 self-start">
            <Avatar />
            <div
              className={
                m.kind === "refused"
                  ? "min-w-0 rounded-2xl rounded-tl-sm border border-amber-200 bg-amber-50 px-4 py-2.5 text-sm text-gray-800"
                  : "min-w-0 rounded-2xl rounded-tl-sm border border-gray-200 bg-gray-50 px-4 py-2.5 text-sm text-gray-800"
              }
            >
              <span className="sr-only">Assistant: </span>
              <KindPill kind={m.kind} />
              <MarkdownBody text={m.content} />
              {/* Citations stay a SIBLING of the markdown tree, never inside it, so
                  the seek-on-click handlers survive markdown rendering. */}
              <Citations cites={m.citations} onSeek={onSeek} />
            </div>
          </div>
        ),
      )}
      {loading && (
        <div className="flex items-start gap-2 self-start" role="status">
          <Avatar />
          <div className="rounded-2xl rounded-tl-sm border border-gray-200 bg-gray-50 px-4 py-3">
            <span className="sr-only">Assistant: Thinking</span>
            <div className="flex items-center gap-1" aria-hidden="true">
              {[0, 150, 300].map((delay) => (
                <span
                  key={delay}
                  className="h-2 w-2 animate-bounce rounded-full bg-violet-400"
                  style={{ animationDelay: `${delay}ms` }}
                />
              ))}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
