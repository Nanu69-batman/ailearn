"use client";

import { useEffect, useState } from "react";

import { getSupabaseBrowserClient, supabaseConfigured } from "@/lib/supabase/client";

/** Account role chosen at sign-up, stored in Supabase `user_metadata.role`. */
export type Role = "individual" | "teacher" | "student";

export const ROLES: { id: Role; label: string; blurb: string }[] = [
  { id: "individual", label: "Individual", blurb: "Learn on your own with your own subjects." },
  { id: "teacher", label: "Teacher", blurb: "Create classes, invite students, manage their subjects." },
  { id: "student", label: "Student", blurb: "Join your teacher's class with an invite link." },
];

export function isRole(v: unknown): v is Role {
  return v === "individual" || v === "teacher" || v === "student";
}

/** Save name + role on the account, then refresh so the API token carries them. */
export async function saveRole(name: string, role: Role): Promise<void> {
  const supabase = getSupabaseBrowserClient();
  if (!supabase) return;
  const { error } = await supabase.auth.updateUser({ data: { full_name: name, role } });
  if (error) throw new Error(error.message);
  await supabase.auth.refreshSession();
}

/**
 * The signed-in user's role. `null` while loading; `"individual"` when auth
 * is off or no role has been chosen, so the app behaves exactly as before.
 */
export function useRole(): Role | null {
  const [role, setRole] = useState<Role | null>(supabaseConfigured() ? null : "individual");
  useEffect(() => {
    const supabase = getSupabaseBrowserClient();
    if (!supabase) return;
    let alive = true;
    void supabase.auth.getUser().then(({ data }) => {
      const r = (data.user?.user_metadata as Record<string, unknown> | undefined)?.role;
      if (alive) setRole(isRole(r) ? r : "individual");
    });
    return () => {
      alive = false;
    };
  }, []);
  return role;
}
