/**
 * Which account this browser's stored data belongs to.
 *
 * Chats, quizzes, personalization, activity and progress are cached in
 * localStorage/sessionStorage under fixed keys. Without an owner check, the
 * next person to sign in on the same browser would see the previous account's
 * chats and name, and the chat/quiz sync would even upload them into the new
 * account. So: storage is stamped with the owner's user id, and when a
 * different account (or nobody) takes over, every per-user key is removed and
 * every in-memory cache is reset before anything is read or synced.
 *
 * Stores read nothing until `isAccountClaimed()` is true for this page load,
 * and anything that syncs with the server awaits `ensureAccountClaimed()`
 * first.
 */
import { getSupabaseBrowserClient } from "@/lib/supabase/client";

const OWNER_KEY = "ailearn.account.owner";
/** Written by older builds (lib/activity.ts); adopted so an upgrade keeps its data. */
const LEGACY_OWNER_KEY = "ailearn.activity.owner";
/** Stored owner for a signed-out browser. */
const ANON = "anon";

/** Device UI preferences: not personal, kept across accounts. */
const DEVICE_KEYS = new Set([
  "ailearn.chat_layout",
  "ailearn.sidebar_collapsed",
  "ailearn.subjects.view",
  "ailearn.voice_model",
  "ailearn.voice_name",
  "ailearn.voice_tone",
  "malai:model-settings",
  "malai:quiz-model",
]);
const DEVICE_PREFIXES = ["ailearn.lesson.layout", "malai.dev."];

function isPerUserKey(key: string): boolean {
  if (key === OWNER_KEY) return false;
  if (!key.startsWith("ailearn.") && !key.startsWith("malai.")) return false;
  if (DEVICE_KEYS.has(key)) return false;
  return !DEVICE_PREFIXES.some((p) => key.startsWith(p));
}

const resetHooks = new Set<() => void>();
const claimedListeners = new Set<() => void>();
let claimedFor: string | null = null;
let claiming: Promise<string | null> | null = null;

/** Register a reset for a module's in-memory cache; runs whenever the account changes. */
export function onAccountChange(fn: () => void): () => void {
  resetHooks.add(fn);
  return () => resetHooks.delete(fn);
}

/** Runs once storage is known to belong to the current account. */
export function onAccountClaimed(fn: () => void): () => void {
  claimedListeners.add(fn);
  return () => claimedListeners.delete(fn);
}

/** True once this page load has checked storage against the signed-in account. */
export function isAccountClaimed(): boolean {
  return claimedFor !== null;
}

function storedOwner(): string | null {
  try {
    const ls = window.localStorage;
    const owner = ls.getItem(OWNER_KEY);
    if (owner) return owner;
    const legacy = ls.getItem(LEGACY_OWNER_KEY);
    // Older builds stored it as JSON.
    return legacy ? (JSON.parse(legacy) as string) : null;
  } catch {
    return null;
  }
}

function wipe(storage: Storage): void {
  for (const key of Object.keys(storage)) {
    if (isPerUserKey(key)) storage.removeItem(key);
  }
}

/**
 * Hand this browser's storage to `userId` (null = signed out). When it
 * belonged to someone else, per-user data is removed and caches are reset.
 */
export function claimBrowserForUser(userId: string | null): void {
  if (typeof window === "undefined") return;
  const next = userId ?? ANON;
  const previous = storedOwner();
  // A browser with data but no recorded owner, or another owner: not ours.
  const changed = previous !== next && !(previous === null && next === ANON);
  if (changed) {
    try {
      wipe(window.localStorage);
    } catch {
      // Storage unavailable -> nothing stored to leak.
    }
    try {
      wipe(window.sessionStorage);
    } catch {
      // Same.
    }
  }
  try {
    window.localStorage.setItem(OWNER_KEY, next);
    window.localStorage.removeItem(LEGACY_OWNER_KEY);
  } catch {
    // Storage unavailable.
  }
  const accountSwitched = claimedFor !== null && claimedFor !== next;
  const firstClaim = claimedFor === null;
  claimedFor = next;
  if (changed || accountSwitched) resetHooks.forEach((fn) => fn());
  if (changed || accountSwitched || firstClaim) claimedListeners.forEach((fn) => fn());
}

/** The current account's id (null signed out), after claiming storage for it. */
export function ensureAccountClaimed(): Promise<string | null> {
  if (typeof window === "undefined") return Promise.resolve(null);
  if (!claiming) {
    claiming = (async () => {
      const supabase = getSupabaseBrowserClient();
      let userId: string | null = null;
      if (supabase) {
        try {
          const { data } = await supabase.auth.getSession();
          userId = data.session?.user.id ?? null;
        } catch {
          userId = null;
        }
      }
      claimBrowserForUser(userId);
      return userId;
    })().finally(() => {
      claiming = null;
    });
  }
  return claiming;
}
