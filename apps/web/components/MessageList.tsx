"use client";

import type { ChatMessage, Citation } from "../lib/api";
import CitationBadge from "./CitationBadge";

interface MessageListProps {
  messages: ChatMessage[];
  loading: boolean;
  onSeek: (startMs: number) => void;
}

function Citations({ cites, onSeek }: { cites: Citation[]; onSeek: (ms: number) => void }) {
  if (cites.length === 0) return null;
  return (
    <div className="mt-3 flex flex-wrap gap-1.5">
      {cites.map((c) => (
        <CitationBadge key={c.chunk_id} citation={c} onSeek={onSeek} />
      ))}
    </div>
  );
}

export default function MessageList({ messages, loading, onSeek }: MessageListProps) {
  if (messages.length === 0 && !loading) {
    return (
      <div className="rounded-xl border border-dashed border-gray-300 p-6 text-center text-sm text-gray-400" role="status">
        <p className="font-medium text-gray-700">No messages yet.</p>
        <p className="mt-1 text-xs text-gray-400">
          Pause the video and ask your first question — answers stay grounded in
          what you have watched so far.
        </p>
      </div>
    );
  }
  return (
    <div className="mt-3 flex max-h-[60vh] flex-col gap-3 overflow-y-auto pr-1" aria-live="polite">
      {messages.map((m) => (
        <div
          key={m.id}
          className={
            m.role === "user"
              ? "self-end rounded-2xl rounded-br-sm bg-violet-600 px-4 py-2.5 text-sm text-white max-w-[85%]"
              : "self-start rounded-2xl rounded-bl-sm border border-gray-200 bg-gray-50 px-4 py-2.5 text-sm text-gray-800 max-w-[85%]"
          }
        >
          <div className={`mb-1 text-[10px] font-semibold uppercase tracking-wider ${m.role === "user" ? "text-violet-200" : "text-gray-400"}`}>
            {m.role === "user" ? "You" : "Assistant"}
          </div>
          <div className="whitespace-pre-wrap">{m.content}</div>
          {m.role === "assistant" && <Citations cites={m.citations} onSeek={onSeek} />}
        </div>
      ))}
      {loading && (
        <div className="self-start rounded-2xl rounded-bl-sm border border-gray-200 bg-gray-50 px-4 py-2.5 text-sm text-gray-400" role="status">
          <div className="mb-1 text-[10px] font-semibold uppercase tracking-wider text-gray-400">Assistant</div>
          <div className="animate-pulse">Thinking…</div>
        </div>
      )}
    </div>
  );
}
