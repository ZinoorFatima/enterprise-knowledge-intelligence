"use client";

import { useEffect, useRef, useState } from "react";

/**
 * Renders one page of a PDF and highlights the cited span.
 *
 * Highlighting is three-tier, and the tiers exist because of a specific risk:
 * on a product whose claim is provenance, a confidently misplaced highlight is
 * worse than no highlight at all.
 *
 *   1. Text-layer match  - locate the cited text in the page's own text layer
 *                          and union the matched rects. Exact on born-digital
 *                          PDFs and on OCR output with word boxes.
 *   2. Fuzzy match       - normalized whitespace, prefix match, for when the
 *                          cited span crosses layout boundaries.
 *   3. Page-level        - tint the page and SAY the span is approximate.
 *
 * It never silently falls through to "no highlight": the badge is the signal
 * that the system could not locate the span, which is itself information.
 */

type Tier = "exact" | "approximate" | "page-only" | "pending";

interface Props {
  documentId: string;
  page: number;
  citedText?: string;
  title?: string;
}

export function PdfViewer({ documentId, page, citedText, title }: Props) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const overlayRef = useRef<HTMLDivElement | null>(null);
  // Tracks the in-flight pdf.js render so a new pass can wait for the previous
  // one to unwind before touching the same canvas.
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const renderRef = useRef<any>(null);
  const [tier, setTier] = useState<Tier>("pending");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    let cleanup: (() => void) | undefined;

    (async () => {
      setLoading(true);
      setError(null);
      setTier("pending");
      try {
        // pdf.js touches `window` and needs its worker, so it can only load in
        // the browser - hence the dynamic import rather than a top-level one.
        const pdfjs = await import("pdfjs-dist");
        // Served from public/ as a plain static asset. `new URL(<bare
        // specifier>, import.meta.url)` does NOT resolve a package path -- it
        // produces a 404 and pdf.js then waits on a worker that will never
        // arrive, hanging with no error at all. Keep this a literal path, and
        // keep public/pdf.worker.min.mjs in sync with the installed version.
        pdfjs.GlobalWorkerOptions.workerSrc = "/pdf.worker.min.mjs";

        const task = pdfjs.getDocument({
          url: `/api/documents/${documentId}/file`,
          withCredentials: true,
        });
        // Deliberately NOT calling task.destroy() here.
        //
        // destroy() tears down the pdf.js worker backing the task. React runs
        // effects twice in development, so the discarded first pass would
        // destroy the worker out from under the surviving pass: the in-flight
        // render never settles and the sibling getDocument() hangs, with no
        // error raised anywhere. Cancelling the render is sufficient; the
        // document is garbage collected with the component.
        cleanup = undefined;
        // A missing or mismatched worker manifests as an unresolved promise
        // rather than a rejection, so bound it: an error state is recoverable,
        // an infinite spinner is not.
        const doc = await Promise.race([
          task.promise,
          new Promise<never>((_, reject) =>
            setTimeout(
              () => reject(new Error("Timed out loading the PDF renderer")),
              20_000,
            ),
          ),
        ]);
        if (cancelled) return;

        const target = Math.min(Math.max(page, 1), doc.numPages);
        const pdfPage = await doc.getPage(target);
        if (cancelled) return;

        const canvas = canvasRef.current;
        if (!canvas) return;
        const container = canvas.parentElement!;
        const base = pdfPage.getViewport({ scale: 1 });
        // Fit to container width; rotation is handled by the viewport rather
        // than by transforming coordinates by hand, which is where rotated
        // pages usually go wrong.
        // Guard the measurement: a container that is not laid out yet reports 0,
        // which would produce a zero or negative scale and a blank canvas.
        const available = container.clientWidth || container.parentElement?.clientWidth || 0;
        const scale = available > 20 ? Math.min((available - 2) / base.width, 2) : 1;
        const viewport = pdfPage.getViewport({ scale });

        canvas.width = Math.floor(viewport.width);
        canvas.height = Math.floor(viewport.height);
        canvas.style.width = `${Math.floor(viewport.width)}px`;
        canvas.style.height = `${Math.floor(viewport.height)}px`;

        const ctx = canvas.getContext("2d")!;

        // Only one render may be in flight on a given canvas. React runs
        // effects twice in development, so without this the second pass starts
        // a render on a canvas the first pass is still unwinding, and its
        // promise never settles -- a permanent spinner with no error raised.
        // Cancel the previous render and WAIT for it to finish unwinding first.
        const inFlight = renderRef.current;
        if (inFlight) {
          inFlight.cancel?.();
          await inFlight.promise.catch(() => {});
        }
        if (cancelled) return;

        const render = pdfPage.render({ canvasContext: ctx, viewport, canvas });
        renderRef.current = render;
        cleanup = () => {
          render.cancel?.();
        };
        try {
          await Promise.race([
            render.promise,
            new Promise<never>((_, reject) =>
              setTimeout(
                () => reject(new Error("The PDF renderer did not finish on this browser")),
                20_000,
              ),
            ),
          ]);
        } catch (e) {
          // A cancelled render is an expected outcome, not a failure.
          if ((e as { name?: string })?.name === "RenderingCancelledException") return;
          throw e;
        } finally {
          if (renderRef.current === render) renderRef.current = null;
        }
        if (cancelled) return;
        // Clear the spinner as soon as the page is on screen. Doing this only
        // after the highlight pass means a page that renders but whose span
        // cannot be located still reads as "loading" forever.
        setLoading(false);

        if (!citedText?.trim()) {
          setTier("page-only");
          return;
        }
        const rects = await locateSpan(pdfPage, viewport, citedText);
        if (cancelled) return;

        const overlay = overlayRef.current;
        if (overlay) {
          overlay.innerHTML = "";
          overlay.style.width = `${canvas.width}px`;
          overlay.style.height = `${canvas.height}px`;
          for (const r of rects.rects) {
            const el = document.createElement("div");
            el.style.cssText = `position:absolute;left:${r.x}px;top:${r.y}px;width:${r.w}px;height:${r.h}px;background:var(--highlight-citation);border-radius:2px;pointer-events:none;`;
            overlay.appendChild(el);
          }
          if (rects.rects.length) {
            overlay.firstElementChild?.scrollIntoView({ block: "center" });
          }
        }
        setTier(rects.tier);

      } catch (e) {
        if (!cancelled) {
          setError(e instanceof Error ? e.message : "Could not render this page");
          setLoading(false);
        }
      }
    })();

    return () => {
      cancelled = true;
      cleanup?.();
    };
  }, [documentId, page, citedText]);

  return (
    <div>
      <div className="flex items-baseline justify-between gap-3 px-1 pb-2">
        <p className="text-ui-sm text-foreground-strong">
          {title} <span className="tnum font-mono text-muted-foreground">p.{page}</span>
        </p>
        <TierBadge tier={tier} />
      </div>

      <div className="relative min-h-[18rem] overflow-auto rounded-md border border-border bg-surface-sunken p-px">
        {/*
          The canvas container must stay laid out even while loading: its
          clientWidth is what the render scale is computed from. Hiding it with
          display:none measures 0, the scale degenerates, and the page silently
          never draws. Opacity keeps it measurable while hiding a half-painted
          canvas.
        */}
        <div
          className="relative transition-opacity duration-150"
          style={{ opacity: loading || error ? 0 : 1 }}
          aria-hidden={loading || error !== null}
        >
          <canvas ref={canvasRef} className="block" />
          <div ref={overlayRef} className="pointer-events-none absolute left-0 top-0" />
        </div>

        {loading && (
          <div className="absolute inset-0 flex items-center justify-center">
            <p className="text-ui-sm text-muted-foreground">Rendering page&hellip;</p>
          </div>
        )}
        {error && (
          <div className="absolute inset-0 p-5">
            <p className="text-ui-sm text-danger">{error}</p>
            <a
              href={`/api/documents/${documentId}/file`}
              className="text-ui-sm mt-2 inline-block text-primary underline underline-offset-4"
            >
              Open the original PDF
            </a>
          </div>
        )}
      </div>
    </div>
  );
}

function TierBadge({ tier }: { tier: Tier }) {
  if (tier === "pending") return null;
  const map = {
    exact: ["var(--success)", "var(--success-subtle)", "Exact span"],
    approximate: ["var(--warning)", "var(--warning-subtle)", "Approximate span"],
    "page-only": [
      "var(--warning)",
      "var(--warning-subtle)",
      "Approximate — exact span unavailable",
    ],
  } as const;
  const [fg, bg, label] = map[tier];
  return (
    <span
      className="text-caption shrink-0 rounded-full border px-2 py-0.5 font-medium"
      style={{ color: fg, background: bg, borderColor: fg }}
      title="How precisely the cited text could be located on this page"
    >
      {label}
    </span>
  );
}

interface Located {
  tier: Tier;
  rects: { x: number; y: number; w: number; h: number }[];
}

/** Find the cited text in the page's text layer and map it to canvas rects. */
async function locateSpan(
  pdfPage: { getTextContent: () => Promise<{ items: unknown[] }> },
  viewport: { transform: number[]; height: number },
  citedText: string,
): Promise<Located> {
  const content = await pdfPage.getTextContent();
  type Item = { str: string; transform: number[]; width: number; height: number };
  const items = content.items.filter(
    (i): i is Item => typeof (i as Item).str === "string",
  );

  const norm = (s: string) => s.replace(/\s+/g, " ").trim().toLowerCase();
  const needle = norm(citedText);
  if (!needle) return { tier: "page-only", rects: [] };

  // Build a flat string with an index back to the item that produced each char.
  let flat = "";
  const owner: number[] = [];
  items.forEach((it, idx) => {
    const piece = norm(it.str) + " ";
    flat += piece;
    for (let i = 0; i < piece.length; i++) owner.push(idx);
  });

  let start = flat.indexOf(needle);
  let tier: Tier = "exact";
  if (start < 0) {
    // Fall back to a distinctive prefix: a cited span often crosses a layout
    // boundary that inserts whitespace the normalized form cannot recover.
    const prefix = needle.slice(0, Math.min(60, needle.length));
    start = flat.indexOf(prefix);
    tier = "approximate";
  }
  if (start < 0) return { tier: "page-only", rects: [] };

  const end = Math.min(start + needle.length, flat.length - 1);
  const touched = new Set(owner.slice(start, end + 1));

  const rects: Located["rects"] = [];
  for (const idx of touched) {
    const it = items[idx];
    if (!it) continue;
    // PDF text transforms are in PDF space with a bottom-left origin; the
    // viewport transform maps them into canvas space.
    const [a, , , d, e, f] = it.transform;
    const [va, , , vd, ve, vf] = viewport.transform;
    const x = a * 0 + e * va + ve;
    const yBottom = d * 0 + f * vd + vf;
    const h = Math.abs(it.height * vd) || Math.abs(d * vd) || 10;
    const w = Math.abs(it.width * va) || norm(it.str).length * 5;
    rects.push({ x, y: yBottom - h, w, h });
  }
  return rects.length ? { tier, rects } : { tier: "page-only", rects: [] };
}