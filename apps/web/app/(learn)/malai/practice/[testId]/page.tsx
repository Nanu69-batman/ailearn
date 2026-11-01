import { Suspense } from "react";
import QuizView from "@/components/QuizView";

/** A saved practice test: its analytics, a re-attempt, and every past attempt. */
export default function MalaiPracticeTestPage() {
  return (
    <Suspense fallback={null}>
      <QuizView />
    </Suspense>
  );
}
