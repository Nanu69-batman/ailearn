"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { formatApiError, joinClass, previewClassInvite, type ClassPreview } from "@/lib/api";
import { useRole } from "@/lib/role";

/** Invite landing: /join/<code>. Signed-out visitors are sent to login first. */
export default function JoinClassPage() {
  const { code } = useParams<{ code: string }>();
  const router = useRouter();
  const role = useRole();
  const [preview, setPreview] = useState<ClassPreview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    previewClassInvite(code)
      .then(setPreview)
      .catch((err) => setError(formatApiError(err)));
  }, [code]);

  async function onJoin() {
    setBusy(true);
    setError(null);
    try {
      const klass = await joinClass(code);
      router.push(`/classes/${klass.id}`);
    } catch (err) {
      setError(formatApiError(err));
      setBusy(false);
    }
  }

  return (
    <div className="mx-auto flex max-w-md flex-col p-6">
      <div className="rounded-2xl border border-gray-200 bg-white p-6 shadow-sm">
        <p className="text-xs font-semibold uppercase tracking-wide text-violet-600">Class invite</p>
        {!preview && !error && <p className="mt-3 text-sm text-gray-500">Loading…</p>}
        {preview && (
          <>
            <h1 className="mt-2 text-xl font-bold text-gray-900">{preview.name}</h1>
            {preview.description && <p className="mt-1 text-sm text-gray-500">{preview.description}</p>}
            <p className="mt-2 text-xs text-gray-500">
              Teacher: {preview.teacher_name ?? "—"} · {preview.student_count} student
              {preview.student_count === 1 ? "" : "s"}
            </p>
            {preview.is_teacher ? (
              <Link href={`/classes/${preview.id}`} className="mt-5 inline-block text-sm font-medium text-violet-700 hover:underline">
                You teach this class. Open it →
              </Link>
            ) : preview.already_member ? (
              <Link href={`/classes/${preview.id}`} className="mt-5 inline-block text-sm font-medium text-violet-700 hover:underline">
                You are already in this class. Open it →
              </Link>
            ) : role !== null && role !== "student" ? (
              <p className="mt-5 rounded-xl border border-amber-200 bg-amber-50 px-3.5 py-3 text-sm text-amber-800">
                Only student accounts can join a class. You are signed in with a {role} account.
              </p>
            ) : (
              <button
                type="button"
                onClick={onJoin}
                disabled={busy || role === null}
                className="mt-5 w-full rounded-xl bg-gradient-to-r from-indigo-500 to-violet-500 px-5 py-2.5 text-sm font-medium text-white shadow-sm hover:opacity-90 disabled:opacity-60"
              >
                {busy ? "Joining…" : "Join class"}
              </button>
            )}
          </>
        )}
        {error && (
          <p role="alert" className="mt-4 rounded-xl border border-red-200 bg-red-50 px-3.5 py-2.5 text-sm text-red-700">
            {error}
          </p>
        )}
      </div>
    </div>
  );
}
