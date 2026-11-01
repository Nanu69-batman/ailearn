"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useCallback, useEffect, useState, type FormEvent } from "react";

import {
  classInviteUrl,
  createSubject,
  deleteClass,
  formatApiError,
  getClass,
  regenerateClassCode,
  removeClassMember,
  type ClassInfo,
} from "@/lib/api";
import { subjectPath } from "@/lib/learn";
import { getSupabaseBrowserClient } from "@/lib/supabase/client";

const INPUT =
  "w-full rounded-xl border border-gray-200 bg-white px-3.5 py-2.5 text-sm text-gray-900 placeholder-gray-400 outline-none focus:border-violet-400 focus:ring-2 focus:ring-violet-200";
const BUTTON =
  "rounded-xl bg-gradient-to-r from-indigo-500 to-violet-500 px-4 py-2.5 text-sm font-medium text-white shadow-sm hover:opacity-90 disabled:opacity-60";
const GHOST =
  "rounded-xl border border-gray-200 bg-white px-3 py-2 text-sm font-medium text-gray-700 hover:border-violet-300 disabled:opacity-60";

export default function ClassDetailPage() {
  const { classId } = useParams<{ classId: string }>();
  const router = useRouter();
  const [klass, setKlass] = useState<ClassInfo | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [subjectName, setSubjectName] = useState("");
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState(false);

  const load = useCallback(async () => {
    try {
      setKlass(await getClass(classId));
    } catch (err) {
      setError(formatApiError(err));
    }
  }, [classId]);

  useEffect(() => {
    void load();
  }, [load]);

  async function run(fn: () => Promise<unknown>) {
    setBusy(true);
    setError(null);
    try {
      await fn();
      await load();
    } catch (err) {
      setError(formatApiError(err));
    } finally {
      setBusy(false);
    }
  }

  async function onAddSubject(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (!subjectName.trim()) return;
    await run(async () => {
      await createSubject({ name: subjectName.trim(), class_id: classId });
      setSubjectName("");
    });
  }

  async function copyLink() {
    if (!klass?.join_code) return;
    try {
      await navigator.clipboard.writeText(classInviteUrl(klass.join_code));
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      setError("Could not copy. Select the link and copy it manually.");
    }
  }

  if (!klass) {
    return (
      <div className="mx-auto max-w-4xl p-6">
        {error ? (
          <p role="alert" className="text-sm text-red-700">{error}</p>
        ) : (
          <p className="text-sm text-gray-500">Loading…</p>
        )}
        <Link href="/classes" className="mt-4 inline-block text-sm text-violet-700 hover:underline">
          ← All classes
        </Link>
      </div>
    );
  }

  const teacher = klass.is_teacher;

  return (
    <div className="mx-auto max-w-4xl p-6">
      <Link href="/classes" className="text-sm text-violet-700 hover:underline">
        ← All classes
      </Link>
      <div className="mt-2 flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">{klass.name}</h1>
          {klass.description && <p className="mt-1 text-sm text-gray-500">{klass.description}</p>}
          {!teacher && <p className="mt-1 text-xs text-gray-500">Teacher: {klass.teacher_name ?? "—"}</p>}
        </div>
        {teacher ? (
          <button
            type="button"
            className={`${GHOST} text-red-700 hover:border-red-300`}
            disabled={busy}
            onClick={() => {
              if (!confirm(`Delete "${klass.name}"? Students lose access; your subjects stay in My Subjects.`)) return;
              void deleteClass(klass.id)
                .then(() => router.push("/classes"))
                .catch((err) => setError(formatApiError(err)));
            }}
          >
            Delete class
          </button>
        ) : (
          <button
            type="button"
            className={GHOST}
            disabled={busy}
            onClick={() => {
              if (!confirm(`Leave "${klass.name}"?`)) return;
              void (async () => {
                try {
                  const { data } = (await getSupabaseBrowserClient()?.auth.getUser()) ?? { data: { user: null } };
                  const sub = data.user?.id;
                  if (!sub) throw new Error("Not signed in");
                  await removeClassMember(klass.id, sub);
                  router.push("/classes");
                } catch (err) {
                  setError(formatApiError(err));
                }
              })();
            }}
          >
            Leave class
          </button>
        )}
      </div>

      {error && (
        <p role="alert" className="mt-4 rounded-xl border border-red-200 bg-red-50 px-3.5 py-2.5 text-sm text-red-700">
          {error}
        </p>
      )}

      {teacher && klass.join_code && (
        <section className="mt-6 rounded-2xl border border-violet-200 bg-violet-50 p-5">
          <h2 className="text-sm font-semibold text-violet-900">Invite students</h2>
          <p className="mt-1 text-xs text-violet-800">
            Share this link. Students sign up (or sign in) with a student account and join with one click.
          </p>
          <div className="mt-3 flex flex-col gap-2 sm:flex-row">
            <input readOnly className={`${INPUT} font-mono`} value={classInviteUrl(klass.join_code)} onFocus={(e) => e.currentTarget.select()} />
            <button type="button" className={BUTTON} onClick={copyLink}>
              {copied ? "Copied!" : "Copy link"}
            </button>
            <button
              type="button"
              className={GHOST}
              disabled={busy}
              onClick={() => {
                if (confirm("Create a new link? The current link will stop working.")) {
                  void run(() => regenerateClassCode(klass.id));
                }
              }}
            >
              New link
            </button>
          </div>
          <p className="mt-2 text-xs text-violet-800">
            Code: <span className="font-mono font-semibold">{klass.join_code}</span>
          </p>
        </section>
      )}

      <section className="mt-6">
        <h2 className="text-lg font-semibold text-gray-900">Subjects</h2>
        {teacher && (
          <form onSubmit={onAddSubject} className="mt-3 flex flex-col gap-2 sm:flex-row">
            <input className={INPUT} placeholder="New subject name" maxLength={200} value={subjectName} onChange={(e) => setSubjectName(e.target.value)} />
            <button type="submit" className={BUTTON} disabled={busy || !subjectName.trim()}>
              Add subject
            </button>
          </form>
        )}
        <div className="mt-3 grid gap-3 sm:grid-cols-2">
          {klass.subjects.length === 0 ? (
            <p className="text-sm text-gray-500">
              {teacher ? "No subjects yet. Add one, then upload videos and documents to it." : "Your teacher has not added subjects yet."}
            </p>
          ) : (
            klass.subjects.map((s) => (
              <Link
                key={s.id}
                href={subjectPath(s.id)}
                className="rounded-2xl border border-gray-200 bg-white p-4 shadow-sm transition hover:border-violet-300"
              >
                <p className="font-semibold text-gray-900">{s.name}</p>
                <p className="mt-1 text-xs text-gray-500">
                  {s.resource_count} resource{s.resource_count === 1 ? "" : "s"}
                  {teacher ? " · open to add resources" : ""}
                </p>
              </Link>
            ))
          )}
        </div>
      </section>

      {teacher && (
        <section className="mt-8">
          <h2 className="text-lg font-semibold text-gray-900">
            Students <span className="text-sm font-normal text-gray-500">({klass.students?.length ?? 0})</span>
          </h2>
          {(klass.students?.length ?? 0) === 0 ? (
            <p className="mt-2 text-sm text-gray-500">No students yet. Share the invite link above.</p>
          ) : (
            <ul className="mt-3 divide-y divide-gray-100 rounded-2xl border border-gray-200 bg-white">
              {klass.students!.map((m) => (
                <li key={m.id} className="flex items-center justify-between gap-3 px-4 py-3">
                  <div className="min-w-0">
                    <p className="truncate text-sm font-medium text-gray-900">{m.name ?? m.email ?? "Student"}</p>
                    <p className="truncate text-xs text-gray-500">
                      {m.email ?? ""} · joined {new Date(m.joined_at).toLocaleDateString()}
                    </p>
                  </div>
                  <button
                    type="button"
                    className={`${GHOST} text-red-700 hover:border-red-300`}
                    disabled={busy}
                    onClick={() => {
                      if (confirm(`Remove ${m.name ?? m.email ?? "this student"} from the class?`)) {
                        void run(() => removeClassMember(klass.id, m.id));
                      }
                    }}
                  >
                    Remove
                  </button>
                </li>
              ))}
            </ul>
          )}
        </section>
      )}
    </div>
  );
}
