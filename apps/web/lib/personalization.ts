import { getSupabaseBrowserClient } from "@/lib/supabase/client";

/**
 * Malai personalization (the sidebar's Personalize modal). Saved in this
 * browser and, when signed in, in the account's user metadata so it follows
 * the learner to other devices. Sent with every chat and voice request as
 * `personalization`; the API turns it into a style note (tone only, never
 * the grounding or citation rules).
 */

export type LanguagePref = "auto" | "english" | "hinglish" | "hindi";
export type LengthPref = "concise" | "balanced" | "detailed";

export interface Personalization {
  enabled: boolean;
  name: string;
  about: string;
  traits: string[];
  instructions: string;
  language: LanguagePref;
  length: LengthPref;
}

/** What the API accepts (apps/api/app/chat/schemas.py `Personalization`). */
export interface PersonalizationPayload {
  name?: string;
  about?: string;
  traits: string[];
  instructions?: string;
  language: LanguagePref;
  length: LengthPref;
}

export const LIMITS = { name: 60, about: 300, instructions: 1500, traits: 8, trait: 40 } as const;

export const TRAITS = [
  "Encouraging",
  "Patient",
  "Chatty",
  "Witty",
  "Straight to the point",
  "Uses analogies",
  "Socratic",
  "Gen Z",
] as const;

export const DEFAULT_PERSONALIZATION: Personalization = {
  enabled: true,
  name: "",
  about: "",
  traits: [],
  instructions: "",
  language: "auto",
  length: "balanced",
};

const KEY = "ailearn.personalization";
const META_KEY = "malai_personalization";

let cache: Personalization | null = null;
const listeners = new Set<(p: Personalization) => void>();

function clean(raw: unknown): Personalization {
  const r = (raw && typeof raw === "object" ? raw : {}) as Partial<Personalization>;
  const str = (v: unknown, max: number) => (typeof v === "string" ? v.slice(0, max) : "");
  const langs: LanguagePref[] = ["auto", "english", "hinglish", "hindi"];
  const lengths: LengthPref[] = ["concise", "balanced", "detailed"];
  return {
    enabled: r.enabled !== false,
    name: str(r.name, LIMITS.name),
    about: str(r.about, LIMITS.about),
    traits: Array.isArray(r.traits)
      ? r.traits.filter((t): t is string => typeof t === "string").map((t) => t.slice(0, LIMITS.trait)).slice(0, LIMITS.traits)
      : [],
    instructions: str(r.instructions, LIMITS.instructions),
    language: langs.includes(r.language as LanguagePref) ? (r.language as LanguagePref) : "auto",
    length: lengths.includes(r.length as LengthPref) ? (r.length as LengthPref) : "balanced",
  };
}

export function loadPersonalization(): Personalization {
  if (cache) return cache;
  try {
    const raw = typeof window !== "undefined" ? window.localStorage.getItem(KEY) : null;
    cache = raw ? clean(JSON.parse(raw)) : { ...DEFAULT_PERSONALIZATION };
  } catch {
    cache = { ...DEFAULT_PERSONALIZATION };
  }
  return cache;
}

function storeLocal(p: Personalization): void {
  cache = p;
  try {
    window.localStorage.setItem(KEY, JSON.stringify(p));
  } catch {
    // Storage unavailable: the choice lasts for this page only.
  }
  for (const fn of listeners) fn(p);
}

/** Save locally, then to the signed-in account (best effort). */
export async function savePersonalization(next: Personalization): Promise<void> {
  const p = clean(next);
  storeLocal(p);
  const supabase = getSupabaseBrowserClient();
  if (!supabase) return;
  try {
    const { data } = await supabase.auth.getSession();
    if (data.session) await supabase.auth.updateUser({ data: { [META_KEY]: p } });
  } catch {
    // Offline or signed out: the local copy still applies.
  }
}

/** Pull the account's copy (another device may have changed it). */
export async function syncPersonalization(): Promise<Personalization> {
  const supabase = getSupabaseBrowserClient();
  if (supabase) {
    try {
      const { data } = await supabase.auth.getUser();
      const remote = data.user?.user_metadata?.[META_KEY];
      if (remote) storeLocal(clean(remote));
    } catch {
      // Keep the local copy.
    }
  }
  return loadPersonalization();
}

export function onPersonalizationChange(fn: (p: Personalization) => void): () => void {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

/** The request field, or undefined when turned off or empty. */
export function personalizationForRequest(): PersonalizationPayload | undefined {
  const p = loadPersonalization();
  if (!p.enabled) return undefined;
  const payload: PersonalizationPayload = {
    ...(p.name.trim() ? { name: p.name.trim() } : {}),
    ...(p.about.trim() ? { about: p.about.trim() } : {}),
    traits: p.traits,
    ...(p.instructions.trim() ? { instructions: p.instructions.trim() } : {}),
    language: p.language,
    length: p.length,
  };
  const empty = !payload.name && !payload.about && !payload.instructions && !payload.traits.length && payload.language === "auto" && payload.length === "balanced";
  return empty ? undefined : payload;
}
