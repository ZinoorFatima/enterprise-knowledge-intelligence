"use client";

import { useCallback, useRef, useState } from "react";
import type { AskResponse, Citation } from "@/lib/ask-types";
import { parseSSE } from "@/lib/sse";
import { RetrievalInspector } from "@/components/app/retrieval-inspector";
import { VerificationPanel } from "@/components/app/verification-panel";
import dynamic from "next/dynamic";

// pdf.js needs window and a worker, so it must never render on the server.
const PdfViewer = dynamic(
  () => import("@/components/app/pdf-viewer").then((m) => m.PdfViewer),
  { ssr: false, loading: () => <div className="h-72 rounded-md border border-border bg-surface-sunken" /> },
);

type Tab = "sources" | "retrieval" | "verification";

/** Shape used while a stream is still filling in, so partial state renders. */
const EMPTY_ANSWER: AskResponse = {
  answer: "",
  refused: false,
  citations: [],
  retrieval: { lanes: {}, fused: 0, reranked: [] },
  rewrite: null,
  verification: null,
  latency_ms: {},
  errors: [],
};

export function AskWorkspace() {
  const [question, setQuestion] = useState("");
  const [pending, setPending] = useState(false);
  const [data, setData] = useState<AskResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [dockOpen, setDockOpen] = useState(false);
  const [tab, setTab] = useState<Tab>("sources");
  const [active, setActive] = useState<number | null>(null);
  const [claimSpan, setClaimSpan] = useState<[number, number] | null>(null);
  // Which pipeline stage is running, so the wait is legible rather than a spinner.
  const [stage, setStage] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  const ask = useCallback(async () => {
    const q = question.trim();
    if (!q) return;
    abortRef.current?.abort();
    const ac = new AbortController();
    abortRef.current = ac;

    setPending(true);
    setError(null);
    setData(null);
    setActive(null);
    setStage("Retrieving and ranking passages");

    try {
      const res = await fetch("/api/ask/stream", {
        method: "POST",
        headers: { "content-type": "application/json", accept: "text/event-stream" },
        body: JSON.stringify({ question: q }),
        signal: ac.signal,
      });
      if (!res.ok || !res.body) {
        const body = await res.json().catch(() => ({}));
        setError(body?.detail ?? body?.error ?? `Request failed (${res.status})`);
        return;
      }

      let sawDone = false;
      for await (const ev of parseSSE(res.body, ac.signal)) {
        if (ev.event === "rewrite") {
          setStage("Searching both retrieval lanes");
        } else if (ev.event === "retrieval") {
          // Partial render: sources appear while generation is still running.
          // This is the largest perceived-latency win available and it costs
          // nothing, because the work has genuinely finished.
          setData((prev) => ({
            ...(prev ?? EMPTY_ANSWER),
            retrieval: ev.data,
          }));
          if (ev.data?.reranked?.length) setDockOpen(true);
          setStage("Generating a grounded answer");
        } else if (ev.event === "answer") {
          setData((prev) => ({
            ...(prev ?? EMPTY_ANSWER),
            answer: ev.data.text ?? "",
            refused: !!ev.data.refused,
          }));
          setStage("Verifying claims against the evidence");
        } else if (ev.event === "verification") {
          setData((prev) => ({ ...(prev ?? EMPTY_ANSWER), verification: ev.data }));
        } else if (ev.event === "done") {
          sawDone = true;
          setData(ev.data as AskResponse);
        } else if (ev.event === "error") {
          setError(ev.data?.message ?? "The server reported an error");
          return;
        }
      }
      // A stream that ends without `done` was cut off, not completed. Saying so
      // is better than presenting a partial answer as finished.
      if (!sawDone) {
        setError((e) => e ?? "The connection closed before the answer finished.");
      }
    } catch (e) {
      if ((e as Error).name !== "AbortError") {
        setError(e instanceof Error ? e.message : "Request failed");
      }
    } finally {
      setPending(false);
      setStage(null);
    }
  }, [question]);

  const openCitation = useCallback((c: Citation) => {
    setActive(c.evidence_index);
    setTab("sources");
    setDockOpen(true);
  }, []);

  return (
    <div
      className="grid h-[calc(100vh-52px)]"
      style={{ gridTemplateColumns: dockOpen ? "minmax(0,1fr) 420px" : "minmax(0,1fr)" }}
    >
      {/* Conversation */}
      <section className="overflow-y-auto px-6 py-8">
        <div className="mx-auto max-w-[72ch]">
          <h1 className="text-title-lg text-foreground-strong">Ask</h1>
          <p className="text-ui-sm mt-1 text-muted-foreground">
            Answers cite the exact passage they came from, and every claim is
            checked against the retrieved evidence.
          </p>

          <div className="mt-6 flex gap-2">
            <input
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && (e.metaKey || e.ctrlKey || !e.shiftKey)) {
                  e.preventDefault();
                  ask();
                }
              }}
              placeholder="What is the liability cap?"
              className="text-ui h-10 flex-1 rounded-md border border-border-strong bg-background px-3 text-foreground placeholder:text-subtle-foreground"
            />
            <button
              onClick={ask}
              disabled={pending || !question.trim()}
              className="text-ui inline-flex h-10 items-center rounded-md bg-primary px-4 font-medium text-primary-foreground transition-colors duration-[120ms] hover:bg-primary-hover disabled:opacity-60"
            >
              {pending ? "Asking…" : "Ask"}
            </button>
          </div>

          {pending && stage && (
            <p className="text-ui-sm mt-6 text-muted-foreground" aria-live="polite">
              {stage}&hellip;
            </p>
          )}

          {error && (
            <div role="alert" className="mt-6 rounded-md border border-danger bg-danger-subtle px-4 py-3">
              <p className="text-ui-sm text-danger">{error}</p>
            </div>
          )}

          {data && (
            <article className="mt-8">
              {data.refused && (
                <div className="mb-4 rounded-md border border-warning bg-warning-subtle px-4 py-3">
                  <p className="text-ui-sm text-warning">
                    Nothing retrieved cleared the relevance threshold, so the
                    system declined rather than answering from weak evidence.
                  </p>
                </div>
              )}

              <AnswerText
                text={data.answer}
                citations={data.citations}
                onCitation={openCitation}
                highlight={claimSpan}
              />

              {data.errors.length > 0 && (
                <ul className="mt-5 space-y-1">
                  {data.errors.map((e, i) => (
                    <li key={i} className="text-caption text-warning">
                      {e}
                    </li>
                  ))}
                </ul>
              )}

              {!dockOpen && (
                <button
                  onClick={() => setDockOpen(true)}
                  className="text-ui-sm mt-6 font-medium text-primary hover:underline underline-offset-4"
                >
                  Show how this answer was retrieved &rarr;
                </button>
              )}
            </article>
          )}
        </div>
      </section>

      {/* Inspector dock */}
      {dockOpen && (
        <aside
          id="inspector-dock"
          role="complementary"
          aria-label="Source inspector"
          className="overflow-y-auto border-l border-border bg-surface"
        >
          <div className="sticky top-0 z-10 flex items-center gap-1 border-b border-border bg-surface px-3 py-2">
            {(["sources", "retrieval", "verification"] as const).map((t) => (
              <button
                key={t}
                onClick={() => setTab(t)}
                aria-selected={tab === t}
                className={`text-ui-sm rounded-md px-2.5 py-1 capitalize transition-colors duration-[120ms] ${
                  tab === t
                    ? "bg-primary-subtle font-medium text-primary"
                    : "text-muted-foreground hover:bg-surface-sunken hover:text-foreground"
                }`}
              >
                {t}
              </button>
            ))}
            <button
              onClick={() => setDockOpen(false)}
              aria-label="Close inspector"
              className="text-ui-sm ml-auto rounded-md px-2 py-1 text-muted-foreground hover:bg-surface-sunken hover:text-foreground"
            >
              &times;
            </button>
          </div>

          {!data ? (
            <p className="text-ui-sm px-4 py-6 text-muted-foreground">
              Ask a question to inspect how it was answered.
            </p>
          ) : tab === "sources" ? (
            <SourcesTab citations={data.citations} active={active} />
          ) : tab === "retrieval" ? (
            <RetrievalInspector data={data} />
          ) : (
            <VerificationPanel verification={data.verification} onHoverClaim={setClaimSpan} />
          )}
        </aside>
      )}
    </div>
  );
}

/** Renders the answer, replacing nothing in the text itself: citation chips are
 *  appended per-citation beneath the prose until the backend emits inline
 *  sentinels. A chip is a real button so it is keyboard reachable. */
function AnswerText({
  text,
  citations,
  onCitation,
  highlight,
}: {
  text: string;
  citations: Citation[];
  onCitation: (c: Citation) => void;
  highlight: [number, number] | null;
}) {
  const before = highlight ? text.slice(0, highlight[0]) : text;
  const marked = highlight ? text.slice(highlight[0], highlight[1]) : "";
  const after = highlight ? text.slice(highlight[1]) : "";

  return (
    <>
      <div className="text-body whitespace-pre-wrap text-foreground">
        {highlight ? (
          <>
            {before}
            <mark style={{ background: "var(--highlight-citation)", color: "inherit" }}>
              {marked}
            </mark>
            {after}
          </>
        ) : (
          text
        )}
      </div>

      {citations.length > 0 && (
        <div className="mt-4 flex flex-wrap items-center gap-1.5">
          <span className="text-caption text-muted-foreground">Sources:</span>
          {citations.map((c) => (
            <button
              key={`${c.evidence_index}-${c.marker}`}
              onClick={() => onCitation(c)}
              aria-label={`Source ${c.marker}: ${c.title}, page ${c.page}. Open in source viewer.`}
              className="inline-flex h-[20px] items-center rounded-[5px] border border-primary-border bg-primary-subtle px-1.5 font-mono text-[11px] leading-none text-primary transition-colors duration-[120ms] hover:bg-primary hover:text-primary-foreground"
            >
              {c.marker}
            </button>
          ))}
        </div>
      )}
    </>
  );
}

function SourcesTab({ citations, active }: { citations: Citation[]; active: number | null }) {
  // Opening the viewer for whichever citation the user clicked; defaults to the
  // first so the pane is never an empty frame.
  const [openIdx, setOpenIdx] = useState<number | null>(
    citations.length ? citations[0].evidence_index : null,
  );
  const shown = active ?? openIdx;
  const current = citations.find((c) => c.evidence_index === shown) ?? citations[0];

  if (citations.length === 0) {
    return (
      <p className="text-ui-sm px-4 py-6 text-muted-foreground">
        No citations &mdash; the answer was not grounded in retrieved passages.
      </p>
    );
  }

  return (
    <div>
      <ul className="divide-y divide-border border-b border-border">
        {citations.map((c) => (
          <li key={`${c.evidence_index}-${c.marker}`}>
            <button
              onClick={() => setOpenIdx(c.evidence_index)}
              className="w-full px-4 py-3 text-left transition-colors duration-[120ms] hover:bg-surface-sunken"
              style={{
                background:
                  shown === c.evidence_index ? "var(--primary-subtle)" : undefined,
              }}
              aria-current={shown === c.evidence_index}
            >
              <div className="flex items-baseline gap-2">
                <span className="text-caption tnum font-mono text-primary">[{c.marker}]</span>
                <span className="text-ui-sm text-foreground-strong">{c.title}</span>
                <span className="text-caption tnum ml-auto font-mono text-muted-foreground">
                  p.{c.page}
                </span>
              </div>
              <blockquote className="text-ui-sm mt-1.5 border-l-2 border-border pl-2.5 text-muted-foreground">
                {c.cited_text}
              </blockquote>
            </button>
          </li>
        ))}
      </ul>

      {current && (
        <div className="p-3">
          <PdfViewer
            documentId={current.document_id}
            page={current.page}
            citedText={current.cited_text}
            title={current.title}
          />
        </div>
      )}
    </div>
  );
}
