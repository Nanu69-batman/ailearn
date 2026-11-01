"use client";

import { useEffect, useState, type FormEvent } from "react";
import { useRouter, useSearchParams } from "next/navigation";

import { RolePicker } from "@/components/RolePicker";
import { saveRole, type Role } from "@/lib/role";
import { getSupabaseBrowserClient, safeNextPath } from "@/lib/supabase/client";

const INPUT_CLASS =
  "w-full rounded-xl border border-gray-200 bg-white px-3.5 py-2.5 text-sm text-gray-900 placeholder-gray-400 outline-none transition focus:border-violet-400 focus:ring-2 focus:ring-violet-200";

/** Shown once to accounts with no role yet (Google sign-in, older accounts). */
export default function OnboardingForm() {
  const router = useRouter();
  const next = safeNextPath(useSearchParams().get("next"));
  const [name, setName] = useState("");
  const [role, setRole] = useState<Role>("individual");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    void getSupabaseBrowserClient()
      ?.auth.getUser()
      .then(({ data }) => {
        const meta = (data.user?.user_metadata ?? {}) as Record<string, unknown>;
        const n = meta.full_name ?? meta.name;
        if (typeof n === "string") setName((cur) => cur || n);
      });
  }, []);

  async function onSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (!name.trim()) {
      setError("Please enter your name.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await saveRole(name.trim(), role);
      router.replace(next);
      router.refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not save. Please try again.");
      setBusy(false);
    }
  }

  return (
    <div className="w-full max-w-sm rounded-2xl border border-gray-200 bg-white p-8 shadow-sm">
      <h1 className="text-xl font-bold text-gray-900">Set up your account</h1>
      <p className="mt-1 text-sm text-gray-500">Tell us who you are so we can set things up.</p>
      <form onSubmit={onSubmit} className="mt-6 flex flex-col gap-3">
        <label className="flex flex-col gap-1 text-sm font-medium text-gray-700">
          Name
          <input
            type="text"
            required
            maxLength={100}
            value={name}
            onChange={(e) => setName(e.target.value)}
            className={INPUT_CLASS}
            placeholder="Your full name"
          />
        </label>
        <div className="flex flex-col gap-1 text-sm font-medium text-gray-700">
          I am a…
          <RolePicker value={role} onChange={setRole} />
        </div>
        {error && (
          <p role="alert" className="rounded-xl border border-red-200 bg-red-50 px-3.5 py-2.5 text-sm text-red-700">
            {error}
          </p>
        )}
        <button
          type="submit"
          disabled={busy}
          className="mt-1 rounded-xl bg-gradient-to-r from-indigo-500 to-violet-500 px-5 py-2.5 text-sm font-medium text-white shadow-sm transition hover:opacity-90 disabled:opacity-60"
        >
          {busy ? "Saving…" : "Continue"}
        </button>
      </form>
    </div>
  );
}
