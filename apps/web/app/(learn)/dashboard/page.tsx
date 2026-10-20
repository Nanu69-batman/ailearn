import SubjectList from "@/components/SubjectList";

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
    <div className="mx-auto flex w-full max-w-6xl flex-col gap-6">
      <SubjectList
        greeting
        title="Your subjects"
        description="Everything Malai has indexed for you, grouped by subject. Open one to watch, read and ask questions grounded in your own materials."
      />
    </div>
  );
}
