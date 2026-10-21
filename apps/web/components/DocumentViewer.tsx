"use client";

import { useEffect, useState } from "react";

/**
 * In-page viewers for the non-timed materials: PDF, slides and images.
 *
 * PDFs use the browser's own viewer in an iframe; `#page=N` is the standard
 * open-parameter for jumping, and the iframe is re-keyed on every jump so a
 * second click on the same page still scrolls back to it. Slides (.pptx) have
 * no browser renderer, so they go through Office Online's embed viewer, which
 * fetches the file itself and therefore only works when the API is reachable
 * from the internet. On localhost the viewer says so and offers the download.
 */

export type DocumentKind = "pdf" | "pptx" | "image";

interface DocumentViewerProps {
  kind: DocumentKind;
  /** Null when the original file is no longer stored. */
  src: string | null;
  title: string;
  /** PDF only: a page to jump to, re-applied whenever `jump.nonce` changes. */
  jump?: { page: number; nonce: number } | null;
}

const LABEL: Record<DocumentKind, string> = {
  pdf: "PDF",
  pptx: "Slides",
  image: "Image",
};

const ICON: Record<DocumentKind, string> = {
  pdf: "📄",
  pptx: "📊",
  image: "🖼️",
};

/** Office Online can only read a file it can download over public https. */
function isPublicUrl(src: string): boolean {
  try {
    const url = new URL(src);
    return (
      url.protocol === "https:" &&
      !/^(localhost|127\.|10\.|192\.168\.|\[::1\])/.test(url.hostname)
    );
  } catch {
    return false;
  }
}

function officeEmbedUrl(src: string): string {
  return `https://view.officeapps.live.com/op/embed.aspx?src=${encodeURIComponent(src)}`;
}

function Toolbar({
  kind,
  title,
  src,
  page,
  onPage,
}: {
  kind: DocumentKind;
  title: string;
  src: string | null;
  page: number;
  onPage: (page: number) => void;
}) {
  const [draft, setDraft] = useState(String(page));
  useEffect(() => setDraft(String(page)), [page]);

  return (
    <div className="flex flex-wrap items-center gap-3 border-b border-gray-200 bg-gray-50/80 px-4 py-2.5">
      <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-white text-base shadow-sm" aria-hidden="true">
        {ICON[kind]}
      </span>
      <div className="min-w-0 flex-1">
        <p className="truncate text-sm font-medium text-gray-900">{title}</p>
        <p className="text-[11px] text-gray-500">{LABEL[kind]}</p>
      </div>
      {kind === "pdf" && src && (
        <form
          className="flex items-center gap-1.5 text-xs text-gray-600"
          onSubmit={(e) => {
            e.preventDefault();
            const n = Number.parseInt(draft, 10);
            if (Number.isFinite(n) && n >= 1) onPage(n);
          }}
        >
          <label htmlFor="doc-page">Page</label>
          <input
            id="doc-page"
            type="number"
            min={1}
            inputMode="numeric"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            className="w-14 rounded-lg border border-gray-200 bg-white px-2 py-1 text-xs tabular-nums focus:border-violet-400 focus:outline-none focus:ring-2 focus:ring-violet-200"
          />
          <button
            type="submit"
            className="rounded-lg border border-gray-200 bg-white px-2 py-1 font-medium text-gray-700 transition hover:border-violet-300 hover:text-violet-700"
          >
            Go
          </button>
        </form>
      )}
      {src && (
        <div className="flex items-center gap-1.5">
          <a
            href={src}
            target="_blank"
            rel="noopener noreferrer"
            className="rounded-lg border border-gray-200 bg-white px-2.5 py-1 text-xs font-medium text-gray-700 transition hover:border-violet-300 hover:text-violet-700"
          >
            Open ↗
          </a>
          <a
            href={src}
            download
            className="rounded-lg border border-gray-200 bg-white px-2.5 py-1 text-xs font-medium text-gray-700 transition hover:border-violet-300 hover:text-violet-700"
          >
            Download
          </a>
        </div>
      )}
    </div>
  );
}

function Notice({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex min-h-[320px] flex-col items-center justify-center gap-2 px-6 py-10 text-center" role="note">
      {children}
    </div>
  );
}

export default function DocumentViewer({ kind, src, title, jump }: DocumentViewerProps) {
  const [page, setPage] = useState(jump?.page ?? 1);
  const [nonce, setNonce] = useState(0);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    if (!jump) return;
    setPage(jump.page);
    setNonce((n) => n + 1);
  }, [jump]);

  const goTo = (n: number): void => {
    setPage(n);
    setNonce((v) => v + 1);
  };

  let body: React.ReactNode;
  if (!src) {
    body = (
      <Notice>
        <p className="text-sm font-medium text-gray-800">The original file is no longer stored.</p>
        <p className="max-w-sm text-xs text-gray-500">
          Malai can still answer from its indexed text — ask in the chat.
        </p>
      </Notice>
    );
  } else if (kind === "image") {
    body = (
      <div className="flex items-center justify-center bg-gray-100 p-3">
        <img src={src} alt={title} className="max-h-[75vh] w-auto max-w-full rounded-lg object-contain shadow-sm" />
      </div>
    );
  } else if (kind === "pptx" && !isPublicUrl(src)) {
    body = (
      <Notice>
        <p className="text-sm font-medium text-gray-800">Slide preview needs a public server.</p>
        <p className="max-w-sm text-xs text-gray-500">
          The slide viewer loads the file from the internet, so it can&apos;t reach
          this local server. Download the deck to view it — Malai can still answer
          about it with slide citations.
        </p>
        <a
          href={src}
          download
          className="mt-2 rounded-xl bg-violet-600 px-4 py-2 text-sm font-semibold text-white shadow-sm transition hover:bg-violet-700"
        >
          Download slides
        </a>
      </Notice>
    );
  } else {
    const frameSrc =
      kind === "pdf" ? `${src}#page=${page}&view=FitH` : officeEmbedUrl(src);
    body = (
      <div className="relative h-[72vh] min-h-[420px] bg-gray-100">
        {!loaded && (
          <div className="absolute inset-0 flex items-center justify-center text-sm text-gray-500" aria-hidden="true">
            Loading {LABEL[kind].toLowerCase()}…
          </div>
        )}
        <iframe
          key={kind === "pdf" ? `${page}-${nonce}` : src}
          src={frameSrc}
          title={title}
          onLoad={() => setLoaded(true)}
          className="relative h-full w-full border-0"
          allowFullScreen
        />
      </div>
    );
  }

  return (
    <div className="overflow-hidden rounded-2xl border border-gray-200 bg-white shadow-sm">
      <Toolbar kind={kind} title={title} src={src} page={page} onPage={goTo} />
      {body}
    </div>
  );
}
