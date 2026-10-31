"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";

import { getSupabaseBrowserClient } from "@/lib/supabase/client";
import { signOut as signOutAccount, useProfile } from "@/lib/profile";

/**
 * Signed-in user + Sign out, for the sidebar footer. Renders nothing when
 * Supabase is not configured or nobody is signed in.
 */
export default function AccountMenu({ collapsed }: { collapsed: boolean }) {
  const router = useRouter();
  const [email, setEmail] = useState<string | null>(null);
  const profile = useProfile();

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
    await signOutAccount();
    router.replace("/login");
    router.refresh();
  }

  const name = profile?.displayName ?? email;
  const avatar = profile?.avatarUrl ? (
    // eslint-disable-next-line @next/next/no-img-element
    <img src={profile.avatarUrl} alt="" className="h-8 w-8 shrink-0 rounded-full object-cover" />
  ) : (
    <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-gradient-to-br from-indigo-500 to-violet-500 text-sm font-bold text-white">
      {name[0]?.toUpperCase() ?? "?"}
    </span>
  );

  if (collapsed) {
    return (
      <Link
        href="/profile"
        title={`${email} (Profile)`}
        aria-label={`Signed in as ${email}. Open profile`}
        className="flex items-center justify-center rounded-xl px-2 py-2 transition hover:bg-white/5 focus:outline-none focus-visible:ring-2 focus-visible:ring-violet-400"
      >
        {avatar}
      </Link>
    );
  }

  return (
    <div className="flex items-center gap-3 rounded-xl px-3 py-2">
      <Link href="/profile" title="Profile" className="flex min-w-0 flex-1 items-center gap-3 rounded-lg focus:outline-none focus-visible:ring-2 focus-visible:ring-violet-400">
        {avatar}
        <span className="min-w-0 flex-1">
          <span className="block truncate text-xs font-medium text-gray-200">{name}</span>
          {name !== email && <span className="block truncate text-[11px] text-gray-400">{email}</span>}
        </span>
      </Link>
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
