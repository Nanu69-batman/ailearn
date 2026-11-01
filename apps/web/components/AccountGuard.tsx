"use client";

import { useEffect } from "react";

import { claimBrowserForUser, ensureAccountClaimed } from "@/lib/accountScope";
import { getSupabaseBrowserClient } from "@/lib/supabase/client";

/**
 * Mounted once at the app root. Ties this browser's stored chats, quizzes,
 * personalization and activity to the signed-in account, and clears them the
 * moment a different account signs in or the current one signs out.
 */
export default function AccountGuard() {
  useEffect(() => {
    void ensureAccountClaimed();
    const supabase = getSupabaseBrowserClient();
    if (!supabase) return;
    const { data: sub } = supabase.auth.onAuthStateChange((_event, session) => {
      claimBrowserForUser(session?.user?.id ?? null);
    });
    return () => sub.subscription.unsubscribe();
  }, []);
  return null;
}
