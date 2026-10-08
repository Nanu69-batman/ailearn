"use client";

import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { useParams, useRouter } from "next/navigation";
import VideoPlayer from "@/components/VideoPlayer";
import YouTubePlayer, {
  type YouTubePlayerHandle,
} from "@/components/YouTubePlayer";
import YouTubeIngestForm, {
  type IngestedVideo,
} from "@/components/YouTubeIngestForm";
import ChatPanel from "@/components/ChatPanel";
import SessionBootstrap from "@/components/SessionBootstrap";
import {
  formatApiError,
  getCourseResources,
  isApiConfigured,
  resourceMediaUrl,
  type CourseResource,
  type StoredSession,
} from "@/lib/api";

const FALLBACK_VIDEO =
  "https://interactive-examples.mdn.mozilla.net/media/cc0-videos/flower.mp4";

type Selected =
  | { kind: "file"; resourceId: string; src: string; title: string }
  | { kind: "pdf"; resourceId: string; title: string }
  | { kind: "youtube"; resourceId: string; youtubeId: string; title: string };

function toSelected(resource: CourseResource): Selected | null {
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
  if (resource.type === "pdf") {
    return { kind: "pdf", resourceId: resource.id, title };
  }
  // Only an asset that still exists on disk gets a real stream URL. Without
  // this check we would point the player at a 404 and the browser would show
  // the last playable source (i.e. the wrong video).
  if (!resource.asset_name) return null;
  return {
    kind: "file",
    resourceId: resource.id,
    src: resourceMediaUrl(resource.id),
    title,
  };
}

/**
 * Scope honesty, not decoration.
 *
 * "AI Tutor" is the only entry point that resolves to a real screen — it is
 * this page. Every other entry is a planned destination with no route behind
 * it, so rather than letting a demo look like a finished product we mark each
 * one with a muted "Soon" tag and `aria-disabled`. A viewer should never have
 * to guess which screens actually exist.
 *
 * Keep `implemented: false` until the item has a real destination.
 */
type NavItem = {
  label: string;
  icon: string;
  /** True only when the item leads somewhere real. */
  implemented: boolean;
  /** Marks the current screen. */
  active?: boolean;
};

const NOT_AVAILABLE_SUFFIX = "— not yet available";

const NAV_ITEMS: ReadonlyArray<NavItem> = [
  { label: "Dashboard", icon: "M3 12l9-9 9 9M5 10v10h5v-6h4v6h5V10", implemented: false },
  { label: "My Courses", icon: "M4 19.5A2.5 2.5 0 016.5 17H20V4H6.5A2.5 2.5 0 004 6.5v13zM4 19.5A2.5 2.5 0 006.5 22H20v-5", implemented: false },
  { label: "AI Tutor", icon: "M12 2a7 7 0 00-7 7c0 2.4 1.2 4.5 3 5.7V17a2 2 0 002 2h4a2 2 0 002-2v-2.3c1.8-1.2 3-3.3 3-5.7a7 7 0 00-7-7zM9 22h6", implemented: true, active: true },
  { label: "Practice", icon: "M9 11l3 3L22 4M21 12v7a2 2 0 01-2 2H5a2 2 0 01-2-2V5a2 2 0 012-2h11", implemented: false },
  { label: "Progress", icon: "M3 3v18h18M7 14l4-4 4 4 5-6", implemented: false },
  { label: "Resources", icon: "M4 4h16v16H4zM4 9h16M9 4v5", implemented: false },
];

const SIDEBAR_BOTTOM: ReadonlyArray<NavItem> = [
  { label: "Calendar", icon: "M8 2v4M16 2v4M3 9h18M5 4h14a2 2 0 012 2v14a2 2 0 01-2 2H5a2 2 0 01-2-2V6a2 2 0 012-2z", implemented: false },
  { label: "Notifications", icon: "M18 8a6 6 0 10-12 0c0 7-3 9-3 9h18s-3-2-3-9M13.7 21a2 2 0 01-3.4 0", implemented: false },
  { label: "Settings", icon: "M12 15a3 3 0 100-6 3 3 0 000 6zM19.4 15a1.65 1.65 0 00.33 1.82l.06.06a2 2 0 11-2.83 2.83l-.06-.06a1.65 1.65 0 00-1.82-.33 1.65 1.65 0 00-1 1.51V21a2 2 0 11-4 0v-.09a1.65 1.65 0 00-1-1.51 1.65 1.65 0 00-1.82.33l-.06.06a2 2 0 11-2.83-2.83l.06-.06a1.65 1.65 0 00.33-1.82 1.65 1.65 0 00-1.51-1H3a2 2 0 110-4h.09a1.65 1.65 0 001.51-1 1.65 1.65 0 00-.33-1.82l-.06-.06a2 2 0 112.83-2.83l.06.06a1.65 1.65 0 001.82.33h.01a1.65 1.65 0 001-1.51V3a2 2 0 114 0v.09a1.65 1.65 0 001 1.51h.01a1.65 1.65 0 001.82-.33l.06-.06a2 2 0 112.83 2.83l-.06.06a1.65 1.65 0 00-.33 1.82v.01a1.65 1.65 0 001.51 1H21a2 2 0 110 4h-.09a1.65 1.65 0 00-1.51 1z", implemented: false },
];

/** Collapsed icon rail vs. the default 15rem sidebar. */
const SIDEBAR_ID = "learn-sidebar";
const SIDEBAR_RAIL_CLASS = "w-14";
const SIDEBAR_EXPANDED_CLASS = "w-60";
const SIDEBAR_COLLAPSED_KEY = "ailearn.sidebar_collapsed";
/** Small-screen drawer. Independent of the desktop rail state on purpose. */
const DRAWER_ID = "learn-mobile-drawer";

function navAriaLabel(item: NavItem): string {
  return item.implemented ? item.label : `${item.label} ${NOT_AVAILABLE_SUFFIX}`;
}

function SideIcon({ d }: { d: string }) {
  // shrink-0: in the 3.5rem rail the flex parent is narrower than the icon's
  // natural size, and without this the SVG gets squished into a sliver.
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" className="h-5 w-5 shrink-0" aria-hidden="true">
      <path d={d} />
    </svg>
  );
}

/** Sidebar rail preference. Default expanded; restored after mount. */
function readSidebarCollapsed(): boolean {
  try {
    return window.localStorage.getItem(SIDEBAR_COLLAPSED_KEY) === "1";
  } catch {
    return false;
  }
}

function writeSidebarCollapsed(value: boolean): void {
  try {
    window.localStorage.setItem(SIDEBAR_COLLAPSED_KEY, value ? "1" : "0");
  } catch {
    // Storage unavailable -> keep in-memory only.
  }
}

/**
 * Chat layout preference. Persisted across reloads; forced to "bottom" below
 * the `lg` breakpoint where a right-hand column would squeeze the player.
 */
type ChatLayout = "bottom" | "right";

const CHAT_LAYOUT_KEY = "ailearn.chat_layout";
const CHAT_LAYOUT_OPTIONS: ReadonlyArray<{
  value: ChatLayout;
  label: string;
  hint: string;
}> = [
  { value: "bottom", label: "Bottom", hint: "Chat below the player" },
  { value: "right", label: "Right", hint: "Chat in a right-hand sidebar" },
];

/** Per-course completion, user-controlled only. */
function completionKey(courseId: string): string {
  return `ailearn.course.${courseId}.completion`;
}

function readCompletion(courseId: string): string[] {
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

function writeCompletion(courseId: string, ids: string[]): void {
  try {
    window.localStorage.setItem(completionKey(courseId), JSON.stringify(ids));
  } catch {
    // Storage unavailable (private mode / quota) -> keep in-memory only.
  }
}

function readChatLayout(): ChatLayout {
  try {
    const v = window.localStorage.getItem(CHAT_LAYOUT_KEY);
    return v === "right" ? "right" : "bottom";
  } catch {
    return "bottom";
  }
}

function writeChatLayout(value: ChatLayout): void {
  try {
    window.localStorage.setItem(CHAT_LAYOUT_KEY, value);
  } catch {
    // Storage unavailable -> keep in-memory only.
  }
}

/** Honest label for an ingested resource — derived, never invented. */
function resourceKindLabel(resource: CourseResource): string {
  const kind =
    resource.type === "pdf"
      ? "PDF"
      : resource.type === "video"
        ? "Video"
        : resource.type;
  if (!kind) return "Unknown type";
  return resource.provider === "youtube" ? `${kind} · YouTube` : kind;
}

export default function LearnPage() {
  const params = useParams<{ courseId: string }>();
  const router = useRouter();
  const courseId = Array.isArray(params.courseId)
    ? params.courseId[0]
    : (params.courseId ?? "demo-course");
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const ytHandleRef = useRef<YouTubePlayerHandle | null>(null);
  const [session, setSession] = useState<StoredSession | null>(null);
  const [offline, setOffline] = useState(false);
  const [pausedAtMs, setPausedAtMs] = useState(0);
  const [selected, setSelected] = useState<Selected | null>(null);
  const [resources, setResources] = useState<CourseResource[]>([]);
  const [resourcesState, setResourcesState] = useState<
    "idle" | "loading" | "ready" | "error"
  >("idle");
  const [resourcesError, setResourcesError] = useState<string | null>(null);
  const [chatLayoutPref, setChatLayoutPref] = useState<ChatLayout>("bottom");
  const [isWide, setIsWide] = useState(false);
  /**
   * Starts false so the server-rendered HTML and the first client render agree;
   * localStorage is only read in the effect below, otherwise React would log a
   * hydration mismatch on any client that had collapsed the sidebar.
   */
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  /**
   * Small-screen nav drawer. Deliberately separate from `sidebarCollapsed`:
   * below `md` the persistent sidebar is `hidden`, so this is the only way to
   * reach the nav on a phone. At `md` and up this is ignored entirely.
   */
  const [drawerOpen, setDrawerOpen] = useState(false);
  const drawerRef = useRef<HTMLDivElement | null>(null);
  const menuButtonRef = useRef<HTMLButtonElement | null>(null);
  /** Resource ids the user explicitly ticked. Nothing else marks work done. */
  const [completedIds, setCompletedIds] = useState<string[]>([]);

  const fileSrc = process.env.NEXT_PUBLIC_VIDEO_URL || FALLBACK_VIDEO;
  const apiLive = isApiConfigured() && !offline;
  // Mobile always uses the bottom layout, whatever was persisted.
  const chatLayout: ChatLayout = isWide ? chatLayoutPref : "bottom";
  const activeResourceId = selected ? selected.resourceId : null;

  useEffect(() => {
    setChatLayoutPref(readChatLayout());
  }, []);

  useEffect(() => {
    setSidebarCollapsed(readSidebarCollapsed());
  }, []);

  const closeDrawer = useCallback((): void => {
    setDrawerOpen(false);
  }, []);

  const openDrawer = useCallback((): void => {
    setDrawerOpen(true);
  }, []);

  // Focus moves into the drawer on open and returns to the hamburger on close,
  // so keyboard users are never stranded on <body>.
  const drawerWasOpenRef = useRef(false);
  useEffect(() => {
    if (drawerOpen) {
      drawerWasOpenRef.current = true;
      const node = drawerRef.current;
      const first = node?.querySelector<HTMLElement>("[data-drawer-autofocus]");
      (first ?? node)?.focus();
      return;
    }
    // Only on an open -> closed transition; never on mount, which would steal
    // focus from the page for every keyboard user who did not open the drawer.
    if (drawerWasOpenRef.current) {
      drawerWasOpenRef.current = false;
      menuButtonRef.current?.focus();
    }
  }, [drawerOpen]);

  // Escape closes, and Tab is kept inside the drawer while it is open.
  useEffect(() => {
    if (!drawerOpen) return;
    function onKeyDown(event: KeyboardEvent): void {
      if (event.key === "Escape") {
        event.preventDefault();
        closeDrawer();
        return;
      }
      if (event.key !== "Tab") return;
      const root = drawerRef.current;
      if (!root) return;
      const focusable = Array.from(
        root.querySelectorAll<HTMLElement>(
          'a[href], button:not([disabled]), [tabindex]:not([tabindex="-1"])',
        ),
      ).filter((el) => el.offsetParent !== null);
      if (focusable.length === 0) {
        event.preventDefault();
        root.focus();
        return;
      }
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      const active = document.activeElement;
      if (event.shiftKey && (active === first || !root.contains(active))) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && active === last) {
        event.preventDefault();
        first.focus();
      }
    }
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [drawerOpen, closeDrawer]);

  // Scroll lock released in the cleanup so an unmount while open can never
  // leave the page permanently unscrollable.
  useEffect(() => {
    if (!drawerOpen) return;
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = previous;
    };
  }, [drawerOpen]);

  useEffect(() => {
    if (typeof window.matchMedia !== "function") return;
    const mq = window.matchMedia("(min-width: 1024px)");
    const sync = (): void => setIsWide(mq.matches);
    sync();
    mq.addEventListener("change", sync);
    return () => mq.removeEventListener("change", sync);
  }, []);

  useEffect(() => {
    setCompletedIds(readCompletion(courseId));
  }, [courseId]);

  const chooseChatLayout = useCallback((value: ChatLayout): void => {
    setChatLayoutPref(value);
    writeChatLayout(value);
  }, []);

  const toggleSidebar = useCallback((): void => {
    setSidebarCollapsed((prev) => {
      writeSidebarCollapsed(!prev);
      return !prev;
    });
  }, []);

  const toggleCompleted = useCallback(
    (resourceId: string): void => {
      setCompletedIds((prev) => {
        const next = prev.includes(resourceId)
          ? prev.filter((id) => id !== resourceId)
          : [...prev, resourceId];
        writeCompletion(courseId, next);
        return next;
      });
    },
    [courseId],
  );

  useEffect(() => {
    if (!session || !apiLive) return;
    let cancelled = false;
    async function load(): Promise<void> {
      setResourcesState("loading");
      setResourcesError(null);
      try {
        const res = await getCourseResources(courseId);
        if (!cancelled) {
          setResources(res.resources ?? []);
          setResourcesState("ready");
        }
      } catch (err) {
        if (!cancelled) {
          setResourcesError(formatApiError(err));
          setResourcesState("error");
        }
      }
    }
    void load();
    return () => {
      cancelled = true;
    };
  }, [session, apiLive, courseId]);

  const getCurrentTimeMs = useCallback((): number => {
    if (selected?.kind === "youtube") {
      return ytHandleRef.current?.getCurrentTimeMs() ?? pausedAtMs;
    }
    const el = videoRef.current;
    if (!el) return pausedAtMs;
    return Math.max(0, Math.floor(el.currentTime * 1000));
  }, [pausedAtMs, selected]);

  const handleSeek = useCallback(
    (startMs: number): void => {
      if (!Number.isFinite(startMs)) return;
      if (selected?.kind === "youtube") {
        ytHandleRef.current?.seekTo(startMs);
        return;
      }
      const el = videoRef.current;
      if (!el) return;
      el.currentTime = Math.max(0, startMs / 1000);
      el.focus();
    },
    [selected],
  );

  const handleReady = useCallback((s: StoredSession, isOffline: boolean): void => {
    setSession(s);
    setOffline(isOffline);
  }, []);

  const handleRejoin = useCallback((): void => {
    setSession(null);
    setOffline(false);
    setSelected(null);
  }, []);

  const handleIngested = useCallback((video: IngestedVideo): void => {
    setSelected({
      kind: "youtube",
      resourceId: video.resourceId,
      youtubeId: video.youtubeId,
      title: video.title ?? video.youtubeId,
    });
  }, []);

  // Single renderer for both nav groups so the "Soon" tag and the disabled
  // semantics can never drift apart between primary and secondary lists.
  // `collapsed` is passed in rather than read from state so the mobile drawer
  // can force the expanded variant without mutating the persisted desktop flag.
  const renderNavItem = (item: NavItem, collapsed: boolean): ReactNode => {
    const unavailable = !item.implemented;
    const tone = item.active
      ? "bg-violet-600/90 text-white shadow"
      : "text-gray-400 hover:bg-white/5 hover:text-white";
    const layout = collapsed
      ? "justify-center px-2 py-2.5"
      : "gap-3 px-3 py-2.5";
    return (
      <a
        key={item.label}
        href="#"
        onClick={(e) => e.preventDefault()}
        // Kept focusable (not a dead div) so keyboard users can still discover
        // that the item exists, but announced as unavailable instead of working.
        aria-disabled={unavailable || undefined}
        aria-label={navAriaLabel(item)}
        title={navAriaLabel(item)}
        className={[
          "flex items-center rounded-xl text-sm transition",
          layout,
          // Non-functional items must not read as clickable.
          unavailable && !item.active ? "cursor-default" : "cursor-pointer",
          tone,
        ].join(" ")}
      >
        <SideIcon d={item.icon} />
        {/* Rail mode hides all text so the tag can never push the icon out. */}
        {!collapsed && (
          <>
            <span className="truncate">{item.label}</span>
            {unavailable && (
              // Secondary metadata, deliberately slate rather than violet so it
              // never competes with the label or with the active item.
              <span
                aria-hidden="true"
                className="ml-auto shrink-0 rounded-full border border-white/10 px-1.5 py-px text-[10px] font-medium uppercase tracking-wide text-gray-500"
              >
                Soon
              </span>
            )}
          </>
        )}
      </a>
    );
  };

  return (
    <div className="min-h-screen bg-gray-50 text-gray-800">
      <div className="flex">
        {/* Left sidebar. Still `hidden md:flex` — the rail state only applies
            once the sidebar is visible at md and up. */}
        <aside
          id={SIDEBAR_ID}
          className={[
            "hidden min-h-screen shrink-0 flex-col bg-[#1a1f3a] text-gray-300",
            "transition-[width] duration-200 md:flex",
            sidebarCollapsed ? SIDEBAR_RAIL_CLASS : SIDEBAR_EXPANDED_CLASS,
          ].join(" ")}
        >
          <div
            className={
              sidebarCollapsed
                ? "flex items-center justify-center px-2 py-6"
                : "flex items-center gap-3 px-5 py-6"
            }
          >
            <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-gradient-to-br from-indigo-500 to-violet-500 text-lg font-bold text-white">
              M
            </span>
            {!sidebarCollapsed && (
              <div className="min-w-0">
                <p className="text-sm font-bold leading-tight text-white">Malai Pioneer</p>
                <p className="text-[11px] text-gray-400">Learning</p>
              </div>
            )}
          </div>

          <div
            className={
              sidebarCollapsed ? "flex justify-center px-2 pb-2" : "flex justify-end px-3 pb-2"
            }
          >
            <button
              type="button"
              onClick={toggleSidebar}
              aria-expanded={!sidebarCollapsed}
              aria-controls={SIDEBAR_ID}
              aria-label={sidebarCollapsed ? "Expand sidebar" : "Collapse sidebar"}
              title={sidebarCollapsed ? "Expand sidebar" : "Collapse sidebar"}
              className="rounded-lg p-1.5 text-gray-400 transition hover:bg-white/5 hover:text-white focus:outline-none focus-visible:ring-2 focus-visible:ring-violet-400"
            >
              <svg
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
                strokeLinejoin="round"
                className="h-4 w-4 shrink-0"
                aria-hidden="true"
              >
                <path d={sidebarCollapsed ? "M9 6l6 6-6 6" : "M15 6l-6 6 6 6"} />
              </svg>
            </button>
          </div>

          <nav
            className={sidebarCollapsed ? "flex flex-col gap-1 px-2" : "flex flex-col gap-1 px-3"}
            aria-label="Primary"
          >
            {NAV_ITEMS.map((item) => renderNavItem(item, sidebarCollapsed))}
          </nav>
          <div className="mx-5 my-4 border-t border-white/10" />
          <nav
            className={sidebarCollapsed ? "flex flex-col gap-1 px-2" : "flex flex-col gap-1 px-3"}
            aria-label="Secondary"
          >
            {SIDEBAR_BOTTOM.map((item) => renderNavItem(item, sidebarCollapsed))}
          </nav>
        </aside>

        {/* Small-screen-only drawer. `md:hidden` means the persistent sidebar
            above is untouched at `md` and up; this is an addition, never a
            replacement. Stays mounted so the slide animates both ways, and is
            hidden from AT while closed. */}
        <div className="fixed inset-0 z-40 md:hidden" aria-hidden={!drawerOpen}>
          <div
            onClick={closeDrawer}
            className={[
              "absolute inset-0 bg-gray-900/50 transition-opacity duration-200 motion-reduce:transition-none",
              drawerOpen
                ? "pointer-events-auto opacity-100"
                : "pointer-events-none opacity-0",
            ].join(" ")}
          />
          <div
            ref={drawerRef}
            id={DRAWER_ID}
            role="dialog"
            aria-modal={drawerOpen ? true : undefined}
            aria-label="Navigation"
            tabIndex={-1}
            onClick={(e) => e.stopPropagation()}
            className={[
              "absolute inset-y-0 left-0 flex w-72 max-w-[85vw] flex-col overflow-y-auto",
              "bg-[#1a1f3a] text-gray-300 shadow-xl outline-none",
              "transition-transform duration-200 motion-reduce:transition-none",
              drawerOpen
                ? "pointer-events-auto visible translate-x-0"
                : "pointer-events-none invisible -translate-x-full",
            ].join(" ")}
          >
            <div className="flex items-center justify-between gap-3 px-5 py-6">
              <div className="flex min-w-0 items-center gap-3">
                <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-gradient-to-br from-indigo-500 to-violet-500 text-lg font-bold text-white">
                  M
                </span>
                <div className="min-w-0">
                  <p className="text-sm font-bold leading-tight text-white">
                    Malai Pioneer
                  </p>
                  <p className="text-[11px] text-gray-400">Learning</p>
                </div>
              </div>
              <button
                data-drawer-autofocus
                type="button"
                onClick={closeDrawer}
                aria-label="Close navigation menu"
                title="Close navigation menu"
                className="rounded-lg p-1.5 text-gray-400 transition hover:bg-white/5 hover:text-white focus:outline-none focus-visible:ring-2 focus-visible:ring-violet-400"
              >
                <svg
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2"
                  strokeLinecap="round"
                  className="h-5 w-5 shrink-0"
                  aria-hidden="true"
                >
                  <path d="M18 6L6 18M6 6l12 12" />
                </svg>
              </button>
            </div>

            {/* Always the expanded variant: a 3.5rem icon rail inside a phone
                drawer is unusable, and the desktop preference is persisted
                state we must not mutate from here. */}
            <nav
              className="flex flex-col gap-1 px-3"
              aria-label="Primary"
              onClick={closeDrawer}
            >
              {NAV_ITEMS.map((item) => renderNavItem(item, false))}
            </nav>
            <div className="mx-5 my-4 border-t border-white/10" />
            <nav
              className="flex flex-col gap-1 px-3"
              aria-label="Secondary"
              onClick={closeDrawer}
            >
              {SIDEBAR_BOTTOM.map((item) => renderNavItem(item, false))}
            </nav>
          </div>
        </div>

        <div className="flex min-w-0 flex-1 flex-col">
          {/* Top bar */}
          <header className="sticky top-0 z-10 flex items-center justify-between gap-4 border-b border-gray-200 bg-white px-4 py-3 sm:px-6">
            <div className="flex items-center gap-3">
              {/* Below `md` the sidebar is `hidden`, so this is the only way to
                  reach the nav on a phone. Hidden from `md` up, where the
                  persistent sidebar is always visible. */}
              <button
                ref={menuButtonRef}
                type="button"
                onClick={openDrawer}
                aria-expanded={drawerOpen}
                aria-controls={DRAWER_ID}
                aria-label="Open navigation menu"
                title="Open navigation menu"
                className="rounded-lg border border-gray-200 bg-white p-2 text-gray-600 shadow-sm transition hover:border-violet-300 hover:bg-gray-50 hover:text-gray-900 focus:outline-none focus-visible:ring-2 focus-visible:ring-violet-400 md:hidden"
              >
                <svg
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2"
                  strokeLinecap="round"
                  className="h-5 w-5 shrink-0"
                  aria-hidden="true"
                >
                  <path d="M3 6h18M3 12h18M3 18h18" />
                </svg>
              </button>
              <button
                type="button"
                onClick={() => router.push("/")}
                className="rounded-lg border border-gray-200 bg-white px-3 py-1.5 text-sm font-medium text-gray-600 shadow-sm transition hover:border-violet-300 hover:bg-gray-50 hover:text-gray-900"
              >
                ← Back to home
              </button>
              <span className="flex h-9 w-9 items-center justify-center rounded-xl bg-gradient-to-br from-indigo-500 to-violet-500 text-base font-bold text-white md:hidden">
                M
              </span>
              <h1 className="text-lg font-bold text-gray-900">Malai Pioneer</h1>
            </div>
            <div className="hidden flex-1 justify-center sm:flex">
              <label className="sr-only" htmlFor="top-search">Search</label>
              <input
                id="top-search"
                type="search"
                placeholder="Search courses, topics, notes…"
                className="w-full max-w-md rounded-full border border-gray-200 bg-gray-100 px-4 py-2 text-sm text-gray-800 placeholder-gray-400 outline-none transition focus:border-violet-400 focus:bg-white focus:ring-2 focus:ring-violet-200"
              />
            </div>
            <div className="flex items-center gap-4">
              <button type="button" aria-label="Notifications" className="relative rounded-full p-2 text-gray-500 transition hover:bg-gray-100 hover:text-gray-800">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" className="h-5 w-5">
                  <path d="M18 8a6 6 0 10-12 0c0 7-3 9-3 9h18s-3-2-3-9M13.7 21a2 2 0 01-3.4 0" />
                </svg>
                <span className="absolute right-1.5 top-1.5 h-2 w-2 rounded-full bg-violet-500" />
              </button>
              <div className="flex items-center gap-2">
                <span className="flex h-9 w-9 items-center justify-center rounded-full bg-violet-100 text-sm font-bold text-violet-700">N</span>
                <span className="hidden text-sm font-medium text-gray-800 sm:inline">Nimish</span>
              </div>
            </div>
          </header>

          <main className="flex-1 px-4 py-6 sm:px-6">
            {!session ? (
              <SessionBootstrap courseId={courseId} onReady={handleReady} />
            ) : !selected ? (
              <div className="flex flex-col gap-6">
                <YouTubeIngestForm
                  courseId={courseId}
                  onReady={handleIngested}
                  disabled={!apiLive}
                />
                {!apiLive && (
                  <p className="text-sm text-amber-600" role="note">
                    YouTube ingest needs the live API — offline demo uses the file
                    video below.
                  </p>
                )}

                <section aria-label="Course videos">
                  <h2 className="mb-4 text-lg font-semibold text-gray-900">Course videos</h2>
                  {resourcesState === "loading" && (
                    <div className="rounded-xl border border-gray-200 bg-white p-4 text-sm text-gray-500 shadow-sm" role="status">
                      Loading course videos…
                    </div>
                  )}
                  {resourcesState === "error" && (
                    <div className="rounded-xl border border-red-200 bg-red-50 p-4 text-sm text-red-600 shadow-sm" role="alert">
                      Could not load course videos: {resourcesError}
                    </div>
                  )}
                  {(resourcesState === "ready" || resourcesState === "idle") &&
                    resources.length === 0 &&
                    resourcesState === "ready" && (
                      <div className="rounded-xl border border-gray-200 bg-white p-4 text-sm text-gray-500 shadow-sm" role="status">
                        No videos in this course yet — paste a YouTube link above to
                        add the first one.
                      </div>
                    )}
                  {resources.length > 0 && (
                    <ul className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
                      {resources.map((r) => {
                        const s = toSelected(r);
                        if (!s) return null;
                        const icon =
                          r.provider === "youtube"
                            ? "📺"
                            : r.type === "pdf"
                              ? "📄"
                              : "▶";
                        const statusStyle =
                          r.status === "done"
                            ? "bg-emerald-50 text-emerald-600 border-emerald-200"
                            : r.status === "failed"
                              ? "bg-red-50 text-red-600 border-red-200"
                              : r.status
                                ? "bg-amber-50 text-amber-600 border-amber-200"
                                : "";
                        return (
                          <li
                            key={r.id}
                            className="group flex flex-col gap-3 rounded-xl border border-gray-200 bg-white p-4 shadow-sm transition hover:border-violet-300 hover:shadow"
                          >
                            <div className="flex items-center gap-3">
                              <span className="flex h-11 w-11 items-center justify-center rounded-xl bg-violet-50 text-xl">
                                {icon}
                              </span>
                              <div className="min-w-0 flex-1">
                                <div className="truncate text-sm font-semibold text-gray-900">
                                  {s.title}
                                </div>
                                <div className="text-xs text-gray-400">
                                  {r.provider === "youtube" ? "YouTube" : "File"}
                                </div>
                              </div>
                            </div>
                            <div className="flex items-center justify-between">
                              {r.status ? (
                                <span
                                  className={`rounded-full border px-2.5 py-0.5 text-xs font-medium ${statusStyle}`}
                                >
                                  {r.status}
                                </span>
                              ) : (
                                <span />
                              )}
                              <button
                                type="button"
                                className="rounded-lg bg-violet-600 px-4 py-1.5 text-sm font-medium text-white transition hover:bg-violet-700"
                                onClick={() => setSelected(s)}
                              >
                                {r.type === "pdf" ? "Study" : "Watch"}
                              </button>
                            </div>
                          </li>
                        );
                      })}
                    </ul>
                  )}
                  <div className="mt-6 flex flex-col gap-3">
                    <button
                      type="button"
                      className="self-start rounded-xl border border-gray-200 bg-white px-4 py-2 text-sm text-gray-600 shadow-sm transition hover:bg-gray-50"
                      onClick={() =>
                        setSelected({
                          kind: "file",
                          resourceId: "demo-resource",
                          src: fileSrc,
                          title: "Demo file video (sample clip)",
                        })
                      }
                    >
                      ▶ Play sample clip (not course content)
                    </button>
                    {resources.some((r) => !toSelected(r)) && (
                      <p className="text-sm text-gray-400" role="note">
                        Some ingested files can no longer be played because their
                        original file is missing — re-ingest them from the course
                        folder to restore playback.
                      </p>
                    )}
                  </div>
                </section>
              </div>
            ) : (
              <div
                className={
                  chatLayout === "right"
                    ? "grid grid-cols-1 gap-6 lg:grid-cols-[250px_minmax(0,1fr)_minmax(320px,380px)]"
                    : "grid grid-cols-1 gap-6 lg:grid-cols-[250px_minmax(0,1fr)_230px]"
                }
              >
                {/* Course content tree.
                    TODO(backend): `GET /courses/{id}/resources` returns a flat
                    resource list with no unit/module hierarchy, so this list is
                    derived from the ingested resources themselves rather than
                    from a course tree. Once the backend exposes the real tree
                    (unit -> ordered lessons -> resource ids), render that here
                    and keep the same id-based completion map. */}
                <aside className="rounded-xl border border-gray-200 bg-white p-4 shadow-sm" aria-label="Course content">
                  <h2 className="mb-1 text-sm font-semibold uppercase tracking-wide text-gray-500">Course Content</h2>
                  <p className="mb-3 text-[11px] leading-snug text-gray-400">
                    Tick an item to mark it done. Nothing is marked complete
                    automatically.
                  </p>
                  {resourcesState === "loading" && (
                    <p className="text-xs text-gray-400" role="status">
                      Loading course content…
                    </p>
                  )}
                  {resourcesState === "error" && (
                    <p className="text-xs text-red-600" role="alert">
                      Could not load course content.
                    </p>
                  )}
                  {resourcesState !== "loading" && resourcesState !== "error" && (
                    <>
                      {resources.length === 0 ? (
                        <p className="rounded-lg border border-dashed border-gray-200 px-3 py-2 text-xs text-gray-400">
                          No course content available yet — no resources have
                          been ingested for this course.
                        </p>
                      ) : (
                        <ul className="flex flex-col gap-1">
                          {resources.map((r) => {
                            const isDone = completedIds.includes(r.id);
                            const isCurrent = activeResourceId === r.id;
                            return (
                              <li key={r.id}>
                                <label
                                  className={
                                    isCurrent
                                      ? "flex cursor-pointer items-center gap-2 rounded-lg bg-violet-50 px-2 py-1.5 text-xs font-medium text-violet-700"
                                      : "flex cursor-pointer items-center gap-2 rounded-lg px-2 py-1.5 text-xs text-gray-600 hover:bg-gray-50"
                                  }
                                  aria-current={isCurrent ? "true" : undefined}
                                >
                                  <input
                                    type="checkbox"
                                    className="h-3.5 w-3.5 shrink-0 accent-violet-600"
                                    checked={isDone}
                                    onChange={() => toggleCompleted(r.id)}
                                  />
                                  <span className="min-w-0 flex-1 break-words">
                                    {r.title ?? r.id}
                                  </span>
                                  {isCurrent && (
                                    <span className="shrink-0 text-[10px] font-semibold uppercase tracking-wide text-violet-600">
                                      Viewing
                                    </span>
                                  )}
                                </label>
                              </li>
                            );
                          })}
                        </ul>
                      )}
                    </>
                  )}
                  <button
                    type="button"
                    className="mt-4 w-full rounded-lg border border-gray-200 px-3 py-1.5 text-xs text-gray-500 transition hover:bg-gray-50"
                    onClick={() => setSelected(null)}
                  >
                    ← All videos
                  </button>
                </aside>

                {/* Center: player (+ chat when layout = bottom) */}
                <div className="flex min-w-0 flex-col gap-6">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <h2 className="min-w-0 truncate text-sm font-semibold text-gray-900">
                      {selected.title}
                    </h2>
                    <div
                      className="hidden shrink-0 items-center gap-1 rounded-lg border border-gray-200 bg-white p-1 shadow-sm lg:flex"
                      role="group"
                      aria-label="Chat layout"
                    >
                      <span className="px-1.5 text-[11px] font-medium uppercase tracking-wide text-gray-400">
                        Chat
                      </span>
                      {CHAT_LAYOUT_OPTIONS.map((opt) => (
                        <button
                          key={opt.value}
                          type="button"
                          title={opt.hint}
                          aria-pressed={chatLayout === opt.value}
                          onClick={() => chooseChatLayout(opt.value)}
                          className={
                            chatLayout === opt.value
                              ? "rounded-md bg-violet-600 px-2.5 py-1 text-xs font-medium text-white"
                              : "rounded-md px-2.5 py-1 text-xs font-medium text-gray-500 transition hover:bg-gray-100 hover:text-gray-800"
                          }
                        >
                          {opt.label}
                        </button>
                      ))}
                    </div>
                  </div>

                  {selected.kind === "youtube" ? (
                    <YouTubePlayer
                      ref={ytHandleRef}
                      youtubeId={selected.youtubeId}
                      title={selected.title}
                      onPauseAt={setPausedAtMs}
                    />
                  ) : selected.kind === "pdf" ? (
                    <div className="rounded-xl border border-gray-200 bg-white p-6 shadow-sm" role="note">
                      <h3 className="mb-2 text-lg font-semibold text-gray-900">{selected.title}</h3>
                      <p className="text-sm text-gray-500">
                        This is a document resource — its questions are answered
                        through the chat panel with page citations. Ask a question
                        below.
                      </p>
                    </div>
                  ) : (
                    <VideoPlayer
                      src={selected.src}
                      videoRef={videoRef}
                      onPauseAt={setPausedAtMs}
                    />
                  )}
                  {chatLayout === "bottom" && (
                    <ChatPanel
                      sessionId={session.session_id}
                      sessionToken={session.session_token}
                      videoId={selected.resourceId}
                      getCurrentTimeMs={getCurrentTimeMs}
                      onSeek={handleSeek}
                      offline={offline}
                      onRejoin={handleRejoin}
                    />
                  )}
                </div>

                {/* Right column: chat (layout = right) + real ingested sources */}
                <aside className="flex flex-col gap-4" aria-label="Sources and topics">
                  {chatLayout === "right" && (
                    <div className="lg:sticky lg:top-20 lg:self-start">
                      <ChatPanel
                        sessionId={session.session_id}
                        sessionToken={session.session_token}
                        videoId={selected.resourceId}
                        getCurrentTimeMs={getCurrentTimeMs}
                        onSeek={handleSeek}
                        offline={offline}
                        onRejoin={handleRejoin}
                      />
                    </div>
                  )}
                  <div className="rounded-xl border border-gray-200 bg-white p-4 shadow-sm">
                    <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-gray-500">Sources</h2>
                    {resourcesState === "loading" && (
                      <p className="text-xs text-gray-400" role="status">
                        Loading sources…
                      </p>
                    )}
                    {resourcesState === "error" && (
                      <p className="text-xs text-red-600" role="alert">
                        Could not load sources.
                      </p>
                    )}
                    {resourcesState === "ready" && resources.length === 0 && (
                      <p className="text-xs text-gray-500">No sources ingested yet</p>
                    )}
                    {resources.length > 0 && (
                      <ul className="flex flex-col gap-1.5">
                        {resources.map((r) => {
                          const s = toSelected(r);
                          const isCurrent = activeResourceId === r.id;
                          const icon =
                            r.provider === "youtube" ? "📺" : r.type === "pdf" ? "📄" : "▶";
                          const statusStyle =
                            r.status === "done"
                              ? "border-emerald-200 bg-emerald-50 text-emerald-600"
                              : r.status === "failed"
                                ? "border-red-200 bg-red-50 text-red-600"
                                : r.status
                                  ? "border-amber-200 bg-amber-50 text-amber-600"
                                  : "border-gray-200 bg-gray-50 text-gray-500";
                          const body = (
                            <>
                              <span className="shrink-0" aria-hidden="true">{icon}</span>
                              <span className="min-w-0 flex-1">
                                <span className="block break-words text-xs font-medium text-gray-800">
                                  {r.title ?? r.id}
                                </span>
                                <span className="block text-[11px] text-gray-400">
                                  {resourceKindLabel(r)}
                                </span>
                              </span>
                              {r.status && (
                                <span
                                  className={`shrink-0 rounded-full border px-2 py-0.5 text-[10px] font-medium ${statusStyle}`}
                                >
                                  {r.status}
                                </span>
                              )}
                            </>
                          );
                          const rowClass =
                            "flex w-full items-start gap-2 rounded-lg border border-transparent px-2 py-1.5 text-left transition";
                          return (
                            <li key={r.id}>
                              {s ? (
                                <button
                                  type="button"
                                  onClick={() => setSelected(s)}
                                  aria-current={isCurrent ? "true" : undefined}
                                  title={`Open ${resourceKindLabel(r)}`}
                                  className={`${rowClass} hover:border-violet-200 hover:bg-violet-50 focus:outline-none focus:ring-2 focus:ring-violet-300 ${
                                    isCurrent ? "border-violet-200 bg-violet-50" : ""
                                  }`}
                                >
                                  {body}
                                </button>
                              ) : (
                                <div
                                  className={`${rowClass} opacity-60`}
                                  title="Original file is unavailable, so this resource cannot be opened"
                                >
                                  {body}
                                  <span className="sr-only">
                                    (original file unavailable)
                                  </span>
                                </div>
                              )}
                            </li>
                          );
                        })}
                      </ul>
                    )}
                    <p className="mt-3 text-[11px] leading-snug text-gray-400">
                      Titles and statuses come from the ingested course resources.
                      Page/slide numbers appear only in chat citations.
                    </p>
                  </div>
                </aside>
              </div>
            )}
          </main>
        </div>
      </div>
    </div>
  );
}
