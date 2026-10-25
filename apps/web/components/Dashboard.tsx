"use client";

import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import Link from "next/link";
import type { Subject } from "@/lib/api";
import {
  currentStreak,
  dayKey,
  formatStudyTime,
  readAccessed,
  readArchived,
  readLastVisit,
  readStudyDays,
  subjectProgress,
  type LastVisit,
} from "@/lib/activity";
import { LEARNER_NAME, SUBJECTS_PATH, contentPath } from "@/lib/learn";
import SubjectCard, { AddSubjectTile, ProgressBar, SubjectCardSkeleton } from "@/components/SubjectCard";
import SubjectCreateDialog from "@/components/SubjectCreateDialog";
import SubjectDeleteDialog from "@/components/SubjectDeleteDialog";
import { Notice, SubjectsError } from "@/components/SubjectList";
import { useSubjects } from "@/components/useSubjects";

/**
 * /dashboard: greeting, a year of study activity, the Malai entry point,
 * "continue learning", the next task, and the most recent subjects.
 *
 * Activity numbers are this browser's own records (lib/activity.ts); subjects
 * come from the API. Nothing here is a placeholder: with no history the cards
 * say so instead of showing made-up progress.
 */

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const DASHBOARD_SUBJECTS = 3;

interface Activity {
  days: Record<string, number>;
  last: LastVisit | null;
  accessed: Record<string, number>;
  archived: string[];
}

function timeGreeting(date: Date): string {
  const h = date.getHours();
  if (h < 12) return "Good morning";
  if (h < 18) return "Good afternoon";
  return "Good evening";
}

/** "Thu, 3 Oct 2026" */
function longDate(date: Date): string {
  return `${WEEKDAYS[date.getDay()]}, ${date.getDate()} ${MONTHS[date.getMonth()]} ${date.getFullYear()}`;
}

function cellTone(seconds: number | undefined, future: boolean): string {
  if (future) return "bg-gray-50";
  if (!seconds) return "bg-gray-100";
  if (seconds < 15 * 60) return "bg-violet-200";
  if (seconds < 30 * 60) return "bg-violet-300";
  if (seconds < 60 * 60) return "bg-violet-500";
  return "bg-violet-600";
}

function Heatmap({ days, today }: { days: Record<string, number>; today: Date }) {
  // Columns are weeks (Sunday first) covering the whole calendar year.
  const { weeks, monthCols } = useMemo(() => {
    const year = today.getFullYear();
    const start = new Date(year, 0, 1);
    start.setDate(start.getDate() - start.getDay());
    const end = new Date(year, 11, 31);
    const cols: Array<Array<Date | null>> = [];
    const firsts: Array<{ month: number; col: number }> = [];
    const cursor = new Date(start);
    while (cursor <= end) {
      const week: Array<Date | null> = [];
      for (let d = 0; d < 7; d++) {
        const inYear = cursor.getFullYear() === year;
        week.push(inYear ? new Date(cursor) : null);
        if (inYear && cursor.getDate() === 1) firsts.push({ month: cursor.getMonth(), col: cols.length });
        cursor.setDate(cursor.getDate() + 1);
      }
      cols.push(week);
    }
    return { weeks: cols, monthCols: firsts };
  }, [today]);

  const todayKey = dayKey(today);
  // One grid template for the month row and the day grid, so labels line up:
  // a label column, then one flexible column per week.
  const columns = `2rem repeat(${weeks.length}, minmax(7px, 1fr))`;

  // On narrow screens the year scrolls; start at the recent end, not January.
  const scrollRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollLeft = el.scrollWidth;
  }, []);

  return (
    <div ref={scrollRef} className="overflow-x-auto pb-1">
      <div className="min-w-[640px]">
        <div className="grid h-6 text-[11px] text-gray-500" style={{ gridTemplateColumns: columns, columnGap: "3px" }} aria-hidden="true">
          {/* Covers month names scrolling under the pinned weekday column. */}
          <span className="sticky left-0 z-10 -mr-[3px] bg-white" style={{ gridColumnStart: 1, gridRowStart: 1 }} />
          {monthCols.map(({ month, col }) => (
            <span key={month} style={{ gridColumnStart: col + 2, gridRowStart: 1 }} className="whitespace-nowrap">
              {MONTHS[month]}
            </span>
          ))}
        </div>
        <div
          className="grid grid-flow-col grid-rows-7"
          style={{ gridTemplateColumns: columns, gap: "3px" }}
          role="img"
          aria-label="Study activity for this year"
        >
          {/* The weekday column stays pinned while the weeks scroll under it. */}
          {WEEKDAYS.map((d, i) => (
            <span key={d} aria-hidden="true" className="sticky left-0 z-10 -mr-[3px] flex items-center bg-white text-[11px] leading-none text-gray-500">
              {i % 2 === 1 ? d : ""}
            </span>
          ))}
          {weeks.flatMap((week, w) =>
            week.map((date, d) => {
              if (!date) return <span key={`${w}-${d}`} />;
              const key = dayKey(date);
              const secs = days[key];
              const future = key > todayKey;
              return (
                <span
                  key={key}
                  title={`${date.getDate()} ${MONTHS[date.getMonth()]}: ${secs ? formatStudyTime(secs) + " studied" : "no study"}`}
                  className={`aspect-square w-full self-center rounded-[3px] ${cellTone(secs, future)} ${key === todayKey ? "ring-1 ring-violet-400" : ""}`}
                />
              );
            }),
          )}
        </div>
      </div>
    </div>
  );
}

function Stat({ icon, value, label }: { icon: ReactNode; value: string; label: string }) {
  return (
    <div className="flex items-start gap-3">
      <span className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center">{icon}</span>
      <div>
        <p className="text-xl font-bold leading-tight text-gray-900 tabular-nums">{value}</p>
        <p className="text-sm text-gray-600">{label}</p>
      </div>
    </div>
  );
}

function ArrowCircle({ href, label }: { href: string; label: string }) {
  return (
    <Link
      href={href}
      aria-label={label}
      className="flex h-11 w-11 shrink-0 items-center justify-center rounded-full border border-gray-200 bg-white text-violet-700 shadow-sm transition hover:border-violet-300 hover:bg-violet-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-violet-400"
    >
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" className="h-5 w-5" aria-hidden="true">
        <path d="M9 6l6 6-6 6" />
      </svg>
    </Link>
  );
}

export default function Dashboard() {
  const { apiLive, subjects, state, error, load, added, removed } = useSubjects();
  const [now, setNow] = useState<Date | null>(null);
  const [activity, setActivity] = useState<Activity | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  const [pendingDelete, setPendingDelete] = useState<Subject | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  // Clock and browser records are read after mount so server and client HTML agree.
  useEffect(() => {
    setNow(new Date());
    setActivity({
      days: readStudyDays(),
      last: readLastVisit(),
      accessed: readAccessed(),
      archived: readArchived(),
    });
  }, []);

  useEffect(() => {
    if (!notice) return;
    const t = window.setTimeout(() => setNotice(null), 3500);
    return () => window.clearTimeout(t);
  }, [notice]);

  const closeCreate = useCallback(() => setCreateOpen(false), []);
  const closeDelete = useCallback(() => setPendingDelete(null), []);
  const onCreated = useCallback(
    (created: Subject): void => {
      added(created);
      setNotice(`“${created.name}” created.`);
      setCreateOpen(false);
    },
    [added],
  );
  const onDeleted = useCallback(
    (id: string): void => {
      const gone = subjects.find((s) => s.id === id);
      if (gone) setNotice(`“${gone.name}” removed. Its materials stay indexed.`);
      removed(id);
      setPendingDelete(null);
    },
    [subjects, removed],
  );

  const days = activity?.days ?? {};
  const studyDays = Object.keys(days).length;
  const totalSeconds = Object.values(days).reduce((a, b) => a + b, 0);
  const streak = now ? currentStreak(days, now) : 0;

  // The last lesson only counts while its subject still exists.
  const last = activity?.last ?? null;
  const lastSubject = last ? subjects.find((s) => s.id === last.subjectId) ?? null : null;
  const continueVisit = last && (state !== "ready" || lastSubject) ? last : null;
  const continueHref = continueVisit ? contentPath(continueVisit.subjectId, continueVisit.resourceId) : SUBJECTS_PATH;
  const continuePct = lastSubject ? subjectProgress(lastSubject) : null;

  const recent = useMemo(() => {
    const accessed = activity?.accessed ?? {};
    const archived = activity?.archived ?? [];
    return subjects
      .filter((s) => !archived.includes(s.id))
      .sort((a, b) => (accessed[b.id] ?? 0) - (accessed[a.id] ?? 0) || (b.created_at ?? "").localeCompare(a.created_at ?? ""))
      .slice(0, DASHBOARD_SUBJECTS);
  }, [subjects, activity]);

  return (
    <div className="flex flex-col gap-8">
      {/* Greeting */}
      <div className="flex flex-col gap-2 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h1 className="text-3xl font-bold tracking-tight text-gray-900 sm:text-4xl">
            {now ? timeGreeting(now) : "Welcome back"}, {LEARNER_NAME} <span aria-hidden="true">👋</span>
          </h1>
          <p className="mt-2 text-base text-gray-500">Keep your learning momentum going.</p>
        </div>
        {now && <p className="text-sm text-gray-600 sm:pb-1">{longDate(now)}</p>}
      </div>

      {/* Learning activity */}
      <section aria-labelledby="activity-title" className="grid overflow-hidden rounded-2xl border border-gray-200 bg-white shadow-sm xl:grid-cols-[minmax(0,1fr)_auto_16rem]">
        <div className="min-w-0 p-6">
          <div className="flex items-start gap-4">
            <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-violet-100 text-violet-700">
              <svg viewBox="0 0 24 24" fill="currentColor" className="h-5 w-5" aria-hidden="true">
                <rect x="4" y="12" width="4" height="8" rx="1" />
                <rect x="10" y="6" width="4" height="14" rx="1" />
                <rect x="16" y="9" width="4" height="11" rx="1" />
              </svg>
            </span>
            <div>
              <h2 id="activity-title" className="text-lg font-semibold text-gray-900">Your learning activity</h2>
              <p className="text-sm text-gray-600">Consistency leads to real progress. Keep showing up!</p>
            </div>
          </div>
          <div className="mt-5">{now && <Heatmap days={days} today={now} />}</div>
        </div>

        <div className="flex flex-row flex-wrap justify-around gap-6 border-t border-gray-100 p-6 xl:flex-col xl:justify-center xl:border-l xl:border-t-0 xl:px-8">
          <Stat
            icon={<svg viewBox="0 0 24 24" className="h-7 w-7" aria-hidden="true"><path d="M12 2c1 4 5 6 5 11a5 5 0 01-10 0c0-2 1-3.5 2-4.5 0 2 1 3 2 3 0-3-1-6 1-9.5z" fill="#f97316" /><path d="M12 13c1 1.5 2 2.5 2 4a2 2 0 01-4 0c0-1.2.8-2.2 2-4z" fill="#fde047" /></svg>}
            value={String(streak)}
            label="day streak"
          />
          <Stat
            icon={<svg viewBox="0 0 24 24" fill="#2563eb" className="h-6 w-6" aria-hidden="true"><rect x="4" y="12" width="4" height="9" rx="1" /><rect x="10" y="5" width="4" height="16" rx="1" /><rect x="16" y="9" width="4" height="12" rx="1" /></svg>}
            value={String(studyDays)}
            label="total study days"
          />
          <Stat
            icon={<svg viewBox="0 0 24 24" fill="none" stroke="#2563eb" strokeWidth="2.2" strokeLinecap="round" className="h-6 w-6" aria-hidden="true"><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" /></svg>}
            value={formatStudyTime(totalSeconds)}
            label="time spent"
          />
        </div>

        <div className="relative hidden overflow-hidden bg-gradient-to-br from-white to-violet-50 p-8 xl:block xl:border-l xl:border-gray-100">
          <p className="relative text-lg font-medium leading-snug text-violet-700">
            “A little progress every day adds up to big results.”
          </p>
          <svg viewBox="0 0 200 100" preserveAspectRatio="none" className="absolute inset-x-0 bottom-0 h-28 w-full" aria-hidden="true">
            <path d="M0 100 L60 55 L95 75 L150 20 L200 60 L200 100 Z" fill="#ede9fe" />
            <path d="M0 100 L70 70 L110 85 L160 45 L200 75 L200 100 Z" fill="#ddd6fe" opacity="0.7" />
          </svg>
        </div>
      </section>

      {/* Malai / continue / next task */}
      <div className="grid grid-cols-1 gap-5 lg:grid-cols-3">
        <section className="relative flex overflow-hidden rounded-2xl border border-violet-100 bg-gradient-to-br from-violet-50 via-white to-violet-100 p-6 shadow-sm">
          <div className="relative z-10 flex flex-col">
            <p className="flex items-center gap-2 text-sm font-semibold text-violet-700">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" className="h-5 w-5" aria-hidden="true">
                <path d="M12 2a7 7 0 00-7 7c0 2.4 1.2 4.5 3 5.7V17a2 2 0 002 2h4a2 2 0 002-2v-2.3c1.8-1.2 3-3.3 3-5.7a7 7 0 00-7-7zM9 22h6" />
              </svg>
              Malai
            </p>
            <h2 className="mt-2 text-xl font-bold text-gray-900">Your learning companion</h2>
            <p className="mt-1.5 text-sm text-gray-600">Get explanations, solve doubts, practice questions and more.</p>
            <Link
              href={continueHref}
              className="mt-5 inline-flex w-fit items-center gap-2 rounded-xl bg-violet-600 px-4 py-2.5 text-sm font-semibold text-white shadow-sm shadow-violet-600/30 transition hover:bg-violet-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-violet-500 focus-visible:ring-offset-2"
            >
              Chat with Malai
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" className="h-4 w-4" aria-hidden="true">
                <path d="M5 12h14M13 6l6 6-6 6" />
              </svg>
            </Link>
          </div>
        </section>

        <section className="flex gap-5 rounded-2xl border border-gray-200 bg-white p-6 shadow-sm">
          <span className="flex h-12 w-12 shrink-0 items-center justify-center rounded-xl bg-emerald-50 text-emerald-600">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" className="h-6 w-6" aria-hidden="true">
              <path d="M2 5a2 2 0 012-2h5a3 3 0 013 3v15a2 2 0 00-2-2H2zM22 5a2 2 0 00-2-2h-5a3 3 0 00-3 3v15a2 2 0 012-2h8z" />
            </svg>
          </span>
          <div className="flex min-w-0 flex-1 flex-col">
            <h2 className="text-base font-semibold text-gray-900">Continue learning</h2>
            {continueVisit ? (
              <>
                <p className="mt-2 truncate text-base text-gray-800">{lastSubject?.name ?? continueVisit.subjectName ?? "Your subject"}</p>
                <p className="mt-0.5 truncate text-sm text-gray-600">{continueVisit.title}</p>
              </>
            ) : (
              <>
                <p className="mt-2 text-base text-gray-800">Nothing in progress yet</p>
                <p className="mt-0.5 text-sm text-gray-600">Open a subject to start your first lesson.</p>
              </>
            )}
            <div className="mt-auto flex items-center gap-4 pt-5">
              <div className="min-w-0 flex-1">{continuePct !== null && continueVisit && <ProgressBar value={continuePct} />}</div>
              <ArrowCircle href={continueHref} label={continueVisit ? "Continue learning" : "Open my subjects"} />
            </div>
          </div>
        </section>

        <section className="flex gap-5 rounded-2xl border border-amber-100 bg-gradient-to-br from-amber-50/70 to-white p-6 shadow-sm">
          <span className="flex h-12 w-12 shrink-0 items-center justify-center rounded-xl bg-amber-100 text-amber-500">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="h-6 w-6" aria-hidden="true">
              <path d="M9 11l3 3L22 4M21 12v7a2 2 0 01-2 2H5a2 2 0 01-2-2V5a2 2 0 012-2h11" />
            </svg>
          </span>
          <div className="flex min-w-0 flex-1 flex-col">
            <h2 className="text-base font-semibold text-gray-900">Upcoming task</h2>
            {continueVisit ? (
              <>
                <p className="mt-2 truncate text-base text-gray-800">Practice quiz · {continueVisit.title}</p>
                <p className="mt-0.5 truncate text-sm text-gray-600">{lastSubject?.name ?? continueVisit.subjectName ?? ""}</p>
              </>
            ) : (
              <>
                <p className="mt-2 text-base text-gray-800">No tasks yet</p>
                <p className="mt-0.5 text-sm text-gray-600">A practice quiz appears once you start a lesson.</p>
              </>
            )}
            <div className="mt-auto flex items-center justify-between gap-4 pt-5">
              {continueVisit ? (
                <span className="inline-flex items-center gap-2 rounded-full bg-gray-100 px-3 py-1.5 text-sm text-gray-700">
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" className="h-4 w-4" aria-hidden="true">
                    <path d="M8 2v4M16 2v4M3 9h18M5 4h14a2 2 0 012 2v14a2 2 0 01-2 2H5a2 2 0 01-2-2V6a2 2 0 012-2z" />
                  </svg>
                  Ready when you are
                </span>
              ) : (
                <span />
              )}
              <ArrowCircle href={continueHref} label={continueVisit ? "Open the practice quiz" : "Open my subjects"} />
            </div>
          </div>
        </section>
      </div>

      {/* Subjects */}
      <section aria-labelledby="dash-subjects-title" className="flex flex-col gap-4">
        <div className="flex items-end justify-between gap-4">
          <div>
            <h2 id="dash-subjects-title" className="text-xl font-bold text-gray-900">Your subjects</h2>
            <p className="mt-1 text-sm text-gray-600">Open a subject to watch, read and ask questions about your own materials.</p>
          </div>
          <Link href={SUBJECTS_PATH} className="inline-flex shrink-0 items-center gap-1.5 text-sm font-semibold text-violet-700 transition hover:text-violet-900">
            View all
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" className="h-4 w-4" aria-hidden="true">
              <path d="M5 12h14M13 6l6 6-6 6" />
            </svg>
          </Link>
        </div>

        {notice && <Notice text={notice} />}

        {state === "loading" && (
          <ul className="grid grid-cols-1 gap-5 md:grid-cols-2 xl:grid-cols-3" aria-hidden="true">
            {Array.from({ length: DASHBOARD_SUBJECTS }, (_, i) => (
              <li key={i}><SubjectCardSkeleton /></li>
            ))}
          </ul>
        )}

        {state === "error" && <SubjectsError error={error} onRetry={apiLive ? () => void load() : undefined} />}

        {state === "ready" && (
          <ul className="grid grid-cols-1 gap-5 md:grid-cols-2 xl:grid-cols-3">
            {recent.map((s) => (
              <li key={s.id}>
                <SubjectCard
                  subject={s}
                  started={activity?.accessed[s.id] !== undefined}
                  onRequestDelete={setPendingDelete}
                />
              </li>
            ))}
            {apiLive && (
              <li>
                <AddSubjectTile onClick={() => setCreateOpen(true)} />
              </li>
            )}
          </ul>
        )}
      </section>

      <SubjectCreateDialog open={createOpen} onClose={closeCreate} onCreated={onCreated} />
      <SubjectDeleteDialog subject={pendingDelete} onClose={closeDelete} onDeleted={onDeleted} />
    </div>
  );
}
