import Link from "next/link";

import { Reveal, ScrollGradient, SectionDots } from "@/components/LandingMotion";
import { DASHBOARD_PATH, contentPath } from "@/lib/learn";
import { getSupabaseServerClient } from "@/lib/supabase/server";

export const metadata = {
  title: "Malai Pioneer — Timestamp-aware AI learning assistant",
  description:
    "An AI tutor that follows where you are in a lecture or document, explains in context, quizzes you and cites every answer.",
};

/** The bundled demo lesson; public without signing in (middleware.ts). */
const DEMO_LESSON = contentPath("sub_06dea46b30bf4ee7", "res_2e629d682a3d");

const FOCUS =
  "focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-600";
const PRIMARY_BTN = `inline-flex items-center justify-center gap-1.5 rounded-xl bg-gradient-to-r from-indigo-500 to-violet-500 font-medium text-white shadow-sm shadow-violet-500/20 transition hover:-translate-y-0.5 hover:shadow-lg hover:shadow-violet-500/30 ${FOCUS}`;
const SECONDARY_BTN = `inline-flex items-center justify-center gap-1.5 rounded-xl border border-white/70 bg-white/50 font-medium text-gray-700 shadow-sm backdrop-blur-md transition hover:-translate-y-0.5 hover:bg-white/80 ${FOCUS}`;

const SECTIONS = [
  { id: "top", label: "Home" },
  { id: "timestamp", label: "Timestamp-aware" },
  { id: "features", label: "Features" },
  { id: "how", label: "How it works" },
  { id: "start", label: "Get started" },
];

const FEATURES = [
  { title: "Explains like a tutor", body: "Step by step, with follow-ups.", icon: "M7 8h10M7 12h6M4 4h16v12H8l-4 4z" },
  { title: "Knows your moment", body: "Your timestamp and page, always.", icon: "M12 8v4l3 2M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18Z" },
  { title: "Cites every answer", body: "Grounded in your material.", icon: "M9 12l2 2 4-4M12 3l7 3v6c0 4.5-3 7.5-7 9-4-1.5-7-4.5-7-9V6z" },
  { title: "Quizzes you", body: "On what you just covered.", icon: "M9 12l2 2 4-4M5 4h14v16H5z" },
  { title: "Any material", body: "Videos, YouTube, PDFs, slides.", icon: "M4 6h16M4 12h16M4 18h10" },
  { title: "Tracks progress", body: "Streaks, heatmap, completion.", icon: "M4 20V10M10 20V4M16 20v-7M22 20H2" },
];

const STEPS = [
  { title: "Add your course", body: "Lectures, notes, slides." },
  { title: "Learn and ask", body: "Pause anywhere. Ask anything." },
  { title: "Quiz and repeat", body: "Keep the streak alive." },
];

const CHAPTERS = [
  { label: "Intro", width: 18 },
  { label: "Weights", width: 24 },
  { label: "Activation", width: 22 },
  { label: "Training", width: 36 },
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

/** A full-screen, snap-aligned section. */
function Screen({
  id,
  className = "",
  children,
}: {
  id: string;
  className?: string;
  children: React.ReactNode;
}) {
  return (
    <section
      id={id}
      className={`relative flex min-h-screen snap-start items-center overflow-hidden px-4 pb-12 pt-24 sm:px-6 ${className}`}
    >
      {children}
    </section>
  );
}

/** A static picture of the lesson page: the lecture with the tutor beside it. */
function ProductPreview() {
  return (
    <div className="anim-float overflow-hidden rounded-2xl border border-white/70 bg-white/50 backdrop-blur-xl shadow-2xl shadow-indigo-900/10">
      <div className="flex items-center gap-1.5 border-b border-white/60 bg-white/40 px-4 py-2.5">
        <span className="h-2.5 w-2.5 rounded-full bg-gray-300" />
        <span className="h-2.5 w-2.5 rounded-full bg-gray-300" />
        <span className="h-2.5 w-2.5 rounded-full bg-gray-300" />
        <span className="ml-3 truncate text-xs text-gray-400">AI tutor · Neural networks</span>
      </div>
      <div className="p-4">
        <div className="relative flex aspect-video items-center justify-center overflow-hidden rounded-xl bg-gradient-to-br from-slate-800 to-indigo-950">
          <svg viewBox="0 0 200 100" className="h-3/5 w-3/5 text-indigo-300/70" aria-hidden="true">
            {[20, 50, 80].flatMap((y1) =>
              [35, 65].map((y2) => (
                <line key={`${y1}-${y2}`} x1="46" y1={y1} x2="94" y2={y2} stroke="currentColor" strokeWidth="1" opacity="0.5" />
              )),
            )}
            {[35, 65].map((y) => (
              <line key={`o${y}`} x1="106" y1={y} x2="154" y2="50" stroke="currentColor" strokeWidth="1" opacity="0.5" />
            ))}
            {[20, 50, 80].map((y) => (
              <circle key={`a${y}`} cx="40" cy={y} r="6" fill="currentColor" />
            ))}
            {[35, 65].map((y) => (
              <circle key={`b${y}`} cx="100" cy={y} r="6" fill="currentColor" />
            ))}
            <circle cx="160" cy="50" r="6" fill="currentColor" />
          </svg>
          <div className="absolute inset-x-3 bottom-3 flex items-center gap-2">
            <div className="h-1 flex-1 rounded-full bg-white/20">
              <div className="h-1 w-[42%] rounded-full bg-violet-400" />
            </div>
            <span className="font-mono text-[10px] text-white/70">04:12</span>
          </div>
        </div>
        <div className="mt-3 flex flex-col gap-2 text-[13px] leading-relaxed">
          <div className="ml-auto max-w-[85%] rounded-2xl rounded-br-md bg-gradient-to-r from-indigo-500 to-violet-500 px-3.5 py-2 text-white">
            Why add a bias here?
          </div>
          <div className="max-w-[92%] rounded-2xl rounded-bl-md border border-white/80 bg-white/70 px-3.5 py-2 text-gray-700">
            It shifts the activation, so a neuron can fire with zero input
            <span className="ml-1 inline-flex rounded-md bg-violet-100 px-1.5 font-mono text-[10px] font-semibold text-violet-700">
              03:48
            </span>
          </div>
        </div>
      </div>
    </div>
  );
}

export default async function Home() {
  const signedIn = await isSignedIn();
  const startHref = signedIn ? DASHBOARD_PATH : "/login?mode=signup";

  return (
    <div id="landing-scroll" className="landing-scroll h-screen snap-y snap-proximity overflow-y-auto scroll-smooth bg-gray-50 text-gray-900 md:snap-mandatory">
      <header className="fixed inset-x-0 top-0 z-30 border-b border-white/50 bg-white/30 backdrop-blur-xl">
        <nav className="mx-auto flex h-16 max-w-6xl items-center justify-between gap-4 px-4 sm:px-6">
          <Link href="#top" className={`flex items-center gap-2.5 rounded-lg ${FOCUS}`}>
            <span className="flex h-8 w-8 items-center justify-center rounded-xl bg-gradient-to-br from-indigo-500 to-violet-500 text-sm font-bold text-white">
              M
            </span>
            <span className="font-semibold tracking-tight">Malai Pioneer</span>
          </Link>
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
                <Link
                  href="/login"
                  className={`hidden rounded-xl px-3 py-2 text-sm font-medium text-gray-700 transition hover:text-gray-900 sm:inline-flex ${FOCUS}`}
                >
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

      <ScrollGradient targetId="landing-scroll" />
      <SectionDots sections={SECTIONS} />

      <main className="relative z-10">
        <Screen id="top">
          <div className="relative mx-auto grid w-full max-w-6xl items-center gap-12 lg:grid-cols-[1.1fr_1fr]">
            <div className="text-center lg:text-left">
              <Reveal>
                <span className="inline-flex items-center gap-2 rounded-full border border-white/70 bg-white/50 backdrop-blur-md px-3 py-1 text-xs font-medium text-violet-700 shadow-sm">
                  <span className="relative flex h-1.5 w-1.5">
                    <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-violet-400 opacity-75" />
                    <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-violet-500" />
                  </span>
                  Timestamp-aware AI learning assistant
                </span>
              </Reveal>
              <Reveal delay={100}>
                <h1 className="mt-6 text-4xl font-bold tracking-tight sm:text-6xl">
                  Your AI tutor,{" "}
                  <span className="bg-gradient-to-r from-indigo-600 to-violet-600 bg-clip-text text-transparent">
                    right where you are.
                  </span>
                </h1>
              </Reveal>
              <Reveal delay={200}>
                <p className="mt-5 text-lg text-gray-600">Pause any lesson. Ask. Get cited answers.</p>
              </Reveal>
              <Reveal delay={300}>
                <div className="mt-8 flex flex-col justify-center gap-3 sm:flex-row lg:justify-start">
                  <Link href={startHref} className={`${PRIMARY_BTN} px-6 py-3 text-sm`}>
                    {signedIn ? "Go to dashboard →" : "Get started free →"}
                  </Link>
                  <Link href={DEMO_LESSON} className={`${SECONDARY_BTN} px-6 py-3 text-sm`}>
                    ▶ Try the demo
                  </Link>
                </div>
              </Reveal>
            </div>
            <Reveal delay={250} className="mx-auto w-full max-w-md lg:max-w-none">
              <ProductPreview />
            </Reveal>
          </div>
          <a
            href="#timestamp"
            aria-label="Scroll down"
            className="absolute bottom-6 left-1/2 hidden -translate-x-1/2 animate-bounce text-gray-400 hover:text-gray-600 md:block"
          >
            <Icon d="M6 9l6 6 6-6" className="h-6 w-6" />
          </a>
        </Screen>

        <Screen id="timestamp">
          <div className="mx-auto w-full max-w-4xl text-center">
            <Reveal>
              <span className="text-sm font-semibold uppercase tracking-wider text-violet-600">Timestamp-aware</span>
              <h2 className="mt-3 text-3xl font-bold tracking-tight sm:text-5xl">Answers for this moment.</h2>
              <p className="mt-4 text-lg text-gray-600">Only what you&apos;ve covered. No spoilers.</p>
            </Reveal>
            <Reveal delay={150}>
              <div className="mt-14 text-left">
                <div className="relative">
                  <div className="h-3 rounded-full bg-white/60 ring-1 ring-white/70 backdrop-blur" />
                  <div className="anim-fill absolute inset-y-0 left-0 rounded-full bg-gradient-to-r from-indigo-500 to-violet-500" />
                  <div className="anim-playhead absolute -top-3 h-9 w-0.5 -translate-x-1/2 bg-gray-900">
                    <span className="absolute -top-7 left-1/2 -translate-x-1/2 whitespace-nowrap rounded-md bg-gray-900 px-2 py-0.5 font-mono text-[11px] text-white">
                      you
                    </span>
                  </div>
                </div>
                <div className="mt-3 flex text-xs font-medium text-gray-500">
                  {CHAPTERS.map((c) => (
                    <span key={c.label} style={{ width: `${c.width}%` }} className="truncate">
                      {c.label}
                    </span>
                  ))}
                </div>
              </div>
            </Reveal>
            <div className="mt-12 grid gap-4 sm:grid-cols-2">
              <Reveal delay={250}>
                <div className="rounded-2xl border border-violet-200/70 bg-violet-100/40 p-6 backdrop-blur-xl">
                  <div className="font-semibold text-violet-700">✓ Tutor uses</div>
                  <p className="mt-1 text-sm text-gray-600">What you&apos;ve watched and read</p>
                </div>
              </Reveal>
              <Reveal delay={350}>
                <div className="rounded-2xl border border-dashed border-gray-400/50 bg-white/30 p-6 backdrop-blur-xl">
                  <div className="font-semibold text-gray-500">⏸ Held back</div>
                  <p className="mt-1 text-sm text-gray-500">Everything after your position</p>
                </div>
              </Reveal>
            </div>
          </div>
        </Screen>

        <Screen id="features">
          <div className="mx-auto w-full max-w-6xl">
            <Reveal className="text-center">
              <h2 className="text-3xl font-bold tracking-tight sm:text-5xl">Everything a tutor does.</h2>
            </Reveal>
            <div className="mt-12 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
              {FEATURES.map((f, i) => (
                <Reveal key={f.title} delay={i * 80}>
                  <div className="group h-full rounded-2xl border border-white/70 bg-white/50 backdrop-blur-xl p-6 transition duration-300 hover:-translate-y-1 hover:border-white hover:bg-white/70 hover:shadow-xl hover:shadow-violet-500/10">
                    <span className="flex h-11 w-11 items-center justify-center rounded-xl bg-gradient-to-br from-indigo-500 to-violet-500 text-white transition duration-300 group-hover:rotate-6 group-hover:scale-110">
                      <Icon d={f.icon} />
                    </span>
                    <h3 className="mt-4 font-semibold">{f.title}</h3>
                    <p className="mt-1 text-sm text-gray-600">{f.body}</p>
                  </div>
                </Reveal>
              ))}
            </div>
          </div>
        </Screen>

        <Screen id="how">
          <div className="mx-auto w-full max-w-5xl">
            <Reveal className="text-center">
              <h2 className="text-3xl font-bold tracking-tight sm:text-5xl">Three steps.</h2>
            </Reveal>
            <div className="relative mt-14 grid gap-8 md:grid-cols-3">
              <div aria-hidden="true" className="absolute left-[16%] right-[16%] top-7 hidden h-0.5 bg-gradient-to-r from-indigo-200 via-violet-300 to-fuchsia-200 md:block" />
              {STEPS.map((s, i) => (
                <Reveal key={s.title} delay={i * 150}>
                  <div className="relative text-center">
                    <span className="mx-auto flex h-14 w-14 items-center justify-center rounded-2xl bg-gradient-to-br from-indigo-500 to-violet-500 font-mono text-lg font-bold text-white shadow-lg shadow-violet-500/25">
                      {i + 1}
                    </span>
                    <h3 className="mt-5 text-lg font-semibold">{s.title}</h3>
                    <p className="mt-1 text-sm text-gray-600">{s.body}</p>
                  </div>
                </Reveal>
              ))}
            </div>
          </div>
        </Screen>

        <Screen id="start" className="flex-col justify-center">
          <Reveal className="mx-auto w-full max-w-4xl">
            <div className="relative overflow-hidden rounded-3xl border border-white/20 bg-gradient-to-br from-indigo-600/85 via-violet-600/80 to-fuchsia-600/75 px-6 backdrop-blur-xl py-16 text-center text-white shadow-2xl shadow-violet-600/30 sm:px-12">
              <div aria-hidden="true" className="anim-blob absolute -right-16 -top-16 h-64 w-64 rounded-full bg-white/10 blur-2xl" />
              <h2 className="relative text-3xl font-bold tracking-tight sm:text-5xl">Start learning smarter.</h2>
              <div className="relative mt-8 flex flex-col items-center justify-center gap-3 sm:flex-row">
                <Link
                  href={startHref}
                  className="inline-flex w-full items-center justify-center rounded-xl bg-white px-6 py-3 text-sm font-semibold text-violet-700 shadow-sm transition hover:-translate-y-0.5 hover:bg-violet-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-white sm:w-auto"
                >
                  {signedIn ? "Open dashboard →" : "Create free account →"}
                </Link>
                <Link
                  href={DEMO_LESSON}
                  className="inline-flex w-full items-center justify-center rounded-xl border border-white/30 px-6 py-3 text-sm font-semibold text-white transition hover:-translate-y-0.5 hover:bg-white/10 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-white sm:w-auto"
                >
                  Try the demo
                </Link>
              </div>
            </div>
          </Reveal>
          <footer className="absolute inset-x-0 bottom-6 text-center text-sm text-gray-400">
            © {new Date().getFullYear()} Malai Pioneer
          </footer>
        </Screen>
      </main>
    </div>
  );
}
