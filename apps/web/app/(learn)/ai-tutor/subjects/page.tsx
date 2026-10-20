import SubjectList from "@/components/SubjectList";

export const metadata = {
  title: "AI Tutor — Subjects",
  description: "Choose a subject to study with the AI Tutor.",
};

/**
 * /ai-tutor/subjects — the "AI Tutor" sidebar target.
 *
 * Session-independent by design: it only lists subjects, so it needs no session
 * and does not mint one. It is still inside the `(learn)` layout, so the
 * LearnSessionProvider above it is never remounted by arriving here from
 * /dashboard (or leaving for it).
 */
export default function SubjectIndexPage() {
  return (
    <div className="mx-auto flex w-full max-w-6xl flex-col gap-6">
      <SubjectList
        title="AI Tutor"
        description="Pick a subject to watch its content and ask questions about it."
      />
    </div>
  );
}
