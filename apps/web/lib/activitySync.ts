/**
 * Mirrors learner activity to Supabase (docs/SUPABASE_PLAN.md) for a
 * signed-in learner, so the streak, heatmap, progress and quiz history follow
 * the account instead of staying in one browser.
 *
 * localStorage (lib/activity.ts, lib/learn.ts) stays the synchronous source
 * the UI renders from. Writes go to both; `pullActivity()` merges the
 * account's rows back into localStorage on load. Every call is best-effort:
 * signed out, Supabase not configured, or a failed request all leave the
 * local records exactly as they were. RLS limits each row to its owner.
 */

import type { SupabaseClient } from "@supabase/supabase-js";

import { getSupabaseBrowserClient } from "@/lib/supabase/client";

async function signedIn(): Promise<{ supabase: SupabaseClient; userId: string } | null> {
  const supabase = getSupabaseBrowserClient();
  if (!supabase) return null;
  try {
    const { data } = await supabase.auth.getSession();
    const userId = data.session?.user.id;
    return userId ? { supabase, userId } : null;
  } catch {
    return null;
  }
}

function warn(what: string, err: unknown): void {
  console.warn(`activity sync: ${what} failed`, err);
}

export async function pushStudySeconds(day: string, seconds: number): Promise<void> {
  const s = await signedIn();
  if (!s) return;
  const { error } = await s.supabase.rpc("add_study_seconds", { p_day: day, p_seconds: seconds });
  if (error) warn("study seconds", error);
}

export async function pushVisit(visit: {
  subjectId: string;
  resourceId: string;
  title: string;
}): Promise<void> {
  const s = await signedIn();
  if (!s) return;
  const { error } = await s.supabase.from("lesson_progress").upsert(
    {
      user_id: s.userId,
      resource_id: visit.resourceId,
      subject_id: visit.subjectId,
      title: visit.title,
      last_opened_at: new Date().toISOString(),
    },
    { onConflict: "user_id,resource_id" },
  );
  if (error) warn("visit", error);
}

export async function pushCompletion(
  subjectId: string,
  changes: Array<{ resourceId: string; completed: boolean }>,
): Promise<void> {
  if (changes.length === 0) return;
  const s = await signedIn();
  if (!s) return;
  const { error } = await s.supabase.from("lesson_progress").upsert(
    changes.map((c) => ({
      user_id: s.userId,
      resource_id: c.resourceId,
      subject_id: subjectId,
      completed: c.completed,
    })),
    { onConflict: "user_id,resource_id" },
  );
  if (error) warn("completion", error);
}

export async function pushQuizAttempt(attempt: {
  resourceId: string | null;
  subjectId: string | null;
  score: number;
  total: number;
}): Promise<void> {
  const s = await signedIn();
  if (!s) return;
  const { error } = await s.supabase.from("quiz_attempts").insert({
    user_id: s.userId,
    resource_id: attempt.resourceId,
    subject_id: attempt.subjectId,
    score: attempt.score,
    total: attempt.total,
  });
  if (error) warn("quiz attempt", error);
}

export interface RemoteActivity {
  userId: string;
  /** { "YYYY-MM-DD": seconds } */
  days: Record<string, number>;
  progress: Array<{
    resourceId: string;
    subjectId: string | null;
    title: string | null;
    completed: boolean;
    lastOpenedAt: number;
  }>;
}

/** The account's activity, or null when signed out / unavailable. */
export async function pullActivity(): Promise<RemoteActivity | null> {
  const s = await signedIn();
  if (!s) return null;
  const [days, progress] = await Promise.all([
    s.supabase.from("study_activity").select("day, seconds"),
    s.supabase
      .from("lesson_progress")
      .select("resource_id, subject_id, title, completed, last_opened_at"),
  ]);
  if (days.error || progress.error) {
    warn("pull", days.error ?? progress.error);
    return null;
  }
  const out: RemoteActivity = { userId: s.userId, days: {}, progress: [] };
  for (const row of days.data ?? []) {
    if (typeof row.day === "string" && typeof row.seconds === "number") {
      out.days[row.day] = row.seconds;
    }
  }
  for (const row of progress.data ?? []) {
    out.progress.push({
      resourceId: row.resource_id,
      subjectId: row.subject_id ?? null,
      title: row.title ?? null,
      completed: Boolean(row.completed),
      lastOpenedAt: Date.parse(row.last_opened_at) || 0,
    });
  }
  return out;
}
