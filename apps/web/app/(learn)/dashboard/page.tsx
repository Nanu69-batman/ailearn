import Link from "next/link";
import SubjectList from "@/components/SubjectList";
import { SUBJECTS_PATH } from "@/lib/learn";

export const metadata = {
  title: "Dashboard — AI Learning Assistant",
  description: "Your subjects and their available content.",
};

/**
 * /dashboard — the subjects overview, and the landing page's single call to
 * action.
 *
 * It lives inside the `(learn)` route group on purpose: that is what keeps
 * LearnSessionProvider mounted, so moving between here and the tutor routes is
 * a client navigation that never re-mints a session.
 */
export default function DashboardPage() {
  return (
    <div className="flex flex-col gap-6">
      <header className="flex flex-col gap-1">
        <h1 className="text-2xl font-bold text-gray-900">Dashboard</h1>
        <p className="text-sm text-gray-500">
          Everything the AI Tutor has indexed for you. Open a subject below, or
          go straight to{" "}
          <Link
            className="font-medium text-violet-700 underline"
            href={SUBJECTS_PATH}
          >
            the subject list
          </Link>
          .
        </p>
      </header>
      <SubjectList description="Subjects come from the backend and are listed exactly as they exist there." />
    </div>
  );
}