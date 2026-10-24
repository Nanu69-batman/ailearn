import Dashboard from "@/components/Dashboard";

export const metadata = {
  title: "Dashboard — Malai Pioneer",
  description: "Your learning activity, what to pick up next, and your subjects.",
};

/**
 * /dashboard — the landing page's single call to action.
 *
 * It lives inside the `(learn)` route group on purpose: that is what keeps
 * LearnSessionProvider mounted, so moving between here and the tutor routes is
 * a client navigation that never re-mints a session.
 */
export default function DashboardPage() {
  return (
    <div className="mx-auto flex w-full max-w-7xl flex-col gap-6">
      <Dashboard />
    </div>
  );
}
