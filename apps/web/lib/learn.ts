/**
 * Shared, presentation-free helpers for the learning routes:
 *   /ai-tutor/subjects                                  — subject list
 *   /ai-tutor/subjects/[subjectId]                      — subject page (content list)
 *   /ai-tutor/subjects/[subjectId]/content/[resourceId] — player + chat + sources
 *
 * Kept out of the page components so the subject list, the subject page and the
 * player agree, byte for byte, on what a resource is playable as, how it is
 * labelled, and which URL it lives at. No React here on purpose — the pages
 * import this, and so does anything that only needs the URL helpers.
 */

import { pushCompletion } from "@/lib/activitySync";
import { resourceMediaUrl, type CourseResource } from "@/lib/api";

/** What the player can render for a given resource. */
export type Selected =
  | { kind: "file"; resourceId: string; src: string; title: string }
  | { kind: "pdf"; resourceId: string; src: string | null; title: string }
  | { kind: "pptx"; resourceId: string; src: string | null; title: string }
  | { kind: "image"; resourceId: string; src: string; title: string }
  | { kind: "youtube"; resourceId: string; youtubeId: string; title: string };

/**
 * The sample clip is not course content, so it has no row in the resource
 * list. It still gets a real route (`.../content/sample-clip`) because every
 * subject-page -> player transition must be a navigation, not component state,
 * for the browser back button to mean anything.
 */
export const SAMPLE_CLIP_ID = "sample-clip";

const FALLBACK_VIDEO =
  "https://interactive-examples.mdn.mozilla.net/media/cc0-videos/flower.mp4";

export function sampleClipSrc(): string {
  return process.env.NEXT_PUBLIC_VIDEO_URL || FALLBACK_VIDEO;
}

export function sampleClipSelected(): Selected {
  return {
    kind: "file",
    resourceId: SAMPLE_CLIP_ID,
    src: sampleClipSrc(),
    title: "Sample clip (not course content)",
  };
}

/**
 * Null when the resource cannot actually be played (a YouTube row with no
 * id, or a file whose asset is gone from disk). Callers must surface that as a
 * disabled row rather than pointing the player at a URL that would 404.
 */
export function toSelected(resource: CourseResource): Selected | null {
  const title = resource.title ?? resource.id;
  if (resource.provider === "youtube") {
    if (!resource.youtube_id) return null;
    return {
      kind: "youtube",
      resourceId: resource.id,
      youtubeId: resource.youtube_id,
      title,
    };
  }
  // Documents stay openable without their file: the chat still answers from
  // the indexed text, and the viewer says the original is gone.
  if (resource.type === "pdf" || resource.type === "pptx") {
    return {
      kind: resource.type,
      resourceId: resource.id,
      src: resource.asset_name
        ? resourceMediaUrl(resource.id, resource.asset_name)
        : null,
      title,
    };
  }
  // Only an asset that still exists on disk gets a real stream URL. Without
  // this check we would point the player at a 404 and the browser would show
  // the last playable source (i.e. the wrong video).
  if (!resource.asset_name) return null;
  if (resource.type === "image") {
    return {
      kind: "image",
      resourceId: resource.id,
      src: resourceMediaUrl(resource.id),
      title,
    };
  }
  return {
    kind: "file",
    resourceId: resource.id,
    src: resourceMediaUrl(resource.id),
    title,
  };
}

/** Honest label for an ingested resource — derived, never invented. */
export function resourceKindLabel(resource: CourseResource): string {
  const kind =
    resource.type === "pdf"
      ? "PDF"
      : resource.type === "pptx"
        ? "Slides"
        : resource.type === "video"
          ? "Video"
          : resource.type;
  if (!kind) return "Unknown type";
  return resource.provider === "youtube" ? `${kind} · YouTube` : kind;
}

export function resourceIcon(resource: CourseResource): string {
  if (resource.provider === "youtube") return "📺";
  if (resource.type === "image") return "🖼️";
  if (resource.type === "pptx") return "📊";
  return resource.type === "pdf" ? "📄" : "▶";
}

/** Subjects overview. The landing page's only call to action. */
export const DASHBOARD_PATH = "/dashboard";

/** "My subjects" sidebar target: the list of subjects. */
export const SUBJECTS_PATH = "/ai-tutor/subjects";

/** The demo learner. There is no account system, so this is the one name shown. */
export const LEARNER_NAME = "Nimish";

export function subjectPath(subjectId: string): string {
  return `${SUBJECTS_PATH}/${encodeURIComponent(subjectId)}`;
}

export function contentPath(subjectId: string, resourceId: string): string {
  return `${subjectPath(subjectId)}/content/${encodeURIComponent(resourceId)}`;
}

/** Normalises a path segment, tolerating Next's string[] params. */
export function firstParam(value: string | string[] | undefined): string | null {
  if (Array.isArray(value)) return value[0] ?? null;
  return value ?? null;
}

// ---------------------------------------------------------------------------
// Chat layout preference. Persisted across reloads; forced to "bottom" below
// the `lg` breakpoint where a right-hand column would squeeze the player.
// ---------------------------------------------------------------------------

export type ChatLayout = "bottom" | "right";

export const CHAT_LAYOUT_KEY = "ailearn.chat_layout";

export const CHAT_LAYOUT_OPTIONS: ReadonlyArray<{
  value: ChatLayout;
  label: string;
  hint: string;
}> = [
  { value: "bottom", label: "Bottom", hint: "Chat below the player" },
  { value: "right", label: "Right", hint: "Chat in a right-hand sidebar" },
];

export function readChatLayout(): ChatLayout {
  try {
    return window.localStorage.getItem(CHAT_LAYOUT_KEY) === "right"
      ? "right"
      : "bottom";
  } catch {
    return "bottom";
  }
}

export function writeChatLayout(value: ChatLayout): void {
  try {
    window.localStorage.setItem(CHAT_LAYOUT_KEY, value);
  } catch {
    // Storage unavailable -> keep in-memory only.
  }
}

// ---------------------------------------------------------------------------
// Per-course completion, user-controlled only.
// ---------------------------------------------------------------------------

function completionKey(courseId: string): string {
  return `ailearn.course.${courseId}.completion`;
}

export function readCompletion(courseId: string): string[] {
  try {
    const raw = window.localStorage.getItem(completionKey(courseId));
    if (!raw) return [];
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.filter((v): v is string => typeof v === "string");
  } catch {
    // Storage unavailable or corrupt value -> start from "nothing done".
    return [];
  }
}

export function writeCompletion(courseId: string, ids: string[]): void {
  const before = new Set(readCompletion(courseId));
  const after = new Set(ids);
  writeCompletionLocal(courseId, ids);
  // Only the ticks that changed go to the account.
  void pushCompletion(courseId, [
    ...ids.filter((id) => !before.has(id)).map((resourceId) => ({ resourceId, completed: true })),
    ...[...before].filter((id) => !after.has(id)).map((resourceId) => ({ resourceId, completed: false })),
  ]);
}

/** Write completion to this browser only (used when syncing from the account). */
export function writeCompletionLocal(courseId: string, ids: string[]): void {
  try {
    window.localStorage.setItem(completionKey(courseId), JSON.stringify(ids));
  } catch {
    // Storage unavailable (private mode / quota) -> keep in-memory only.
  }
}