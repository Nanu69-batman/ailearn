"use client";

import {
  Suspense,
  useCallback,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { usePathname, useRouter } from "next/navigation";
import { DASHBOARD_PATH, LEARNER_NAME, SUBJECTS_PATH } from "@/lib/learn";
import { MALAI_PATH } from "@/lib/malai";
import MalaiSidebar from "@/components/MalaiSidebar";

/**
 * The chrome every route inside the `(learn)` group shares: sidebar, mobile
 * drawer, top bar. It covers /dashboard and the AI Tutor routes alike, so the
 * collapsed-rail preference, the drawer and the session above it all survive
 * navigation between them without a remount.
 */

const NOT_AVAILABLE_SUFFIX = "— not yet available";

type NavItem = {
  label: string;
  icon: string;
  /** True only when the item leads somewhere real. */
  implemented: boolean;
  /** Where the item goes. Only set for implemented items. */
  href?: string;
  /** Path prefix that marks this item as the current screen. */
  activePrefix?: string;
};

const NAV_ITEMS: ReadonlyArray<NavItem> = [
  { label: "Dashboard", icon: "M3 12l9-9 9 9M5 10v10h5v-6h4v6h5V10", implemented: true, href: DASHBOARD_PATH, activePrefix: DASHBOARD_PATH },
  { label: "My Subjects", icon: "M4 19.5A2.5 2.5 0 016.5 17H20V4H6.5A2.5 2.5 0 004 6.5v13zM4 19.5A2.5 2.5 0 006.5 22H20v-5", implemented: true, href: SUBJECTS_PATH, activePrefix: SUBJECTS_PATH },
  { label: "Malai", icon: "M12 2a7 7 0 00-7 7c0 2.4 1.2 4.5 3 5.7V17a2 2 0 002 2h4a2 2 0 002-2v-2.3c1.8-1.2 3-3.3 3-5.7a7 7 0 00-7-7zM9 22h6", implemented: true, href: MALAI_PATH, activePrefix: MALAI_PATH },
  { label: "Practice", icon: "M9 11l3 3L22 4M21 12v7a2 2 0 01-2 2H5a2 2 0 01-2-2V5a2 2 0 012-2h11", implemented: false },
  { label: "Progress", icon: "M3 3v18h18M7 14l4-4 4 4 5-6", implemented: false },
  { label: "Resources", icon: "M4 4h16v16H4zM4 9h16M9 4v5", implemented: false },
];

const SIDEBAR_BOTTOM: ReadonlyArray<NavItem> = [
  { label: "Calendar", icon: "M8 2v4M16 2v4M3 9h18M5 4h14a2 2 0 012 2v14a2 2 0 01-2 2H5a2 2 0 01-2-2V6a2 2 0 012-2z", implemented: false },
  { label: "Notifications", icon: "M18 8a6 6 0 10-12 0c0 7-3 9-3 9h18s-3-2-3-9M13.7 21a1.65 1.65 0 01-3.4 0", implemented: false },
];

const SIDEBAR_FOOTER: ReadonlyArray<NavItem> = [
  { label: "Profile", icon: "M20 21v-2a4 4 0 00-4-4H8a4 4 0 00-4 4v2M12 11a4 4 0 100-8 4 4 0 000 8z", implemented: false },
  { label: "Settings", icon: "M12 15a3 3 0 100-6 3 3 0 000 6zM19.4 15a1.65 1.65 0 00.33 1.82l.06.06a2 2 0 11-2.83 2.83l-.06-.06a1.65 1.65 0 00-1.82-.33 1 1 0 00-1 1.51V21a2 2 0 11-4 0v-.09a1 1 0 00-1-1.51 1.65 1.65 0 00-1.82.33l-.06.06a2 2 0 11-2.83-2.83l.06-.06a1.65 1.65 0 00.33-1.82 1 1 0 00-1.51-1H3a2 2 0 110-4h.09a1 1 0 001.51-1 1.65 1.65 0 00-.33-1.82l-.06-.06a2 2 0 112.83-2.83l.06.06a1.65 1.65 0 00.33 1.82h.01a1 1 0 001-1.51V3a2 2 0 114 0v.09a1 1 0 001 1.51h.01a1.65 1.65 0 001.82-.33l.06-.06a2 2 0 112.83 2.83l-.06.06a1.65 1.65 0 00-.33 1.82v.01a1 1 0 001.51 1H21a2 2 0 110 4h-.09a1 1 0 00-1.51 1z", implemented: false },
];

/** Collapsed icon rail vs. the default 15rem sidebar. */
const SIDEBAR_ID = "learn-sidebar";
const SIDEBAR_RAIL_CLASS = "w-14";
const SIDEBAR_EXPANDED_CLASS = "w-60";
/** Malai's chat list needs more room than the app navigation. */
const SIDEBAR_MALAI_CLASS = "w-80";
const SIDEBAR_COLLAPSED_KEY = "ailearn.sidebar_collapsed";
/** Small-screen drawer. Independent of the desktop rail state on purpose. */
const DRAWER_ID = "learn-mobile-drawer";

function navAriaLabel(item: NavItem, active: boolean): string {
  if (!item.implemented) return `${item.label} ${NOT_AVAILABLE_SUFFIX}`;
  return active ? `${item.label}, current page` : item.label;
}

/** Implemented items go dark when they own the current URL. */
function activeTone(active: boolean): string {
  return active
    ? "bg-violet-600/90 text-white shadow"
    : "text-gray-400 hover:bg-white/5 hover:text-white";
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

export default function LearnShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();

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
  /**
   * On /malai the sidebar shows Malai's chats. "Back to main sidebar" swaps in
   * the app navigation; the Malai item swaps the chats back. Every arrival on
   * a page starts from that page's own sidebar.
   */
  const onMalai = pathname.startsWith(MALAI_PATH);
  const [mainOnMalai, setMainOnMalai] = useState(false);
  useEffect(() => {
    setMainOnMalai(false);
  }, [onMalai]);
  const showMalai = onMalai && !mainOnMalai;
  const backToMain = useCallback(() => setMainOnMalai(true), []);
  const drawerRef = useRef<HTMLDivElement | null>(null);
  const menuButtonRef = useRef<HTMLButtonElement | null>(null);

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

  const toggleSidebar = useCallback((): void => {
    setSidebarCollapsed((prev) => {
      writeSidebarCollapsed(!prev);
      return !prev;
    });
  }, []);

  // Single renderer for both nav groups so the "Soon" tag and the disabled
  // semantics can never drift apart between primary and secondary lists.
  // `collapsed` is passed in rather than read from state so the mobile drawer
  // can force the expanded variant without mutating the persisted desktop flag.
  const renderNavItem = (item: NavItem, collapsed: boolean): ReactNode => {
    const unavailable = !item.implemented;
    const active =
      item.implemented &&
      item.activePrefix !== undefined &&
      pathname.startsWith(item.activePrefix);
    const label = navAriaLabel(item, active);
    const layout = collapsed
      ? "justify-center px-2 py-2.5"
      : "gap-3 px-3 py-2.5";
    const baseClass = [
      "flex items-center rounded-xl text-sm transition",
      layout,
      // Non-functional items must not read as clickable.
      unavailable ? "cursor-default" : "cursor-pointer",
      activeTone(active),
    ].join(" ");

    const inner = (
      <>
        <SideIcon d={item.icon} />
        {/* Rail mode hides all text so the tag can never push the icon out. */}
        {!collapsed && (
          <>
            <span className="truncate">{item.label}</span>
            {/* In Malai with the app navigation showing: this item brings the chats back. */}
            {item.href === MALAI_PATH && onMalai && !showMalai && (
              <span
                aria-hidden="true"
                className="ml-auto flex shrink-0 items-center gap-0.5 text-[11px] font-medium text-violet-100"
              >
                Chats
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" className="h-3 w-3">
                  <path d="M5 12h14M13 6l6 6-6 6" />
                </svg>
              </span>
            )}
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
      </>
    );

    if (item.implemented && item.href) {
      return (
        <a
          key={item.label}
          href={item.href}
          onClick={(e) => {
            // A real <a> keeps middle-click / open-in-new-tab working, but a
            // plain click is a client navigation so the session in the provider
            // above is never torn down.
            if (e.metaKey || e.ctrlKey || e.shiftKey || e.button !== 0) return;
            e.preventDefault();
            // Already in Malai: bring its chats back, keeping the open chat.
            if (item.href === MALAI_PATH && onMalai) {
              setMainOnMalai(false);
              return;
            }
            router.push(item.href as string);
          }}
          aria-current={active ? "page" : undefined}
          aria-label={label}
          title={label}
          className={baseClass}
        >
          {inner}
        </a>
      );
    }

    return (
      <a
        key={item.label}
        href="#"
        onClick={(e) => e.preventDefault()}
        // Kept focusable (not a dead div) so keyboard users can still discover
        // that the item exists, but announced as unavailable instead of working.
        aria-disabled
        aria-label={label}
        title={label}
        className={baseClass}
      >
        {inner}
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
            "sticky top-0 hidden h-screen shrink-0 flex-col overflow-y-auto bg-[#1a1f3a] text-gray-300",
            "transition-[width] duration-200 md:flex",
            sidebarCollapsed
              ? SIDEBAR_RAIL_CLASS
              : showMalai
                ? SIDEBAR_MALAI_CLASS
                : SIDEBAR_EXPANDED_CLASS,
          ].join(" ")}
        >
          {showMalai ? (
            <Suspense fallback={null}>
              <MalaiSidebar
                collapsed={sidebarCollapsed}
                onToggleCollapse={toggleSidebar}
                onBack={backToMain}
              />
            </Suspense>
          ) : (
          <>
          {/* Brand row. Expanded: logo, name, collapse arrow on the right.
              Rail: the logo itself is the expand button and turns into the
              arrow on hover/focus. */}
          {sidebarCollapsed ? (
            <div className="flex items-center justify-center px-2 py-6">
              <button
                type="button"
                onClick={toggleSidebar}
                aria-expanded={false}
                aria-controls={SIDEBAR_ID}
                aria-label="Expand sidebar"
                title="Expand sidebar"
                className="group/brand relative flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-gradient-to-br from-indigo-500 to-violet-500 text-lg font-bold text-white transition hover:from-white/10 hover:to-white/10 focus:outline-none focus-visible:from-white/10 focus-visible:to-white/10 focus-visible:ring-2 focus-visible:ring-violet-400"
              >
                <span className="transition-all duration-200 group-hover/brand:scale-50 group-hover/brand:opacity-0 group-focus-visible/brand:opacity-0">
                  M
                </span>
                <svg
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  className="absolute h-5 w-5 scale-50 opacity-0 transition-all duration-200 group-hover/brand:scale-100 group-hover/brand:opacity-100 group-focus-visible/brand:scale-100 group-focus-visible/brand:opacity-100"
                  aria-hidden="true"
                >
                  <path d="M9 6l6 6-6 6" />
                </svg>
              </button>
            </div>
          ) : (
            <div className="flex items-center gap-3 py-6 pl-5 pr-3">
              <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-gradient-to-br from-indigo-500 to-violet-500 text-lg font-bold text-white">
                M
              </span>
              <div className="min-w-0 flex-1 animate-[shell-fade_200ms_ease-out]">
                <p className="truncate text-sm font-bold leading-tight text-white">Malai Pioneer</p>
                <p className="text-[11px] text-gray-400">Learning</p>
              </div>
              <button
                type="button"
                onClick={toggleSidebar}
                aria-expanded
                aria-controls={SIDEBAR_ID}
                aria-label="Collapse sidebar"
                title="Collapse sidebar"
                className="shrink-0 rounded-lg p-1.5 text-gray-400 transition hover:bg-white/5 hover:text-white focus:outline-none focus-visible:ring-2 focus-visible:ring-violet-400"
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
                  <path d="M15 6l-6 6 6 6" />
                </svg>
              </button>
            </div>
          )}

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
          <div className="mx-5 mb-4 mt-auto border-t border-white/10" />
          <nav
            className={sidebarCollapsed ? "flex flex-col gap-1 px-2 pb-6" : "flex flex-col gap-1 px-3 pb-6"}
            aria-label="Account"
          >
            {SIDEBAR_FOOTER.map((item) => renderNavItem(item, sidebarCollapsed))}
          </nav>
          </>
          )}
        </aside>

        {/* Small-screen-only drawer. `md:hidden` means the persistent sidebar
            above is untouched at `md` and up; this is an addition, never a
            replacement. Stays mounted so the slide animates both ways, and is
            hidden from AT while closed. */}
        <div className="pointer-events-none fixed inset-0 z-40 md:hidden" aria-hidden={!drawerOpen}>
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
            {showMalai ? (
              <Suspense fallback={null}>
                <MalaiSidebar
                  collapsed={false}
                  onToggleCollapse={closeDrawer}
                  onBack={backToMain}
                  onNavigate={closeDrawer}
                />
              </Suspense>
            ) : (
            <>
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
            <div className="mx-5 my-4 border-t border-white/10" />
            <nav
              className="flex flex-col gap-1 px-3 pb-6"
              aria-label="Account"
              onClick={closeDrawer}
            >
              {SIDEBAR_FOOTER.map((item) => renderNavItem(item, false))}
            </nav>
            </>
            )}
          </div>
        </div>

        <div className="flex min-w-0 flex-1 flex-col">
          {/* Top bar */}
          <header className="sticky top-0 z-10 flex items-center justify-between gap-4 border-b border-gray-200/80 bg-white/85 px-4 py-3 backdrop-blur supports-[backdrop-filter]:bg-white/70 sm:px-6 md:hidden">
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
              <span className="flex h-9 w-9 items-center justify-center rounded-xl bg-gradient-to-br from-indigo-500 to-violet-500 text-base font-bold text-white">
                M
              </span>
              {/* Brand label, not a heading: each page owns its own <h1>. */}
              <span className="text-lg font-bold tracking-tight text-gray-900">Malai Pioneer</span>
            </div>
            <div className="hidden flex-1 justify-center sm:flex">
              <label className="sr-only" htmlFor="top-search">Search</label>
              <input
                id="top-search"
                type="search"
                placeholder="Search subjects, topics, notes…"
                className="w-full max-w-md rounded-full border border-gray-200 bg-gray-100 px-4 py-2 text-sm text-gray-800 placeholder-gray-400 outline-none transition focus:border-violet-400 focus:bg-white focus:ring-2 focus:ring-violet-200"
              />
            </div>
            <div className="flex items-center gap-4">
              {/* Opens the live pipeline trace (/dev) in its own window, so it
                  can sit beside the lesson during a demo. */}
              <button
                type="button"
                onClick={() =>
                  window.open("/dev", "malai-dev-view", "width=1440,height=900")
                }
                title="Open the developer view: a live diagram of every pipeline step"
                className="hidden items-center gap-1.5 rounded-lg border border-violet-200 bg-violet-50 px-3 py-1.5 text-xs font-semibold text-violet-700 transition hover:border-violet-300 hover:bg-violet-100 focus:outline-none focus-visible:ring-2 focus-visible:ring-violet-400 sm:flex"
              >
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="h-4 w-4" aria-hidden="true">
                  <path d="M16 18l6-6-6-6M8 6l-6 6 6 6" />
                </svg>
                Developer view
              </button>
              <button type="button" aria-label="Notifications" className="relative rounded-full p-2 text-gray-500 transition hover:bg-gray-100 hover:text-gray-800 focus:outline-none focus-visible:ring-2 focus-visible:ring-violet-400">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" className="h-5 w-5">
                  <path d="M18 8a6 6 0 10-12 0c0 7-3 9-3 9h18s-3-2-3-9M13.7 21a1 1 0 01-3.4 0" />
                </svg>
                <span className="absolute right-1.5 top-1.5 h-2 w-2 rounded-full bg-violet-500" />
              </button>
              <div className="flex items-center gap-2">
                <span className="flex h-9 w-9 items-center justify-center rounded-full bg-gradient-to-br from-violet-100 to-indigo-100 text-sm font-bold text-violet-700 ring-2 ring-white">{LEARNER_NAME[0]}</span>
                <span className="hidden text-sm font-medium text-gray-800 sm:inline">{LEARNER_NAME}</span>
              </div>
            </div>
          </header>

          <main className="relative flex-1 px-4 py-6 sm:px-6 lg:px-10 lg:py-10">
            {/* Desktop has no top bar: the developer view is a floating icon
                button in the corner, labelled by its tooltip. */}
            <div className="group absolute right-4 top-4 z-20 hidden md:block lg:right-6 lg:top-6">
              <button
                type="button"
                onClick={() =>
                  window.open("/dev", "malai-dev-view", "width=1440,height=900")
                }
                aria-label="Open the developer view: a live diagram of every pipeline step"
                className="flex h-11 w-11 items-center justify-center rounded-full bg-violet-100 text-violet-700 shadow-sm transition hover:bg-violet-200 focus:outline-none focus-visible:ring-2 focus-visible:ring-violet-400"
              >
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" className="h-5 w-5" aria-hidden="true">
                  <path d="M15 18l6-6-6-6M9 6l-6 6 6 6" />
                </svg>
              </button>
              <span
                role="tooltip"
                className="pointer-events-none absolute right-0 top-full mt-2 whitespace-nowrap rounded-md bg-gray-900 px-2.5 py-1.5 text-xs font-medium text-white opacity-0 shadow transition group-focus-within:opacity-100 group-hover:opacity-100"
              >
                Developer view
              </span>
            </div>
            {children}
          </main>
        </div>
      </div>
    </div>
  );
}