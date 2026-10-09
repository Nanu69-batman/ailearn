import Link from "next/link";
import { DASHBOARD_PATH } from "@/lib/learn";

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
        </div>
      </div>
    </main>
  );
}