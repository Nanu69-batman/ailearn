import Link from "next/link";
import { DASHBOARD_PATH, contentPath } from "@/lib/learn";

/** The bundled demo lesson; public without signing in (middleware.ts). */
const DEMO_LESSON = contentPath("sub_06dea46b30bf4ee7", "res_2e629d682a3d");

export default function Home() {
  return (
    <main className="flex min-h-screen flex-col items-center justify-center bg-gray-50 p-8 text-gray-900">
      <div className="rounded-2xl border border-gray-200 bg-white p-10 text-center shadow-sm">
        <h1 className="text-3xl font-bold text-gray-900">Malai Pioneer</h1>
        <p className="mt-2 text-sm text-gray-500">AI Learning Assistant</p>
        <div className="mt-6 flex flex-wrap items-center justify-center gap-3">
          <Link
            href={DASHBOARD_PATH}
            className="inline-block rounded-xl bg-gradient-to-r from-indigo-500 to-violet-500 px-5 py-2.5 text-sm font-medium text-white shadow-sm transition hover:opacity-90 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-600"
          >
            Open dashboard →
          </Link>
          <Link
            href={DEMO_LESSON}
            className="inline-block rounded-xl border border-gray-200 bg-white px-5 py-2.5 text-sm font-medium text-gray-700 shadow-sm transition hover:bg-gray-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-600"
          >
            Try the demo lesson
          </Link>
        </div>
      </div>
    </main>
  );
}