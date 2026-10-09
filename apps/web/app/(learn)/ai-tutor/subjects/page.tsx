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
    <div className="flex flex-col gap-6">
      <header className="flex flex-col gap-1">
        <h1 className="text-2xl font-bold text-gray-900">AI Tutor</h1>
        <p className="text-sm text-gray-500">
          Pick a subject to watch its content and ask questions about it.
        </p>
      </header>
      <SubjectList description="Only subjects the backend knows about are shown." />
    </div>
  );
}