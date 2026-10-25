"use client";

import { useState, type FormEvent } from "react";
import { useRouter, useSearchParams } from "next/navigation";

import {
  getSupabaseBrowserClient,
  safeNextPath,
  supabaseConfigured,
} from "@/lib/supabase/client";

type Mode = "signin" | "signup";

const INPUT_CLASS =
  "w-full rounded-xl border border-gray-200 bg-white px-3.5 py-2.5 text-sm text-gray-900 placeholder-gray-400 outline-none transition focus:border-violet-400 focus:ring-2 focus:ring-violet-200";

export default function LoginForm() {
  const router = useRouter();
  const params = useSearchParams();
  const next = safeNextPath(params.get("next"));

  const [mode, setMode] = useState<Mode>("signin");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(
    params.get("error") ? "Sign-in could not be completed. Please try again." : null,
  );
  const [checkEmail, setCheckEmail] = useState(false);

  const configured = supabaseConfigured();
  const callbackUrl = (): string =>
    `${window.location.origin}/auth/callback?next=${encodeURIComponent(next)}`;

  async function onSubmit(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    const supabase = getSupabaseBrowserClient();
    if (!supabase) return;
    setBusy(true);
    setError(null);
    try {
      if (mode === "signin") {
        const { error: err } = await supabase.auth.signInWithPassword({ email, password });
        if (err) {
          setError(err.message);
          return;
        }
        router.replace(next);
        router.refresh();
        return;
      }
      const { data, error: err } = await supabase.auth.signUp({
        email,
        password,
        options: { emailRedirectTo: callbackUrl() },
      });
      if (err) {
        setError(err.message);
        return;
      }
      if (data.session) {
        // Email confirmation is off for this project: already signed in.
        router.replace(next);
        router.refresh();
      } else {
        setCheckEmail(true);
      }
    } finally {
      setBusy(false);
    }
  }

  async function onGoogle(): Promise<void> {
    const supabase = getSupabaseBrowserClient();
    if (!supabase) return;
    setBusy(true);
    setError(null);
    const { error: err } = await supabase.auth.signInWithOAuth({
      provider: "google",
      options: { redirectTo: callbackUrl() },
    });
    if (err) {
      setError(err.message);
      setBusy(false);
    }
  }

  return (
    <div className="w-full max-w-sm rounded-2xl border border-gray-200 bg-white p-8 shadow-sm">
      <div className="flex items-center gap-3">
        <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-gradient-to-br from-indigo-500 to-violet-500 text-lg font-bold text-white">
          M
        </span>
        <div>
          <p className="text-sm font-bold leading-tight text-gray-900">Malai Pioneer</p>
          <p className="text-[11px] text-gray-500">AI Learning Assistant</p>
        </div>
      </div>

      <h1 className="mt-6 text-xl font-bold text-gray-900">
        {mode === "signin" ? "Sign in" : "Create your account"}
      </h1>
      <p className="mt-1 text-sm text-gray-500">
        {mode === "signin"
          ? "Welcome back. Pick up where you left off."
          : "Keep your subjects, chats and progress in one place."}
      </p>

      {!configured ? (
        <p className="mt-6 rounded-xl border border-amber-200 bg-amber-50 px-3.5 py-3 text-sm text-amber-800">
          Sign-in is not configured for this deployment.{" "}
          <a href="/dashboard" className="font-medium underline">
            Continue to the dashboard
          </a>
          .
        </p>
      ) : checkEmail ? (
        <div className="mt-6 rounded-xl border border-violet-200 bg-violet-50 px-3.5 py-3 text-sm text-violet-800">
          <p className="font-semibold">Check your email</p>
          <p className="mt-1">
            We sent a confirmation link to <span className="font-medium">{email}</span>. Open it
            to finish creating your account.
          </p>
          <button
            type="button"
            onClick={() => {
              setCheckEmail(false);
              setMode("signin");
            }}
            className="mt-3 text-sm font-medium text-violet-700 underline"
          >
            Back to sign in
          </button>
        </div>
      ) : (
        <>
          <button
            type="button"
            onClick={onGoogle}
            disabled={busy}
            className="mt-6 flex w-full items-center justify-center gap-2.5 rounded-xl border border-gray-200 bg-white px-4 py-2.5 text-sm font-medium text-gray-800 shadow-sm transition hover:border-violet-300 hover:bg-gray-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-violet-400 disabled:opacity-60"
          >
            <svg viewBox="0 0 24 24" className="h-4 w-4" aria-hidden="true">
              <path fill="#4285F4" d="M23.5 12.3c0-.8-.1-1.6-.2-2.3H12v4.5h6.5a5.6 5.6 0 01-2.4 3.6v3h3.9c2.2-2.1 3.5-5.1 3.5-8.8z" />
              <path fill="#34A853" d="M12 24c3.2 0 6-1.1 7.9-2.9l-3.9-3c-1.1.7-2.4 1.2-4 1.2-3.1 0-5.7-2.1-6.7-4.9h-4v3.1A12 12 0 0012 24z" />
              <path fill="#FBBC05" d="M5.3 14.4a7.2 7.2 0 010-4.8V6.5h-4a12 12 0 000 11z" />
              <path fill="#EA4335" d="M12 4.8c1.8 0 3.3.6 4.6 1.8l3.4-3.4A12 12 0 001.3 6.5l4 3.1C6.3 6.9 8.9 4.8 12 4.8z" />
            </svg>
            Continue with Google
          </button>

          <div className="my-5 flex items-center gap-3 text-xs text-gray-400">
            <span className="h-px flex-1 bg-gray-200" />
            or
            <span className="h-px flex-1 bg-gray-200" />
          </div>

          <form onSubmit={onSubmit} className="flex flex-col gap-3">
            <label className="flex flex-col gap-1 text-sm font-medium text-gray-700">
              Email
              <input
                type="email"
                required
                autoComplete="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                className={INPUT_CLASS}
                placeholder="you@example.com"
              />
            </label>
            <label className="flex flex-col gap-1 text-sm font-medium text-gray-700">
              Password
              <input
                type="password"
                required
                minLength={6}
                autoComplete={mode === "signin" ? "current-password" : "new-password"}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className={INPUT_CLASS}
              />
            </label>

            {error && (
              <p
                role="alert"
                className="rounded-xl border border-red-200 bg-red-50 px-3.5 py-2.5 text-sm text-red-700"
              >
                {error}
              </p>
            )}

            <button
              type="submit"
              disabled={busy}
              className="mt-1 rounded-xl bg-gradient-to-r from-indigo-500 to-violet-500 px-5 py-2.5 text-sm font-medium text-white shadow-sm transition hover:opacity-90 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-600 disabled:opacity-60"
            >
              {busy ? "Please wait…" : mode === "signin" ? "Sign in" : "Create account"}
            </button>
          </form>

          <p className="mt-5 text-center text-sm text-gray-500">
            {mode === "signin" ? "New here? " : "Already have an account? "}
            <button
              type="button"
              onClick={() => {
                setMode(mode === "signin" ? "signup" : "signin");
                setError(null);
              }}
              className="font-medium text-violet-700 hover:underline"
            >
              {mode === "signin" ? "Create an account" : "Sign in"}
            </button>
          </p>
        </>
      )}
    </div>
  );
}
