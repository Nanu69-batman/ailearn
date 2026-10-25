import { Suspense } from "react";
import LessonView from "@/components/LessonView";

/** A resource from My subjects. The same page also lives inside Malai. */
export default function ContentPage() {
  return (
    <Suspense fallback={null}>
      <LessonView />
    </Suspense>
  );
}
