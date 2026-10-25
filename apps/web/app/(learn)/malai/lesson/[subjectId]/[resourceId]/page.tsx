import { Suspense } from "react";
import LessonView from "@/components/LessonView";

/**
 * A resource opened from Malai (a subject's Resources). Same lesson page,
 * with Malai's sidebar beside it and links that stay inside Malai.
 */
export default function MalaiLessonPage() {
  return (
    <Suspense fallback={null}>
      <LessonView malai />
    </Suspense>
  );
}
