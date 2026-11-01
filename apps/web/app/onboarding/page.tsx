import { Suspense } from "react";

import OnboardingForm from "./OnboardingForm";

export const metadata = {
  title: "Set up your account — Biryani Builders",
};

export default function OnboardingPage() {
  return (
    <main className="flex min-h-screen flex-col items-center justify-center bg-gray-50 p-6 text-gray-900">
      <Suspense fallback={null}>
        <OnboardingForm />
      </Suspense>
    </main>
  );
}
