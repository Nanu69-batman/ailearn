import { Suspense } from "react";

import LoginForm from "./LoginForm";

export const metadata = {
  title: "Sign in — Malai Pioneer",
  description: "Sign in to Malai Pioneer to keep your subjects, chats and progress.",
};

export default function LoginPage() {
  return (
    <main className="flex min-h-screen flex-col items-center justify-center bg-gray-50 p-6 text-gray-900">
      <Suspense fallback={null}>
        <LoginForm />
      </Suspense>
    </main>
  );
}
