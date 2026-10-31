import { Suspense } from "react";
import QuizView from "@/components/QuizView";

/** A saved quiz: its analytics, a re-attempt, and every past attempt. */
export default function MalaiQuizPage() {
  return (
    <Suspense fallback={null}>
      <QuizView />
    </Suspense>
  );
}
