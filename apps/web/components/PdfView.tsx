"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Document, Page, pdfjs } from "react-pdf";
import "react-pdf/dist/esm/Page/TextLayer.css";
import { escapeHtml, highlightRanges, type ItemRange } from "@/lib/textMatch";

pdfjs.GlobalWorkerOptions.workerSrc = new URL(
  "pdfjs-dist/build/pdf.worker.min.mjs",
  import.meta.url,
).toString();

/**
 * pdf.js rendering of a PDF with a selectable text layer, so a cited passage
 * can be marked on the page itself. Loaded client-side only (pdf.js touches
 * browser globals at import), via `next/dynamic` in DocumentViewer.
 *
 * Pages render lazily as they near the viewport; the cited page always
 * renders. A jump scrolls to the first highlighted line, or to the page top
 * when the passage could not be located in the text layer.
 */

export interface PdfTarget {
  page: number;
  /** The cited passage; null for a plain page jump. */
  text: string | null;
  /** Changes on every jump, so the same target can be re-applied. */
  nonce: number;
}

interface PdfViewProps {
  src: string;
  target: PdfTarget | null;
  onPageCount?: (n: number) => void;
}

function LazyPage({
  pageNumber,
  width,
  forced,
  root,
  children,
}: {
  pageNumber: number;
  width: number;
  forced: boolean;
  root: HTMLElement | null;
  children: React.ReactNode;
}) {
  const ref = useRef<HTMLDivElement | null>(null);
  const [near, setNear] = useState(pageNumber <= 2);

  useEffect(() => {
    const el = ref.current;
    if (!el || near) return;
    const io = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting)) {
          setNear(true);
          io.disconnect();
        }
      },
      { root, rootMargin: "800px 0px" },
    );
    io.observe(el);
    return () => io.disconnect();
  }, [near, root]);

  return (
    <div
      ref={ref}
      data-page={pageNumber}
      className="mx-auto mb-3 bg-white shadow-sm"
      style={{ width, minHeight: near || forced ? undefined : Math.round(width * 1.414) }}
    >
      {near || forced ? children : null}
    </div>
  );
}

export default function PdfView({ src, target, onPageCount }: PdfViewProps) {
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const [width, setWidth] = useState(0);
  const [numPages, setNumPages] = useState(0);
  const [error, setError] = useState<string | null>(null);
  /** Item strings of the cited page, from its text content. */
  const [items, setItems] = useState<{ page: number; strs: string[] } | null>(null);
  const scrolledFor = useRef<number | null>(null);

  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setWidth(Math.max(240, el.clientWidth - 24)));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const ranges = useMemo<Map<number, ItemRange>>(() => {
    if (!target?.text || !items || items.page !== target.page) return new Map();
    return highlightRanges(items.strs, target.text);
  }, [target, items]);

  const renderer = useCallback(
    ({ str, itemIndex }: { str: string; itemIndex: number }): string => {
      const r = ranges.get(itemIndex);
      if (!r) return escapeHtml(str);
      return (
        escapeHtml(str.slice(0, r.from)) +
        `<mark class="cite-hl">${escapeHtml(str.slice(r.from, r.to))}</mark>` +
        escapeHtml(str.slice(r.to))
      );
    },
    [ranges],
  );

  // A new jump: scroll to the page top now; the text layer's render then
  // refines it to the highlighted line.
  useEffect(() => {
    if (!target || !numPages) return;
    const pageEl = scrollRef.current?.querySelector<HTMLElement>(`[data-page="${target.page}"]`);
    pageEl?.scrollIntoView({ block: "start", behavior: "smooth" });
    scrolledFor.current = null;
  }, [target, numPages]);

  const onTextLayer = useCallback(
    (pageNumber: number): void => {
      if (!target || pageNumber !== target.page || scrolledFor.current === target.nonce) return;
      if (ranges.size === 0 && target.text) return; // wait for the highlighted pass
      const mark = scrollRef.current?.querySelector<HTMLElement>(
        `[data-page="${pageNumber}"] mark.cite-hl`,
      );
      if (mark) {
        mark.scrollIntoView({ block: "center", behavior: "smooth" });
        scrolledFor.current = target.nonce;
      }
    },
    [target, ranges],
  );

  return (
    <div ref={scrollRef} className="h-full overflow-y-auto bg-gray-100 px-3 py-3">
      {error ? (
        <p className="py-16 text-center text-sm text-red-600" role="alert">
          Could not load the PDF: {error}
        </p>
      ) : (
        <Document
          file={src}
          onLoadSuccess={({ numPages: n }) => {
            setNumPages(n);
            onPageCount?.(n);
          }}
          onLoadError={(e) => setError(e.message)}
          loading={<p className="py-16 text-center text-sm text-gray-500">Loading PDF…</p>}
        >
          {width > 0 &&
            Array.from({ length: numPages }, (_, i) => i + 1).map((n) => {
              const cited = target?.page === n;
              return (
                <LazyPage key={n} pageNumber={n} width={width} forced={cited} root={scrollRef.current}>
                  <Page
                    pageNumber={n}
                    width={width}
                    renderAnnotationLayer={false}
                    customTextRenderer={cited ? renderer : undefined}
                    onGetTextSuccess={
                      cited
                        ? (tc) =>
                            setItems({
                              page: n,
                              strs: tc.items.map((it) => ("str" in it ? it.str : "")),
                            })
                        : undefined
                    }
                    onRenderTextLayerSuccess={cited ? () => onTextLayer(n) : undefined}
                  />
                </LazyPage>
              );
            })}
        </Document>
      )}
    </div>
  );
}
