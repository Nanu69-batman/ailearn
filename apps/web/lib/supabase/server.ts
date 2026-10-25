import { createServerClient } from "@supabase/ssr";
import type { SupabaseClient } from "@supabase/supabase-js";
import { cookies } from "next/headers";

import { SUPABASE_ANON_KEY, SUPABASE_URL, supabaseConfigured } from "./config";

export { supabaseConfigured, safeNextPath } from "./config";

/**
 * Server client for route handlers and server components (Next 15 async
 * cookies()). `null` when Supabase is not configured.
 */
export async function getSupabaseServerClient(): Promise<SupabaseClient | null> {
  if (!supabaseConfigured()) return null;
  const cookieStore = await cookies();
  return createServerClient(SUPABASE_URL, SUPABASE_ANON_KEY, {
    cookies: {
      getAll() {
        return cookieStore.getAll();
      },
      setAll(cookiesToSet) {
        try {
          for (const { name, value, options } of cookiesToSet) {
            cookieStore.set(name, value, options);
          }
        } catch {
          // Called from a server component: cookies are read-only there, and
          // the middleware refreshes the session instead.
        }
      },
    },
  });
}
