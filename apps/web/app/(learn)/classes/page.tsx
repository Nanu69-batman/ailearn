"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState, type FormEvent } from "react";

import {
  createClass,
  formatApiError,
  listClasses,
  type ClassInfo,
} from "@/lib/api";
import { useRole } from "@/lib/role";

const INPUT =
  "w-full rounded-xl border border-gray-200 bg-white px-3.5 py-2.5 text-sm text-gray-900 placeholder-gray-400 outline-none focus:border-violet-400 focus:ring-2 focus:ring-violet-200";
const BUTTON =
  "rounded-xl bg-gradient-to-r from-indigo-500 to-violet-500 px-4 py-2.5 text-sm font-medium text-white shadow-sm hover:opacity-90 disabled:opacity-60";

/** Pull a join code out of a pasted invite link or a bare code. */
function extractCode(input: string): string {
  const trimmed = input.trim();
  const m = trimmed.match(/\/join\/([A-Za-z0-9]+)/);
  return (m ? m[1] : trimmed).toUpperCase();
}

export default function ClassesPage() {
  const role = useRole();
  const router = useRouter();
  const [classes, setClasses] = useState<ClassInfo[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [invite, setInvite] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      setClasses((await listClasses()).classes);
    } catch (err) {
      setError(formatApiError(err));
      setClasses([]);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function onCreate(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (!name.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const klass = await createClass({ name: name.trim(), description: description.trim() });
      router.push(`/classes/${klass.id}`);
    } catch (err) {
      setError(formatApiError(err));
      setBusy(false);
    }
  }

  function onJoin(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const code = extractCode(invite);
    if (code) router.push(`/join/${code}`);
  }

  if (role === null) return null;

  if (role === "individual") {
    return (
      <div className="mx-auto max-w-2xl p-6">
        <h1 className="text-2xl font-bold text-gray-900">Classes</h1>
        <p className="mt-2 text-sm text-gray-600">
          Classes are for teacher and student accounts. Your account is set up for learning on your
          own, so your subjects live in My Subjects.
        </p>
      </div>
    );
  }

  const teacher = role === "teacher";

  return (
    <div className="mx-auto max-w-4xl p-6">
      <h1 className="text-2xl font-bold text-gray-900">{teacher ? "Your classes" : "My classes"}</h1>
      <p className="mt-1 text-sm text-gray-500">
        {teacher
          ? "Create a class, share its invite link with your students, and add subjects and resources for them."
          : "Classes you have joined. Your teacher manages the subjects and resources."}
      </p>

      {error && (
        <p role="alert" className="mt-4 rounded-xl border border-red-200 bg-red-50 px-3.5 py-2.5 text-sm text-red-700">
          {error}
        </p>
      )}

      {teacher ? (
        <form onSubmit={onCreate} className="mt-6 grid gap-3 rounded-2xl border border-gray-200 bg-white p-5 shadow-sm sm:grid-cols-[1fr_1fr_auto]">
          <input className={INPUT} placeholder="Class name, e.g. Physics 11-A" value={name} maxLength={200} onChange={(e) => setName(e.target.value)} required />
          <input className={INPUT} placeholder="Description (optional)" value={description} maxLength={2000} onChange={(e) => setDescription(e.target.value)} />
          <button type="submit" disabled={busy || !name.trim()} className={BUTTON}>
            {busy ? "Creating…" : "Create class"}
          </button>
        </form>
      ) : (
        <form onSubmit={onJoin} className="mt-6 flex flex-col gap-3 rounded-2xl border border-gray-200 bg-white p-5 shadow-sm sm:flex-row">
          <input className={INPUT} placeholder="Paste an invite link or code" value={invite} onChange={(e) => setInvite(e.target.value)} required />
          <button type="submit" disabled={!invite.trim()} className={BUTTON}>
            Join class
          </button>
        </form>
      )}

      <div className="mt-6 grid gap-4 sm:grid-cols-2">
        {classes === null ? (
          <p className="text-sm text-gray-500">Loading…</p>
        ) : classes.length === 0 ? (
          <p className="text-sm text-gray-500">
            {teacher ? "No classes yet. Create your first one above." : "You have not joined a class yet."}
          </p>
        ) : (
          classes.map((c) => (
            <Link
              key={c.id}
              href={`/classes/${c.id}`}
              className="rounded-2xl border border-gray-200 bg-white p-5 shadow-sm transition hover:border-violet-300 hover:shadow"
            >
              <p className="text-base font-semibold text-gray-900">{c.name}</p>
              {c.description && <p className="mt-1 line-clamp-2 text-sm text-gray-500">{c.description}</p>}
              <p className="mt-3 text-xs text-gray-500">
                {teacher
                  ? `${c.student_count} student${c.student_count === 1 ? "" : "s"}`
                  : `Teacher: ${c.teacher_name ?? "—"}`}
                {" · "}
                {c.subjects.length} subject{c.subjects.length === 1 ? "" : "s"}
              </p>
            </Link>
          ))
        )}
      </div>
    </div>
  );
}
