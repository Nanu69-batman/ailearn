import LearnSessionProvider from "@/components/LearnSessionProvider";
import LearnShell from "@/components/LearnShell";

/**
 * Shared shell for every route in this group:
 *   /dashboard
 *   /ai-tutor/subjects
 *   /ai-tutor/subjects/[subjectId]
 *   /ai-tutor/subjects/[subjectId]/content/[resourceId]
 *
 * The dashboard deliberately sits in here rather than at the app root. Mounting
 * the session provider above all of them is what lets /dashboard <-> tutor
 * navigation reuse one session: the layout is not remounted between its child
 * routes, so the session object is never destroyed and never re-minted.
 */
export default function LearnLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <LearnShell>
      <LearnSessionProvider>{children}</LearnSessionProvider>
    </LearnShell>
  );
}