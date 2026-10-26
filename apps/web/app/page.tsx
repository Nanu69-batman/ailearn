import Link from "next/link";

import { DASHBOARD_PATH, contentPath } from "@/lib/learn";
import { getSupabaseServerClient } from "@/lib/supabase/server";

export const metadata = {
  title: "Malai Pioneer — Timestamp-aware AI learning assistant",
  description:
    "A personal AI tutor that follows where you are in a lecture or document, explains in context, quizzes you and cites every answer.",
};

/** The bundled demo lesson; public without signing in (middleware.ts). */
const DEMO_LESSON = contentPath("sub_06dea46b30bf4ee7", "res_2e629d682a3d");

const FOCUS =
  "focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-600";
const PRIMARY_BTN = `inline-flex items-center justify-center gap-1.5 rounded-xl bg-gradient-to-r from-indigo-500 to-violet-500 font-medium text-white shadow-sm shadow-violet-500/20 transition hover:opacity-90 ${FOCUS}`;
const SECONDARY_BTN = `inline-flex items-center justify-center gap-1.5 rounded-xl border border-gray-200 bg-white font-medium text-gray-700 shadow-sm transition hover:bg-gray-50 ${FOCUS}`;

const FEATURES = [
  {
    title: "A tutor, not a search box",
    body: "Ask in your own words and get step-by-step explanations, worked examples and follow-ups, the way a good tutor would explain it.",
    icon: "M7 8h10M7 12h6M4 4h16v12H8l-4 4z",
  },
  {
    title: "Timestamp-aware context",
    body: "The tutor knows the moment you are at in a lecture and the page you are on in your notes, and answers about exactly that.",
    icon: "M12 8v4l3 2M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18Z",
  },
  {
    title: "Cited, never made up",
    body: "Every answer is checked against your course material and cites the timestamp or page it came from. If the material does not cover it, the tutor says so.",
    icon: "M9 12l2 2 4-4M12 3l7 3v6c0 4.5-3 7.5-7 9-4-1.5-7-4.5-7-9V6z",
  },
  {
    title: "Quizzes on what you learned",
    body: "Ask for a quiz on any chapter or chat and get questions built from what you have covered so far, with an explanation for each answer.",
    icon: "M9 12l2 2 4-4M5 4h14v16H5z",
  },
  {
    title: "Any course material",
    body: "Lecture videos, YouTube links, PDFs and slide decks. Everything is split into chapters you can ask about.",
    icon: "M4 6h16M4 12h16M4 18h10",
  },
  {
    title: "Progress you can see",
    body: "A study heatmap, daily streak and lesson progress that follow your account, so you always know what to pick up next.",
    icon: "M4 20V10M10 20V4M16 20v-7M22 20H2",
  },
];

const STEPS = [
  { title: "Bring your course", body: "Create a subject and add its lectures, notes and slides." },
  {
    title: "Learn with your tutor",
    body: "Study at your own pace and ask whenever something is unclear. The tutor follows where you are.",
  },
  { title: "Check and keep going", body: "Quiz yourself on what you covered and keep your streak alive." },
];

const TIMELINE: Array<{ label: string; width: number; covered: boolean }> = [
  { label: "Intro", width: 18, covered: true },
  { label: "Weights", width: 24, covered: true },
  { label: "Activation", width: 22, covered: false },
  { label: "Training", width: 36, covered: false },
];

const TIMESTAMP_POINTS = [
  "Questions like “what did she just mean?” work, because the tutor sees the current frame and transcript.",
  "Nothing past your position is used, so there are no spoilers and no confusing jumps ahead.",
  "Every claim links back to a timestamp or page you can check yourself.",
];

function Icon({ d, className = "h-5 w-5" }: { d: string; className?: string }) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.8}
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      aria-hidden="true"
    >
      <path d={d} />
    </svg>
  );
}

async function isSignedIn(): Promise<boolean> {
  try {
    const supabase = await getSupabaseServerClient();
    if (!supabase) return false;
    const { data } = await supabase.auth.getUser();
    return Boolean(data.user);
  } catch {
    return false;
  }
}

/** A static picture of the lesson page: the video with a question beside it. */
function ProductPreview() {
  return (
    <div className="relative mx-auto mt-16 max-w-5xl">
      <div
        aria-hidden="true"
        className="absolute -inset-x-8 -top-8 bottom-0 -z-10 rounded-[2.5rem] bg-gradient-to-tr from-indigo-200/60 via-violet-200/50 to-fuchsia-200/40 blur-2xl"
      />
      <div className="overflow-hidden rounded-2xl border border-gray-200 bg-white shadow-xl shadow-indigo-900/10">
        <div className="flex items-center gap-1.5 border-b border-gray-100 bg-gray-50 px-4 py-2.5">
          <span className="h-2.5 w-2.5 rounded-full bg-gray-300" />
          <span className="h-2.5 w-2.5 rounded-full bg-gray-300" />
          <span className="h-2.5 w-2.5 rounded-full bg-gray-300" />
          <span className="ml-3 truncate text-xs text-gray-400">AI tutor · Neural networks · Chapter 2</span>
        </div>
        <div className="grid gap-0 md:grid-cols-[1.4fr_1fr]">
          <div className="border-b border-gray-100 p-4 md:border-b-0 md:border-r">
            <div className="relative flex aspect-video items-center justify-center overflow-hidden rounded-xl bg-gradient-to-br from-slate-800 to-indigo-950">
              <svg viewBox="0 0 200 100" className="h-3/5 w-3/5 text-indigo-300/70" aria-hidden="true">
                {[20, 50, 80].map((y) => (
                  <circle key={`a${y}`} cx="40" cy={y} r="6" fill="currentColor" />
                ))}
                {[35, 65].map((y) => (
                  <circle key={`b${y}`} cx="100" cy={y} r="6" fill="currentColor" />
                ))}
                <circle cx="160" cy="50" r="6" fill="currentColor" />
                {[20, 50, 80].flatMap((y1) =>
                  [35, 65].map((y2) => (
                    <line key={`${y1}-${y2}`} x1="46" y1={y1} x2="94" y2={y2} stroke="currentColor" strokeWidth="1" opacity="0.5" />
                  )),
                )}
                {[35, 65].map((y) => (
                  <line key={`o${y}`} x1="106" y1={y} x2="154" y2="50" stroke="currentColor" strokeWidth="1" opacity="0.5" />
                ))}
              </svg>
              <div className="absolute inset-x-3 bottom-3 flex items-center gap-2">
                <span className="flex h-6 w-6 items-center justify-center rounded-full bg-white/90 text-[10px] text-slate-800">❚❚</span>
                <div className="h-1 flex-1 rounded-full bg-white/20">
                  <div className="h-1 w-[42%] rounded-full bg-violet-400" />
                </div>
                <span className="font-mono text-[10px] text-white/70">04:12</span>
              </div>
            </div>
            <div className="mt-3 flex gap-2">
              {["Intro", "Weights", "Activation", "Training"].map((c, i) => (
                <span
                  key={c}
                  className={`rounded-lg px-2.5 py-1 text-[11px] font-medium ${
                    i === 1 ? "bg-violet-100 text-violet-700" : "bg-gray-100 text-gray-500"
                  }`}
                >
                  {c}
                </span>
              ))}
            </div>
          </div>
          <div className="flex flex-col gap-3 p-4 text-left text-[13px] leading-relaxed">
            <div className="flex items-center gap-1.5 self-start rounded-full bg-violet-50 px-2.5 py-1 text-[11px] font-medium text-violet-700">
              <span className="h-1.5 w-1.5 rounded-full bg-violet-500" />
              Context: up to 04:12 · slide 4
            </div>
            <div className="ml-auto max-w-[85%] rounded-2xl rounded-br-md bg-gradient-to-r from-indigo-500 to-violet-500 px-3.5 py-2 text-white">
              Why does the network need a bias term here?
            </div>
            <div className="max-w-[92%] rounded-2xl rounded-bl-md border border-gray-100 bg-gray-50 px-3.5 py-2.5 text-gray-700">
              The bias lets each neuron shift its activation, so it can fire even when all inputs are zero
              <span className="ml-1 inline-flex rounded-md bg-violet-100 px-1.5 font-mono text-[10px] font-semibold text-violet-700">
                03:48
              </span>
              . The slide on this frame shows it as the extra <em>b</em> in <span className="font-mono">w·x + b</span>
              <span className="ml-1 inline-flex rounded-md bg-indigo-100 px-1.5 font-mono text-[10px] font-semibold text-indigo-700">
                p. 4
              </span>
              .
            </div>
            <div className="mt-auto flex items-center gap-2 rounded-xl border border-gray-200 bg-white px-3 py-2 text-gray-400">
              <span className="flex-1">Ask your tutor anything…</span>
              <span className="rounded-md bg-gray-100 px-1.5 py-0.5 text-[10px]">/quiz</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

/** The problem statement, drawn: what the tutor may use at the learner's position. */
function TimestampSection() {
  return (
    <section id="timestamp" className="scroll-mt-20 border-t border-gray-200/70 bg-white px-4 py-20 sm:px-6">
      <div className="mx-auto grid max-w-6xl items-center gap-12 lg:grid-cols-2">
        <div>
          <span className="text-sm font-semibold uppercase tracking-wider text-violet-600">Timestamp-aware</span>
          <h2 className="mt-2 text-3xl font-bold tracking-tight sm:text-4xl">
            It answers for the moment you are in, not the whole course
          </h2>
          <p className="mt-4 leading-relaxed text-gray-600">
            A general chatbot knows nothing about your lesson, or answers from all of it at once and gives away
            what comes later. Malai Pioneer knows your position: the second of the lecture you paused on and the
            page you are reading. It builds each answer only from what you have already covered.
          </p>
          <ul className="mt-6 space-y-3 text-sm text-gray-700">
            {TIMESTAMP_POINTS.map((t) => (
              <li key={t} className="flex gap-3">
                <span className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-violet-100 text-violet-700">
                  <Icon d="M5 12l4 4L19 7" className="h-3.5 w-3.5" />
                </span>
                <span>{t}</span>
              </li>
            ))}
          </ul>
        </div>
        <div className="rounded-2xl border border-gray-200 bg-gray-50 p-6 shadow-sm">
          <div className="flex items-center justify-between text-xs text-gray-500">
            <span>Lecture timeline</span>
            <span className="font-mono">you are at 04:12</span>
          </div>
          <div className="relative mt-4">
            <div className="flex h-12 overflow-hidden rounded-xl">
              {TIMELINE.map((c) => (
                <div
                  key={c.label}
                  style={{ width: `${c.width}%` }}
                  className={`flex items-center justify-center border-r border-white text-[11px] font-medium last:border-r-0 ${
                    c.covered
                      ? "bg-gradient-to-r from-indigo-500 to-violet-500 text-white"
                      : "bg-gray-200 text-gray-400"
                  }`}
                >
                  {c.label}
                </div>
              ))}
            </div>
            <div className="absolute -bottom-2 -top-2 left-[42%] w-0.5 rounded bg-gray-900" aria-hidden="true">
              <span className="absolute -top-1 left-1/2 h-2.5 w-2.5 -translate-x-1/2 rounded-full bg-gray-900" />
            </div>
          </div>
          <div className="mt-6 grid gap-3 sm:grid-cols-2">
            <div className="rounded-xl border border-violet-200 bg-white p-4">
              <div className="text-xs font-semibold text-violet-700">The tutor can use</div>
              <p className="mt-1.5 text-sm text-gray-600">
                Transcript and frames up to 04:12, plus the notes and slides you have opened.
              </p>
            </div>
            <div className="rounded-xl border border-dashed border-gray-300 bg-white/60 p-4">
              <div className="text-xs font-semibold text-gray-500">Held back until you get there</div>
              <p className="mt-1.5 text-sm text-gray-500">
                Activation, Training, and everything after your position.
              </p>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}

export default async function Home() {
  const signedIn = await isSignedIn();

  return (
    <div className="min-h-screen bg-gray-50 text-gray-900">
      <header className="sticky top-0 z-30 border-b border-gray-200/70 bg-gray-50/80 backdrop-blur">
        <nav className="mx-auto flex h-16 max-w-6xl items-center justify-between gap-4 px-4 sm:px-6">
          <Link href="/" className={`flex items-center gap-2.5 rounded-lg ${FOCUS}`}>
            <span className="flex h-8 w-8 items-center justify-center rounded-xl bg-gradient-to-br from-indigo-500 to-violet-500 text-sm font-bold text-white">
              M
            </span>
            <span className="font-semibold tracking-tight">Malai Pioneer</span>
          </Link>
          <div className="hidden items-center gap-7 text-sm text-gray-600 md:flex">
            <a href="#timestamp" className="transition hover:text-gray-900">Timestamp-aware</a>
            <a href="#features" className="transition hover:text-gray-900">Features</a>
            <a href="#how" className="transition hover:text-gray-900">How it works</a>
          </div>
          <div className="flex items-center gap-2">
            {signedIn ? (
              <Link href={DASHBOARD_PATH} className={`${PRIMARY_BTN} px-4 py-2 text-sm`}>
                Dashboard →
              </Link>
            ) : (
              <>
                <Link href={DEMO_LESSON} className={`${SECONDARY_BTN} px-3 py-2 text-sm sm:px-4`}>
                  Try demo
                </Link>
                <Link href="/login" className={`hidden rounded-xl px-3 py-2 text-sm font-medium text-gray-700 transition hover:text-gray-900 sm:inline-flex ${FOCUS}`}>
                  Log in
                </Link>
                <Link href="/login?mode=signup" className={`${PRIMARY_BTN} px-4 py-2 text-sm`}>
                  Sign up
                </Link>
              </>
            )}
          </div>
        </nav>
      </header>

      <main>
        <section className="relative overflow-hidden px-4 pb-20 pt-16 text-center sm:px-6 sm:pt-24">
          <div
            aria-hidden="true"
            className="pointer-events-none absolute inset-0 -z-0 bg-[radial-gradient(ellipse_at_top,rgba(139,92,246,0.14),transparent_60%)]"
          />
          <div className="relative mx-auto max-w-3xl">
            <span className="inline-flex items-center gap-2 rounded-full border border-violet-200 bg-white px-3 py-1 text-xs font-medium text-violet-700 shadow-sm">
              <span className="h-1.5 w-1.5 rounded-full bg-violet-500" />
              Timestamp-aware AI learning assistant
            </span>
            <h1 className="mt-6 text-4xl font-bold tracking-tight text-gray-900 sm:text-6xl">
              The AI tutor that knows{" "}
              <span className="bg-gradient-to-r from-indigo-600 to-violet-600 bg-clip-text text-transparent">
                where you are
              </span>{" "}
              in your lesson.
            </h1>
            <p className="mx-auto mt-5 max-w-2xl text-base leading-relaxed text-gray-600 sm:text-lg">
              Malai Pioneer is a personal AI tutor for your courses. It follows the exact moment of the lecture
              and the page of the notes you are on, explains concepts in context, quizzes you, and cites every
              answer without jumping ahead of what you have learned.
            </p>
            <div className="mt-8 flex flex-col items-center justify-center gap-3 sm:flex-row">
              {signedIn ? (
                <Link href={DASHBOARD_PATH} className={`${PRIMARY_BTN} w-full px-6 py-3 text-sm sm:w-auto`}>
                  Go to your dashboard →
                </Link>
              ) : (
                <Link href="/login?mode=signup" className={`${PRIMARY_BTN} w-full px-6 py-3 text-sm sm:w-auto`}>
                  Get started free →
                </Link>
              )}
              <Link href={DEMO_LESSON} className={`${SECONDARY_BTN} w-full px-6 py-3 text-sm sm:w-auto`}>
                ▶ Try the demo lesson
              </Link>
            </div>
            <p className="mt-3 text-xs text-gray-400">The demo works without an account.</p>
          </div>
          <ProductPreview />
        </section>

        <TimestampSection />

        <section id="features" className="scroll-mt-20 border-t border-gray-200/70 px-4 py-20 sm:px-6">
          <div className="mx-auto max-w-6xl">
            <div className="mx-auto max-w-2xl text-center">
              <h2 className="text-3xl font-bold tracking-tight sm:text-4xl">Everything a tutor should do</h2>
              <p className="mt-3 text-gray-600">
                Explain, check, test and track, all grounded in your own course material.
              </p>
            </div>
            <div className="mt-12 grid gap-5 sm:grid-cols-2 lg:grid-cols-3">
              {FEATURES.map((f) => (
                <div
                  key={f.title}
                  className="rounded-2xl border border-gray-200 bg-gray-50/60 p-6 transition hover:border-violet-200 hover:bg-white hover:shadow-md hover:shadow-violet-500/5"
                >
                  <span className="flex h-10 w-10 items-center justify-center rounded-xl bg-gradient-to-br from-indigo-500 to-violet-500 text-white">
                    <Icon d={f.icon} />
                  </span>
                  <h3 className="mt-4 font-semibold text-gray-900">{f.title}</h3>
                  <p className="mt-1.5 text-sm leading-relaxed text-gray-600">{f.body}</p>
                </div>
              ))}
            </div>
          </div>
        </section>

        <section id="how" className="scroll-mt-20 border-t border-gray-200/70 bg-white px-4 py-20 sm:px-6">
          <div className="mx-auto max-w-5xl">
            <h2 className="text-center text-3xl font-bold tracking-tight sm:text-4xl">How it works</h2>
            <ol className="mt-12 grid gap-6 md:grid-cols-3">
              {STEPS.map((s, i) => (
                <li key={s.title} className="relative rounded-2xl border border-gray-200 bg-white p-6 shadow-sm">
                  <span className="font-mono text-sm font-semibold text-violet-600">0{i + 1}</span>
                  <h3 className="mt-2 font-semibold text-gray-900">{s.title}</h3>
                  <p className="mt-1.5 text-sm leading-relaxed text-gray-600">{s.body}</p>
                </li>
              ))}
            </ol>
          </div>
        </section>

        <section className="px-4 pb-20 sm:px-6">
          <div className="mx-auto max-w-5xl overflow-hidden rounded-3xl bg-gradient-to-br from-indigo-600 via-violet-600 to-fuchsia-600 px-6 py-14 text-center text-white shadow-xl shadow-violet-600/20 sm:px-12">
            <h2 className="text-3xl font-bold tracking-tight sm:text-4xl">Learn with a tutor that keeps up with you</h2>
            <p className="mx-auto mt-3 max-w-xl text-white/80">
              Try the demo lesson now, or create an account to keep your subjects, tutor chats and streak.
            </p>
            <div className="mt-8 flex flex-col items-center justify-center gap-3 sm:flex-row">
              <Link
                href={signedIn ? DASHBOARD_PATH : "/login?mode=signup"}
                className="inline-flex w-full items-center justify-center rounded-xl bg-white px-6 py-3 text-sm font-semibold text-violet-700 shadow-sm transition hover:bg-violet-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-white sm:w-auto"
              >
                {signedIn ? "Open dashboard →" : "Create free account →"}
              </Link>
              <Link
                href={DEMO_LESSON}
                className="inline-flex w-full items-center justify-center rounded-xl border border-white/30 px-6 py-3 text-sm font-semibold text-white transition hover:bg-white/10 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-white sm:w-auto"
              >
                Try the demo
              </Link>
            </div>
          </div>
        </section>
      </main>

      <footer className="border-t border-gray-200/70 px-4 py-8 sm:px-6">
        <div className="mx-auto flex max-w-6xl flex-col items-center justify-between gap-3 text-sm text-gray-500 sm:flex-row">
          <span>© {new Date().getFullYear()} Malai Pioneer · Timestamp-aware AI learning assistant</span>
          <div className="flex gap-5">
            <a href="#features" className="hover:text-gray-900">Features</a>
            <Link href={DEMO_LESSON} className="hover:text-gray-900">Demo</Link>
            <Link href={signedIn ? DASHBOARD_PATH : "/login"} className="hover:text-gray-900">
              {signedIn ? "Dashboard" : "Log in"}
            </Link>
          </div>
        </div>
      </footer>
    </div>
  );
}
