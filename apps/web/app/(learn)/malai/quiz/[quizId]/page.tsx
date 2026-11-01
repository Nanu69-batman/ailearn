import { redirect } from "next/navigation";

/** Old saved-quiz links: those quizzes are practice tests now. */
export default async function MalaiQuizPage({ params }: { params: Promise<{ quizId: string }> }) {
  const { quizId } = await params;
  redirect(`/malai/practice/${encodeURIComponent(decodeURIComponent(quizId))}`);
}
