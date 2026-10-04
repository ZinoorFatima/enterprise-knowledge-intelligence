"use client";

import { useEffect, useRef, useState } from "react";

/**
 * The pipeline, as inline SVG.
 *
 * Not an image, not Lottie, not a video. It has to be legible at 375px and at
 * 1440px, its stage labels have to be real text for search and screen readers,
 * it has to follow the theme, and hovering a stage should explain what happens
 * there. That set of requirements rules out every raster option, and it costs
 * about 6KB.
 *
 * The server renders the fully-drawn state, so a JS failure still leaves a
 * correct diagram rather than a blank box.
 */

export interface Stage {
  id: string;
  label: string;
  sub: string;
  detail: string;
}

export const PIPELINE_STAGES: Stage[] = [
  {
    id: "documents",
    label: "Documents",
    sub: "PDF · scans",
    detail:
      "Upload contracts, filings and scanned PDFs. Files are hashed on the way in, so re-uploading the same document returns the existing one instead of paying for OCR twice.",
  },
  {
    id: "parse",
    label: "Parser / OCR",
    sub: "layout + OCR",
    detail:
      "PyMuPDF extracts the text layer. Pages with no text, image-dominant pages, and pages whose text layer is broken-CID garbage are routed to OCR, which returns per-line boxes and a confidence score.",
  },
  {
    id: "chunk",
    label: "Chunking",
    sub: "semantic",
    detail:
      "Sentences are embedded with their neighbours and split where meaning turns, at the 95th percentile of adjacent distance. Every chunk keeps exact character offsets, which is what makes page-accurate citations possible.",
  },
  {
    id: "embed",
    label: "Embedding",
    sub: "bge-m3",
    detail:
      "Each chunk is embedded locally with bge-m3 into 1024 dimensions. No data leaves the deployment, and there is no per-token embedding cost.",
  },
  {
    id: "store",
    label: "Vector DB",
    sub: "pgvector HNSW",
    detail:
      "Vectors and full-text live in the same Postgres table, so a hybrid query is one statement and a metadata filter narrows both lanes identically. HNSW, not IVFFlat, because the corpus grows continuously.",
  },
  {
    id: "retrieve",
    label: "Hybrid retrieval",
    sub: "BM25 + dense → RRF",
    detail:
      "Two lanes run in parallel: keyword matching for exact identifiers and clause numbers, dense vectors for meaning. Reciprocal Rank Fusion merges them, promoting what both lanes agree on.",
  },
  {
    id: "rerank",
    label: "Reranker",
    sub: "cross-encoder",
    detail:
      "A cross-encoder scores each candidate against the question directly. Two score floors apply: when nothing clears them the model receives no evidence and correctly refuses, instead of being handed filler to hallucinate from.",
  },
  {
    id: "generate",
    label: "Claude",
    sub: "grounded synthesis",
    detail:
      "Claude answers from the retrieved passages only, emitting citations as it writes. Each citation carries the exact span it drew on, not a document-level pointer.",
  },
  {
    id: "verify",
    label: "Verifier",
    sub: "per-claim entailment",
    detail:
      "The answer is split into atomic claims and each is checked against the evidence by a different model. Self-checking inflates scores, so the verifier is never the model that wrote the answer.",
  },
  {
    id: "answer",
    label: "Answer + citations",
    sub: "span-level provenance",
    detail:
      "Every sentence is traceable to a page and a highlighted span, with unsupported claims flagged in the answer itself rather than in a footnote.",
  },
];

const NODE_W = 96;
const RETRIEVE_W = 134; // 1.4x — this node is the diagram's visual centre
const NODE_H = 56;
const GAP = 24;
const PAD = 12;
const ROW_Y = 96;

function layout() {
  const xs: number[] = [];
  let x = PAD;
  for (let i = 0; i < PIPELINE_STAGES.length; i++) {
    xs.push(x);
    x += (PIPELINE_STAGES[i].id === "retrieve" ? RETRIEVE_W : NODE_W) + GAP;
  }
  return { xs, width: x - GAP + PAD };
}

const { xs: X, width: VB_W } = layout();
const VB_H = 210;

export function PipelineDiagram({ className = "" }: { className?: string }) {
  const [active, setActive] = useState<number | null>(null);
  const [drawn, setDrawn] = useState(false);
  const ref = useRef<SVGSVGElement | null>(null);

  useEffect(() => {
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (reduce) {
      setDrawn(true);
      return;
    }
    const el = ref.current;
    if (!el) return;
    const io = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) {
          setDrawn(true);
          io.disconnect(); // draws once — a looping animation reads as a screensaver
        }
      },
      { threshold: 0.35 },
    );
    io.observe(el);
    return () => io.disconnect();
  }, []);

  const detail = active === null ? null : PIPELINE_STAGES[active];

  return (
    <div className={className}>
      {/* Desktop: one horizontal row. */}
      <div className="hidden lg:block">
        <svg
          ref={ref}
          viewBox={`0 0 ${VB_W} ${VB_H}`}
          className="w-full"
          role="list"
          aria-label="Document processing pipeline, ten stages"
        >
          {PIPELINE_STAGES.slice(0, -1).map((s, i) => {
            const from = X[i] + (s.id === "retrieve" ? RETRIEVE_W : NODE_W);
            const to = X[i + 1];
            return (
              <line
                key={`c-${s.id}`}
                x1={from}
                y1={ROW_Y + NODE_H / 2}
                x2={to}
                y2={ROW_Y + NODE_H / 2}
                stroke="var(--border-strong)"
                strokeWidth={2}
                strokeDasharray={GAP}
                strokeDashoffset={drawn ? 0 : GAP}
                style={{
                  transition: `stroke-dashoffset 260ms ease-out ${i * 90}ms`,
                }}
              />
            );
          })}

          {PIPELINE_STAGES.map((s, i) => {
            const w = s.id === "retrieve" ? RETRIEVE_W : NODE_W;
            const terminal = i === 0 || i === PIPELINE_STAGES.length - 1;
            const isActive = active === i;
            const dim = active !== null && !isActive;
            return (
              <g
                key={s.id}
                role="listitem"
                tabIndex={0}
                aria-label={`Stage ${i + 1}: ${s.label}. ${s.sub}.`}
                onMouseEnter={() => setActive(i)}
                onMouseLeave={() => setActive(null)}
                onFocus={() => setActive(i)}
                onBlur={() => setActive(null)}
                className="cursor-pointer outline-none [&:focus-visible>rect]:stroke-[var(--ring)]"
                style={{
                  opacity: dim ? 0.45 : 1,
                  transition: "opacity 120ms ease-out",
                }}
              >
                <rect
                  x={X[i]}
                  y={ROW_Y}
                  width={w}
                  height={NODE_H}
                  rx={8}
                  fill="var(--surface-raised)"
                  stroke={
                    isActive || terminal ? "var(--primary)" : "var(--border-strong)"
                  }
                  strokeWidth={isActive ? 2 : 1}
                />
                <text
                  x={X[i] + w / 2}
                  y={ROW_Y + (s.id === "retrieve" ? 22 : 26)}
                  textAnchor="middle"
                  fontSize={11.5}
                  fontWeight={600}
                  fill="var(--foreground-strong)"
                >
                  {s.label}
                </text>

                {s.id === "retrieve" ? (
                  <>
                    {/* The two lanes, visibly merging. This is the diagram's
                        one "aha" moment, so it gets the extra width. */}
                    <rect
                      x={X[i] + 12}
                      y={ROW_Y + 30}
                      width={48}
                      height={6}
                      rx={3}
                      fill="var(--lane-lexical)"
                    />
                    <rect
                      x={X[i] + 12}
                      y={ROW_Y + 40}
                      width={48}
                      height={6}
                      rx={3}
                      fill="var(--lane-semantic)"
                    />
                    <path
                      d={`M${X[i] + 62} ${ROW_Y + 33} Q${X[i] + 82} ${ROW_Y + 33} ${X[i] + 92} ${ROW_Y + 38}
                          M${X[i] + 62} ${ROW_Y + 43} Q${X[i] + 82} ${ROW_Y + 43} ${X[i] + 92} ${ROW_Y + 38}`}
                      stroke="var(--muted-foreground)"
                      strokeWidth={1.5}
                      fill="none"
                    />
                    <rect
                      x={X[i] + 92}
                      y={ROW_Y + 35}
                      width={28}
                      height={6}
                      rx={3}
                      fill="var(--foreground)"
                    />
                  </>
                ) : (
                  <text
                    x={X[i] + w / 2}
                    y={ROW_Y + 41}
                    textAnchor="middle"
                    fontSize={9.5}
                    fill="var(--muted-foreground)"
                  >
                    {s.sub}
                  </text>
                )}

                <text
                  x={X[i] + w / 2}
                  y={ROW_Y - 12}
                  textAnchor="middle"
                  fontSize={9}
                  fontWeight={600}
                  letterSpacing="0.06em"
                  fill="var(--subtle-foreground)"
                  className="tnum"
                >
                  {String(i + 1).padStart(2, "0")}
                </text>
              </g>
            );
          })}
        </svg>
      </div>

      {/* Mobile/tablet: a separate vertical SVG. Reflowing one viewBox with CSS
          always breaks label wrapping, so this is a second drawing. */}
      <div className="lg:hidden">
        <svg
          viewBox={`0 0 320 ${PIPELINE_STAGES.length * 74 + 20}`}
          className="w-full"
          role="list"
          aria-label="Document processing pipeline, ten stages"
        >
          {PIPELINE_STAGES.map((s, i) => {
            const y = 10 + i * 74;
            const terminal = i === 0 || i === PIPELINE_STAGES.length - 1;
            return (
              <g key={s.id} role="listitem" aria-label={`Stage ${i + 1}: ${s.label}`}>
                {i > 0 && (
                  <line
                    x1={160}
                    y1={y - 18}
                    x2={160}
                    y2={y}
                    stroke="var(--border-strong)"
                    strokeWidth={2}
                  />
                )}
                <rect
                  x={40}
                  y={y}
                  width={240}
                  height={56}
                  rx={8}
                  fill="var(--surface-raised)"
                  stroke={terminal ? "var(--primary)" : "var(--border-strong)"}
                />
                <text x={160} y={y + 24} textAnchor="middle" fontSize={13} fontWeight={600} fill="var(--foreground-strong)">
                  {s.label}
                </text>
                <text x={160} y={y + 41} textAnchor="middle" fontSize={11} fill="var(--muted-foreground)">
                  {s.sub}
                </text>
              </g>
            );
          })}
        </svg>
      </div>

      {/* Detail lands BELOW the diagram, not in a tooltip: tooltips get clipped
          by the SVG viewport and are unusable on touch. */}
      <div className="mt-6 min-h-[76px] rounded-lg border border-border bg-surface p-4">
        {detail ? (
          <>
            <p className="text-title-sm text-foreground-strong">{detail.label}</p>
            <p className="text-ui-sm mt-1 max-w-[76ch] text-muted-foreground">
              {detail.detail}
            </p>
          </>
        ) : (
          <p className="text-ui-sm text-muted-foreground">
            Hover or focus a stage to see what happens there. Every stage&rsquo;s
            output is visible in the app &mdash; including the ones that went wrong.
          </p>
        )}
      </div>
    </div>
  );
}
