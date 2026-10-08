import Link from "next/link";

export default function Home() {
  return (
    <main className="flex min-h-screen flex-col items-center justify-center bg-zinc-950 p-8 text-zinc-100">
      <div className="rounded-2xl border border-white/10 bg-white/5 p-10 text-center shadow-2xl backdrop-blur">
        <h1 className="bg-gradient-to-r from-indigo-400 to-violet-400 bg-clip-text text-3xl font-bold text-transparent">
          Malai Pioneer
        </h1>
        <p className="mt-2 text-sm text-zinc-400">AI Learning Assistant</p>
        <Link
          href="/demo-course"
          className="mt-6 inline-block rounded-xl bg-gradient-to-r from-indigo-500 to-violet-500 px-5 py-2.5 text-sm font-medium text-white transition hover:opacity-90"
        >
          Open the demo course →
        </Link>
      </div>
    </main>
  );
}
