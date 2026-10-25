import { createBrowserClient } from "@supabase/ssr";
import type { SupabaseClient } from "@supabase/supabase-js";

import { SUPABASE_ANON_KEY, SUPABASE_URL, supabaseConfigured } from "./config";

export { supabaseConfigured, safeNextPath } from "./config";

let browserClient: SupabaseClient | null = null;

/** Browser client (cookie session). `null` when Supabase is not configured. */
export function getSupabaseBrowserClient(): SupabaseClient | null {
  if (!supabaseConfigured() || typeof window === "undefined") return null;
  if (!browserClient) {
    browserClient = createBrowserClient(SUPABASE_URL, SUPABASE_ANON_KEY);
  }
  return browserClient;
}

/** Current access token, or `null` when signed out or auth is disabled. */
export async function getAccessToken(): Promise<string | null> {
  const supabase = getSupabaseBrowserClient();
  if (!supabase) return null;
  try {
    const { data } = await supabase.auth.getSession();
    return data.session?.access_token ?? null;
  } catch {
    return null;
  }
}
