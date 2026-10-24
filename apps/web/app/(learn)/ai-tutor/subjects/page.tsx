import SubjectList from "@/components/SubjectList";

export const metadata = {
  title: "My subjects — Malai Pioneer",
  description: "All your subjects in one place.",
};

/**
 * /ai-tutor/subjects — the "My subjects" sidebar target.
 *
 * Session-independent by design: it only lists subjects, so it needs no session
 * and does not mint one. It is still inside the `(learn)` layout, so the
 * LearnSessionProvider above it is never remounted by arriving here from
 * /dashboard (or leaving for it).
 */
export default function SubjectIndexPage() {
  return (
    <div className="mx-auto flex w-full max-w-7xl flex-col gap-6">
      <SubjectList />
    </div>
  );
}
