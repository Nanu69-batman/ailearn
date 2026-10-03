"use client";

import type { ChatMessage, Citation } from "../lib/api";
import CitationBadge from "./CitationBadge";
import styles from "./MessageList.module.css";

interface MessageListProps {
  messages: ChatMessage[];
  loading: boolean;
  onSeek: (startMs: number) => void;
}

function Citations({ cites, onSeek }: { cites: Citation[]; onSeek: (ms: number) => void }) {
  if (cites.length === 0) return null;
  return (
    <div className={styles.cites}>
      {cites.map((c) => (
        <CitationBadge key={c.chunk_id} citation={c} onSeek={onSeek} />
      ))}
    </div>
  );
}

export default function MessageList({ messages, loading, onSeek }: MessageListProps) {
  if (messages.length === 0 && !loading) {
    return (
      <div className={styles.empty} role="status">
        <p>No messages yet.</p>
        <p className={styles.emptyHint}>
          Pause the video and ask your first question — answers stay grounded in
          what you have watched so far.
        </p>
      </div>
    );
  }
  return (
    <div className={styles.list} aria-live="polite">
      {messages.map((m) => (
        <div
          key={m.id}
          className={`${styles.msg} ${m.role === "user" ? styles.user : styles.assistant}`}
        >
          <div className={styles.role}>{m.role === "user" ? "You" : "Assistant"}</div>
          <div className={styles.body}>{m.content}</div>
          {m.role === "assistant" && <Citations cites={m.citations} onSeek={onSeek} />}
        </div>
      ))}
      {loading && (
        <div className={`${styles.msg} ${styles.assistant}`} role="status">
          <div className={styles.role}>Assistant</div>
          <div className={styles.typing}>Thinking…</div>
        </div>
      )}
    </div>
  );
}
