"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMalaiChats } from "@/lib/malai";
import { saveProfile, signOut, uploadAvatar, useProfile } from "@/lib/profile";
import { useSavedQuizzes } from "@/lib/quizStore";

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-xl border border-gray-200 bg-white px-4 py-3">
      <p className="text-[11px] font-medium uppercase tracking-wide text-gray-400">{label}</p>
      <p className="mt-0.5 text-xl font-semibold tabular-nums text-gray-900">{value}</p>
    </div>
  );
}

const PROVIDER_LABEL: Record<string, string> = { google: "Google", email: "Email and password" };

export default function ProfileView() {
  const router = useRouter();
  const profile = useProfile();
  const chats = useMalaiChats();
  const quizzes = useSavedQuizzes();
  const [name, setName] = useState("");
  const [saving, setSaving] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);
  const fileRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    if (profile) setName(profile.displayName);
  }, [profile]);

  if (!profile) {
    return <p className="mx-auto w-full max-w-3xl text-sm text-gray-500">Loading your profile…</p>;
  }

  const attempts = quizzes.flatMap((q) => q.attempts);
  const avg = attempts.length
    ? Math.round((attempts.reduce((s, a) => s + (a.total ? a.score / a.total : 0), 0) / attempts.length) * 100)
    : null;
  const trimmed = name.trim();

  async function onSave(): Promise<void> {
    setSaving(true);
    setMessage(null);
    try {
      await saveProfile({ displayName: trimmed });
      setMessage({ ok: true, text: "Saved." });
    } catch (err) {
      setMessage({ ok: false, text: err instanceof Error ? err.message : String(err) });
    } finally {
      setSaving(false);
    }
  }

  async function onPhoto(file: File): Promise<void> {
    setUploading(true);
    setMessage(null);
    try {
      await saveProfile({ avatarUrl: await uploadAvatar(file) });
      setMessage({ ok: true, text: "Photo updated." });
    } catch (err) {
      setMessage({ ok: false, text: err instanceof Error ? err.message : String(err) });
    } finally {
      setUploading(false);
    }
  }

  return (
    <div className="mx-auto flex w-full max-w-3xl flex-col gap-5 pb-10">
      <header>
        <h1 className="text-2xl font-bold tracking-tight text-gray-900">Profile</h1>
        <p className="mt-1 text-sm text-gray-500">How you appear in Malai Pioneer.</p>
      </header>

      <section className="rounded-2xl border border-gray-200 bg-white p-5 shadow-sm">
        <div className="flex flex-col gap-5 sm:flex-row sm:items-center">
          <div className="relative h-20 w-20 shrink-0">
            {profile.avatarUrl ? (
              // eslint-disable-next-line @next/next/no-img-element
              <img src={profile.avatarUrl} alt="" className="h-20 w-20 rounded-full object-cover ring-2 ring-violet-100" />
            ) : (
              <span className="flex h-20 w-20 items-center justify-center rounded-full bg-violet-600 text-2xl font-bold text-white">
                {profile.displayName[0]?.toUpperCase() ?? "?"}
              </span>
            )}
            {uploading && (
              <span className="absolute inset-0 flex items-center justify-center rounded-full bg-white/70">
                <span className="h-5 w-5 animate-spin rounded-full border-2 border-violet-600 border-t-transparent" aria-hidden="true" />
              </span>
            )}
          </div>
          <div className="min-w-0 flex-1">
            <p className="truncate text-lg font-semibold text-gray-900">{profile.displayName}</p>
            <p className="truncate text-sm text-gray-500">{profile.email}</p>
            <div className="mt-2 flex flex-wrap gap-2">
              <button
                type="button"
                onClick={() => fileRef.current?.click()}
                disabled={uploading}
                className="rounded-xl border border-violet-200 px-3 py-1.5 text-sm font-medium text-violet-700 hover:bg-violet-50 disabled:opacity-50"
              >
                Change photo
              </button>
              {profile.avatarUrl && (
                <button
                  type="button"
                  onClick={() => void saveProfile({ avatarUrl: null }).catch(() => {})}
                  className="rounded-xl border border-gray-200 px-3 py-1.5 text-sm text-gray-600 hover:bg-gray-50"
                >
                  Remove photo
                </button>
              )}
              <input
                ref={fileRef}
                type="file"
                accept="image/*"
                className="hidden"
                onChange={(e) => {
                  const f = e.target.files?.[0];
                  e.target.value = "";
                  if (f) void onPhoto(f);
                }}
              />
            </div>
          </div>
        </div>

        <div className="mt-6 border-t border-gray-100 pt-5">
          <label className="block max-w-md">
            <span className="mb-1.5 block text-xs font-medium text-gray-700">Display name</span>
            <input
              value={name}
              maxLength={60}
              onChange={(e) => setName(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && trimmed && trimmed !== profile.displayName) void onSave();
              }}
              className="w-full rounded-xl border border-gray-200 px-3 py-2 text-sm text-gray-900 outline-none focus:border-violet-400 focus:ring-4 focus:ring-violet-100"
            />
          </label>
          <div className="mt-3 flex items-center gap-3">
            <button
              type="button"
              onClick={() => void onSave()}
              disabled={saving || !trimmed || trimmed === profile.displayName}
              className="rounded-xl bg-violet-600 px-4 py-2 text-sm font-medium text-white hover:bg-violet-700 disabled:opacity-50"
            >
              {saving ? "Saving…" : "Save"}
            </button>
            {message && (
              <p role={message.ok ? "status" : "alert"} className={`text-sm ${message.ok ? "text-emerald-700" : "text-red-600"}`}>
                {message.text}
              </p>
            )}
          </div>
        </div>
      </section>

      <section className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Stat label="Chats" value={String(chats.length)} />
        <Stat label="Quizzes" value={String(quizzes.length)} />
        <Stat label="Attempts" value={String(attempts.length)} />
        <Stat label="Avg score" value={avg === null ? "—" : `${avg}%`} />
      </section>

      <section className="rounded-2xl border border-gray-200 bg-white p-5 shadow-sm">
        <h2 className="text-base font-semibold text-gray-900">Account</h2>
        <dl className="mt-3 grid gap-3 text-sm sm:grid-cols-2">
          <div>
            <dt className="text-xs text-gray-500">Email</dt>
            <dd className="text-gray-900">{profile.email ?? "—"}</dd>
          </div>
          <div>
            <dt className="text-xs text-gray-500">Signed in with</dt>
            <dd className="text-gray-900">{PROVIDER_LABEL[profile.provider] ?? profile.provider}</dd>
          </div>
          <div>
            <dt className="text-xs text-gray-500">Member since</dt>
            <dd className="text-gray-900">
              {profile.createdAt ? new Date(profile.createdAt).toLocaleDateString(undefined, { year: "numeric", month: "long", day: "numeric" }) : "—"}
            </dd>
          </div>
        </dl>
        <div className="mt-5 flex flex-wrap gap-2">
          <Link href="/settings" className="rounded-xl border border-gray-200 px-4 py-2 text-sm text-gray-700 hover:bg-gray-50">
            Settings
          </Link>
          <button
            type="button"
            onClick={async () => {
              await signOut();
              router.replace("/login");
            }}
            className="rounded-xl border border-gray-200 px-4 py-2 text-sm text-gray-700 hover:border-red-200 hover:bg-red-50 hover:text-red-700"
          >
            Sign out
          </button>
        </div>
      </section>
    </div>
  );
}
