"use client";

import { useLearnerName } from "@/lib/profile";

/** The signed-in learner's first name (from their own profile, never a constant). */
export function LearnerName() {
  return <>{useLearnerName()}</>;
}

/** First letter of that name, for the avatar bubble. */
export function LearnerInitial() {
  return <>{useLearnerName()[0]?.toUpperCase() ?? "?"}</>;
}
