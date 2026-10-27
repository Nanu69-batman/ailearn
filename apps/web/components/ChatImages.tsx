"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { deleteChatImage, formatApiError, uploadChatImage } from "@/lib/api";

/** The chat an image is attached to. */
export interface ChatImageTarget {
  sessionId: string;
  sessionToken: string;
  /** Undefined: the session's main chat. */
  threadId?: string;
}

export interface PendingImage {
  key: string;
  name: string;
  /** Object URL for the thumbnail; revoked when the chip goes. */
  preview: string;
  status: "uploading" | "ready" | "error";
  error?: string;
  id?: string;
  target?: ChatImageTarget;
}

const ACCEPT = "image/jpeg,image/png,image/webp,image/heic,image/heif";
const MAX_BYTES = 8 * 1024 * 1024;

/**
 * Images attached in the input bar. Each uploads as soon as it is picked
 * (POST /chat/attachments), so it is read by the time the question is sent;
 * once read it is part of the chat for every later answer. Never a resource.
 */
export function useChatImages(getTarget: () => Promise<ChatImageTarget | null>) {
  const [items, setItems] = useState<PendingImage[]>([]);
  const getRef = useRef(getTarget);
  getRef.current = getTarget;
  const itemsRef = useRef(items);
  itemsRef.current = items;

  const patch = (key: string, p: Partial<PendingImage>) =>
    setItems((prev) => prev.map((it) => (it.key === key ? { ...it, ...p } : it)));

  const add = useCallback(async (files: FileList | File[]) => {
    const list = Array.from(files).filter((f) => f.type.startsWith("image/"));
    if (list.length === 0) return;
    let target: ChatImageTarget | null = null;
    try {
      target = await getRef.current();
    } catch {
      target = null;
    }
    for (const file of list) {
      const key = `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
      const item: PendingImage = { key, name: file.name || "image", preview: URL.createObjectURL(file), status: "uploading" };
      setItems((prev) => [...prev, item]);
      if (!target) {
        patch(key, { status: "error", error: "Start the chat first, then attach." });
        continue;
      }
      if (file.size > MAX_BYTES) {
        patch(key, { status: "error", error: "Images are limited to 8 MB." });
        continue;
      }
      const t = target;
      uploadChatImage(t, file)
        .then((res) => patch(key, { status: "ready", id: res.id, target: t }))
        .catch((err) => patch(key, { status: "error", error: formatApiError(err) }));
    }
  }, []);

  const remove = useCallback((key: string) => {
    const it = itemsRef.current.find((x) => x.key === key);
    if (!it) return;
    URL.revokeObjectURL(it.preview);
    setItems((prev) => prev.filter((x) => x.key !== key));
    // Taken back before sending: out of the chat's context too.
    if (it.id && it.target) void deleteChatImage(it.target, it.id).catch(() => {});
  }, []);

  /** After sending: the chips go, the images stay in the chat. */
  const clear = useCallback(() => {
    for (const it of itemsRef.current) URL.revokeObjectURL(it.preview);
    setItems([]);
  }, []);

  useEffect(() => () => itemsRef.current.forEach((it) => URL.revokeObjectURL(it.preview)), []);

  const uploading = items.some((it) => it.status === "uploading");
  const readyNames = items.filter((it) => it.status === "ready").map((it) => it.name);
  return { items, add, remove, clear, uploading, readyNames };
}

/** "Attach an image" button with its hidden file picker. */
export function ImageButton({
  onFiles,
  disabled,
  size = "md",
}: {
  onFiles: (files: FileList) => void;
  disabled?: boolean;
  size?: "md" | "lg";
}) {
  const ref = useRef<HTMLInputElement>(null);
  const box = size === "lg" ? "h-12 w-12" : "h-10 w-10";
  return (
    <>
      <input
        ref={ref}
        type="file"
        accept={ACCEPT}
        multiple
        className="hidden"
        onChange={(e) => {
          if (e.target.files?.length) onFiles(e.target.files);
          e.target.value = "";
        }}
      />
      <button
        type="button"
        onClick={() => ref.current?.click()}
        disabled={disabled}
        aria-label="Attach an image"
        title="Attach an image: Malai reads it for this chat (it is not added to your resources)"
        className={`mb-0.5 flex ${box} shrink-0 items-center justify-center rounded-full text-gray-400 transition hover:bg-gray-100 hover:text-gray-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-violet-400 disabled:opacity-40`}
      >
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="h-5 w-5" aria-hidden="true">
          <rect x="3" y="3" width="18" height="18" rx="3" />
          <circle cx="8.5" cy="8.5" r="1.5" />
          <path d="M21 15l-5-5L5 21" />
        </svg>
      </button>
    </>
  );
}

/** The attached images above the input, each with its upload state. */
export function ImageChips({ items, onRemove }: { items: PendingImage[]; onRemove: (key: string) => void }) {
  if (items.length === 0) return null;
  return (
    <ul className="mb-2 flex flex-wrap gap-2 px-1" aria-label="Attached images">
      {items.map((it) => (
        <li
          key={it.key}
          title={it.error ?? it.name}
          className={`relative flex items-center gap-2 rounded-xl border bg-white py-1 pl-1 pr-7 text-xs shadow-sm ${
            it.status === "error" ? "border-red-200 text-red-600" : "border-gray-200 text-gray-700"
          }`}
        >
          {/* eslint-disable-next-line @next/next/no-img-element -- a local object URL */}
          <img src={it.preview} alt="" className="h-9 w-9 rounded-lg object-cover" />
          <span className="max-w-[9rem]">
            <span className="block truncate font-medium">{it.name}</span>
            <span className="block text-[11px] text-gray-400">
              {it.status === "uploading" ? "Reading…" : it.status === "ready" ? "Added to this chat" : it.error}
            </span>
          </span>
          {it.status === "uploading" && (
            <span className="h-3 w-3 animate-spin rounded-full border-2 border-violet-500 border-t-transparent" aria-label="Reading the image" />
          )}
          <button
            type="button"
            onClick={() => onRemove(it.key)}
            aria-label={`Remove ${it.name}`}
            className="absolute right-1 top-1 flex h-5 w-5 items-center justify-center rounded-full text-gray-400 hover:bg-gray-100 hover:text-gray-700"
          >
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" className="h-3 w-3" aria-hidden="true">
              <path d="M6 6l12 12M18 6L6 18" />
            </svg>
          </button>
        </li>
      ))}
    </ul>
  );
}

function hasFiles(e: React.DragEvent): boolean {
  return Array.from(e.dataTransfer?.types ?? []).includes("Files");
}

/**
 * Drop images anywhere on the chat. `bind` goes on the chat's container; the
 * counter keeps the overlay up while the pointer crosses child elements.
 */
export function useImageDrop(onFiles: (files: FileList) => void, enabled = true) {
  const [dragging, setDragging] = useState(false);
  const depth = useRef(0);
  const bind = {
    onDragEnter: (e: React.DragEvent) => {
      if (!enabled || !hasFiles(e)) return;
      e.preventDefault();
      depth.current += 1;
      setDragging(true);
    },
    onDragOver: (e: React.DragEvent) => {
      if (!enabled || !hasFiles(e)) return;
      e.preventDefault();
      e.dataTransfer.dropEffect = "copy";
    },
    onDragLeave: (e: React.DragEvent) => {
      if (!enabled || !hasFiles(e)) return;
      depth.current = Math.max(0, depth.current - 1);
      if (depth.current === 0) setDragging(false);
    },
    onDrop: (e: React.DragEvent) => {
      if (!enabled || !hasFiles(e)) return;
      e.preventDefault();
      depth.current = 0;
      setDragging(false);
      if (e.dataTransfer.files.length) onFiles(e.dataTransfer.files);
    },
  };
  return { dragging, bind };
}

/** Pasting a screenshot into the input attaches it. */
export function pastedImages(e: React.ClipboardEvent): File[] {
  return Array.from(e.clipboardData?.files ?? []).filter((f) => f.type.startsWith("image/"));
}

/** Shown over the chat while images are dragged onto it. */
export function DropOverlay({ show, fixed = false }: { show: boolean; fixed?: boolean }) {
  if (!show) return null;
  return (
    <div className={`pointer-events-none ${fixed ? "fixed" : "absolute"} inset-0 z-40 flex items-center justify-center rounded-[inherit] border-2 border-dashed border-violet-400 bg-violet-50/80 backdrop-blur-[1px]`}>
      <div className="flex flex-col items-center gap-2 text-violet-700">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" className="h-10 w-10" aria-hidden="true">
          <rect x="3" y="3" width="18" height="18" rx="3" />
          <circle cx="8.5" cy="8.5" r="1.5" />
          <path d="M21 15l-5-5L5 21" />
        </svg>
        <p className="text-sm font-semibold">Drop images to add them to this chat</p>
        <p className="text-xs text-violet-500">Malai reads them here; they are not added to your resources</p>
      </div>
    </div>
  );
}

/** The user's bubble notes which images went with the question. */
export function withImageNote(text: string, names: string[]): string {
  return names.length ? `${text}\n\n${names.map((n) => `📎 ${n}`).join("  ")}` : text;
}
