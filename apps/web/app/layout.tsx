import type { Metadata } from "next";
import { Inter, JetBrains_Mono } from "next/font/google";
import "./globals.css";
// KaTeX stylesheet. Imported here (not linked by hand) so `next dev` and
// `next build` both bundle it and Next fingerprints the font assets.
import "katex/dist/katex.min.css";

// Self-hosted by next/font at build time (no request to Google at runtime).
// Inter is the closest open face to the neo-grotesque ChatGPT reads in.
const sans = Inter({ subsets: ["latin"], display: "swap", variable: "--font-sans" });
const mono = JetBrains_Mono({ subsets: ["latin"], display: "swap", variable: "--font-mono" });

export const metadata: Metadata = {
  title: "AI Learning Assistant",
  description: "Watch, pause, ask, understand — grounded video Q&A.",
};

interface RootLayoutProps {
  children: React.ReactNode;
}

export default function RootLayout({ children }: RootLayoutProps) {
  return (
    <html lang="en" className={`${sans.variable} ${mono.variable}`}>
      <body className="min-h-screen bg-zinc-950 text-zinc-100 antialiased">
        {children}
      </body>
    </html>
  );
}
