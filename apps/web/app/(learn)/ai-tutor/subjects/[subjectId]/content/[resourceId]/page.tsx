import { Suspense } from "react";
import LessonView from "@/components/LessonView";

/**
 * A resource from My subjects, on its own: the tutor chat lives in Malai, which
 * this page links to ("Open with Malai").
 */
export default function ContentPage() {
  return (
    <Suspense fallback={null}>
      <LessonView withChat={false} />
    </Suspense>
  );
}
