"use client";

/**
 * The signed-in learner's account: email and sign-in method from Supabase
 * Auth, display name and avatar from `profiles` (owner-only RLS), the avatar
 * image in the public `avatars` bucket under {user id}/.
 */
import { useEffect, useState } from "react";
import { getSupabaseBrowserClient } from "@/lib/supabase/client";

export interface Profile {
  id: string;
  email: string | null;
  /** "google", "email", ... */
  provider: string;
  createdAt: string | null;
  displayName: string;
  avatarUrl: string | null;
}

const listeners = new Set<(p: Profile | null) => void>();
let cache: Profile | null = null;
let loading: Promise<Profile | null> | null = null;

async function fetchProfile(): Promise<Profile | null> {
  const supabase = getSupabaseBrowserClient();
  if (!supabase) return null;
  const { data } = await supabase.auth.getUser();
  const user = data.user;
  if (!user) return null;
  const { data: row } = await supabase.from("profiles").select("display_name, avatar_url").eq("id", user.id).maybeSingle();
  const meta = (user.user_metadata ?? {}) as Record<string, unknown>;
  const str = (v: unknown) => (typeof v === "string" && v.trim() ? v.trim() : null);
  return {
    id: user.id,
    email: user.email ?? null,
    provider: String(user.app_metadata?.provider ?? "email"),
    createdAt: user.created_at ?? null,
    displayName: str(row?.display_name) ?? str(meta.full_name) ?? str(meta.name) ?? user.email?.split("@")[0] ?? "Learner",
    avatarUrl: str(row?.avatar_url) ?? str(meta.avatar_url) ?? str(meta.picture),
  };
}

function publish(p: Profile | null): void {
  cache = p;
  listeners.forEach((l) => l(p));
}

export function loadProfile(): Promise<Profile | null> {
  if (!loading) loading = fetchProfile().then((p) => (publish(p), p)).catch(() => null);
  return loading;
}

/** Null while loading or signed out. */
export function useProfile(): Profile | null {
  const [p, setP] = useState<Profile | null>(cache);
  useEffect(() => {
    listeners.add(setP);
    void loadProfile();
    return () => {
      listeners.delete(setP);
    };
  }, []);
  return p;
}

export async function saveProfile(patch: { displayName?: string; avatarUrl?: string | null }): Promise<void> {
  const supabase = getSupabaseBrowserClient();
  const current = cache ?? (await loadProfile());
  if (!supabase || !current) throw new Error("Sign in to edit your profile.");
  const next = { ...current, ...patch };
  const { error } = await supabase
    .from("profiles")
    .upsert({ id: current.id, display_name: next.displayName, avatar_url: next.avatarUrl }, { onConflict: "id" });
  if (error) throw new Error(error.message);
  publish(next);
}

/** Uploads to avatars/{user id}/ and returns its public URL. */
export async function uploadAvatar(file: File): Promise<string> {
  const supabase = getSupabaseBrowserClient();
  const current = cache ?? (await loadProfile());
  if (!supabase || !current) throw new Error("Sign in to change your photo.");
  if (!file.type.startsWith("image/")) throw new Error("Choose an image file.");
  if (file.size > 2 * 1024 * 1024) throw new Error("The image must be 2 MB or smaller.");
  const ext = (file.name.split(".").pop() || "png").toLowerCase().replace(/[^a-z0-9]/g, "") || "png";
  const path = `${current.id}/avatar-${Date.now()}.${ext}`;
  const { error } = await supabase.storage.from("avatars").upload(path, file, { upsert: true, contentType: file.type });
  if (error) throw new Error(error.message);
  return supabase.storage.from("avatars").getPublicUrl(path).data.publicUrl;
}

export async function signOut(): Promise<void> {
  await getSupabaseBrowserClient()?.auth.signOut();
  loading = null;
  publish(null);
}
