"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";

import { getSupabaseBrowserClient } from "@/lib/supabase/client";

/**
 * Signed-in user + Sign out, for the sidebar footer. Renders nothing when
 * Supabase is not configured or nobody is signed in.
 */
export default function AccountMenu({ collapsed }: { collapsed: boolean }) {
  const router = useRouter();
  const [email, setEmail] = useState<string | null>(null);

  useEffect(() => {
    const supabase = getSupabaseBrowserClient();
    if (!supabase) return;
    let alive = true;
    void supabase.auth.getUser().then(({ data }) => {
      if (alive) setEmail(data.user?.email ?? null);
    });
    const { data: sub } = supabase.auth.onAuthStateChange((_event, session) => {
      setEmail(session?.user?.email ?? null);
    });
    return () => {
      alive = false;
      sub.subscription.unsubscribe();
    };
  }, []);

  if (!email) return null;

  async function signOut(): Promise<void> {
    await getSupabaseBrowserClient()?.auth.signOut();
    router.replace("/login");
    router.refresh();
  }

  const avatar = (
    <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-gradient-to-br from-indigo-500 to-violet-500 text-sm font-bold text-white">
      {email[0]?.toUpperCase() ?? "?"}
    </span>
  );

  if (collapsed) {
    return (
      <button
        type="button"
        onClick={signOut}
        title={`${email} (Sign out)`}
        aria-label={`Signed in as ${email}. Sign out`}
        className="flex items-center justify-center rounded-xl px-2 py-2 transition hover:bg-white/5 focus:outline-none focus-visible:ring-2 focus-visible:ring-violet-400"
      >
        {avatar}
      </button>
    );
  }

  return (
    <div className="flex items-center gap-3 rounded-xl px-3 py-2">
      {avatar}
      <span className="min-w-0 flex-1 truncate text-xs text-gray-300" title={email}>
        {email}
      </span>
      <button
        type="button"
        onClick={signOut}
        className="shrink-0 rounded-lg px-2 py-1 text-xs font-medium text-gray-400 transition hover:bg-white/5 hover:text-white focus:outline-none focus-visible:ring-2 focus-visible:ring-violet-400"
      >
        Sign out
      </button>
    </div>
  );
}
