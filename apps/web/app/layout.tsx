import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "AI Learning Assistant",
  description: "Watch, pause, ask, understand — grounded video Q&A.",
};

interface RootLayoutProps {
  children: React.ReactNode;
}

export default function RootLayout({ children }: RootLayoutProps) {
  return (
    <html lang="en">
      <body className="min-h-screen bg-zinc-950 text-zinc-100 antialiased">
        {children}
      </body>
    </html>
  );
}
