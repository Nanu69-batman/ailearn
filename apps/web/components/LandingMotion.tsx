"use client";

import { useEffect, useRef, useState, type CSSProperties, type ReactNode } from "react";

/**
 * Landing-page motion. `Reveal` fades its child in each time it scrolls into
 * view; `SectionDots` is the side navigation for the full-screen sections.
 * The CSS lives in globals.css (.reveal) and respects prefers-reduced-motion.
 */
export function Reveal({
  children,
  delay = 0,
  className = "",
}: {
  children: ReactNode;
  delay?: number;
  className?: string;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const io = new IntersectionObserver(([entry]) => setVisible(entry.isIntersecting), {
      threshold: 0.15,
    });
    io.observe(el);
    return () => io.disconnect();
  }, []);

  return (
    <div
      ref={ref}
      className={`reveal ${visible ? "is-visible" : ""} ${className}`}
      style={{ transitionDelay: visible ? `${delay}ms` : "0ms" }}
    >
      {children}
    </div>
  );
}

/** Each blob drifts from its start toward `to` (in vw/vh) as the page scrolls. */
const BLOBS = [
  { className: "left-[-10vw] top-[-10vh] h-[55vh] w-[55vh] bg-indigo-300/30", to: [55, 70], turn: 120 },
  { className: "right-[-12vw] top-[20vh] h-[50vh] w-[50vh] bg-fuchsia-300/20", to: [-60, 50], turn: -90 },
  { className: "bottom-[-15vh] left-[30vw] h-[60vh] w-[60vh] bg-violet-300/30", to: [-30, -80], turn: 160 },
  { className: "left-[60vw] top-[60vh] h-[40vh] w-[40vh] bg-sky-200/30", to: [-50, -30], turn: -140 },
];

/**
 * Fixed gradient blobs behind the landing page that move with the scroll
 * position of the element `targetId` (the page scrolls inside that element,
 * not the window). Progress goes into the `--p` custom property (0 to 1).
 */
export function ScrollGradient({ targetId }: { targetId: string }) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const scroller = document.getElementById(targetId);
    const layer = ref.current;
    if (!scroller || !layer) return;
    let frame = 0;
    const update = () => {
      frame = 0;
      const max = scroller.scrollHeight - scroller.clientHeight;
      layer.style.setProperty("--p", String(max > 0 ? scroller.scrollTop / max : 0));
    };
    const onScroll = () => {
      if (!frame) frame = requestAnimationFrame(update);
    };
    update();
    scroller.addEventListener("scroll", onScroll, { passive: true });
    return () => {
      scroller.removeEventListener("scroll", onScroll);
      cancelAnimationFrame(frame);
    };
  }, [targetId]);

  return (
    <div
      ref={ref}
      aria-hidden="true"
      className="pointer-events-none fixed inset-0 z-0 overflow-hidden"
      style={{ "--p": 0 } as CSSProperties}
    >
      {BLOBS.map((b, i) => (
        <div
          key={i}
          className={`scroll-blob absolute rounded-full blur-3xl ${b.className}`}
          style={{
            transform: `translate3d(calc(var(--p) * ${b.to[0]}vw), calc(var(--p) * ${b.to[1]}vh), 0) rotate(calc(var(--p) * ${b.turn}deg)) scale(calc(1 + var(--p) * 0.3))`,
          }}
        />
      ))}
      <div className="absolute inset-0 bg-[radial-gradient(ellipse_at_center,transparent_40%,rgba(249,250,251,0.6))]" />
    </div>
  );
}

export function SectionDots({ sections }: { sections: Array<{ id: string; label: string }> }) {
  const [active, setActive] = useState(sections[0]?.id);

  useEffect(() => {
    const io = new IntersectionObserver(
      (entries) => {
        for (const e of entries) if (e.isIntersecting) setActive(e.target.id);
      },
      { threshold: 0.55 },
    );
    for (const s of sections) {
      const el = document.getElementById(s.id);
      if (el) io.observe(el);
    }
    return () => io.disconnect();
  }, [sections]);

  return (
    <nav
      aria-label="Sections"
      className="fixed right-5 top-1/2 z-40 hidden -translate-y-1/2 flex-col gap-3 md:flex"
    >
      {sections.map((s) => (
        <a
          key={s.id}
          href={`#${s.id}`}
          aria-label={s.label}
          aria-current={active === s.id ? "true" : undefined}
          className="group flex items-center justify-end gap-2"
        >
          <span className="text-xs font-medium text-gray-500 opacity-0 transition group-hover:opacity-100">
            {s.label}
          </span>
          <span
            className={`block rounded-full transition-all duration-300 ${
              active === s.id ? "h-6 w-2 bg-violet-500" : "h-2 w-2 bg-gray-300 group-hover:bg-gray-400"
            }`}
          />
        </a>
      ))}
    </nav>
  );
}
