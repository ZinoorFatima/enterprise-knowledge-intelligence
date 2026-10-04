import Link from "next/link";
import { PipelineDiagram } from "@/components/marketing/pipeline-diagram";

export const metadata = {
  title: "How it works",
  description:
    "Ten stages from PDF to cited answer: parsing, OCR, semantic chunking, hybrid retrieval, reranking, grounded generation and per-claim verification.",
};

const STAGES = [
  {
    n: "01",
    title: "Parsing and OCR",
    body: "Text is extracted directly where a usable text layer exists. Three cases get routed to OCR instead: pages with no text at all, image-dominant pages, and — the one most systems miss — pages whose text layer is broken-CID garbage. That last case returns text-shaped nonsense that parsers accept happily; left unchecked it gets embedded and indexed, poisoning keyword search with noise.",
    note: "An already-OCR’d scan with an invisible text layer is deliberately left alone, which avoids re-OCR on the most common kind of enterprise scan.",
  },
  {
    n: "02",
    title: "Semantic chunking",
    body: "Sentences are embedded with their neighbours, and the document is cut where adjacent meaning diverges most — at the 95th percentile of distance, not a fixed threshold. A fixed cosine cutoff does not transfer between a dense contract and a slide deck; a percentile does.",
    note: "Every chunk’s text is a literal slice of the source, never a re-join. That exactness is what makes a citation resolve to a real page rather than an estimate.",
  },
  {
    n: "03",
    title: "Hybrid retrieval",
    body: "Two lanes run against the same filtered candidate set: keyword matching for exact identifiers and clause numbers, dense vectors for meaning. Reciprocal Rank Fusion merges them, promoting what both lanes agree on rather than whichever produced a single confident hit.",
    note: "Filters are pushed into both lanes. Filtering after fusion would bias results toward whichever lane happened to surface in-filter documents.",
  },
  {
    n: "04",
    title: "Reranking and score floors",
    body: "A reranker scores each candidate against the question directly, then two floors apply — one absolute, one relative to the best result. Anything below both is dropped rather than padded into the context.",
    note: "This is the main structural defence against hallucination. A question the corpus cannot answer must reach the model with little or no evidence, so it can correctly refuse instead of being handed filler to improvise from.",
  },
  {
    n: "05",
    title: "Grounded generation",
    body: "The model answers from the retrieved passages only, emitting citations as it writes. Each citation carries the exact span it drew on, resolved by index arithmetic against structures the system built — not by matching strings after the fact.",
    note: "Because resolution is index-based, there is no class of bug where a citation fails to resolve or silently points at the wrong passage.",
  },
  {
    n: "06",
    title: "Verification",
    body: "The answer is split into atomic claims and each is checked for entailment against the evidence. A claim whose verdict carries no decisive quote is downgraded to unsupported, and a verdict citing a passage that was never sent is rejected outright.",
    note: "The verifier is never the model that wrote the answer. Self-grading inflates faithfulness measurably, so the system raises an error rather than permitting it.",
  },
] as const;

export default function HowItWorksPage() {
  return (
    <div className="mx-auto max-w-[1120px] px-6 py-20">
      <p className="text-overline text-muted-foreground">How it works</p>
      <h1 className="text-display-2 mt-3 max-w-[20ch] text-foreground-strong">
        Ten stages, all inspectable.
      </h1>
      <p className="text-body-lg mt-5 max-w-[66ch] text-muted-foreground">
        Every stage writes its output into a trace the product renders. The
        rewritten query, both retrieval lanes, the fusion, the rerank movement
        and the cited spans are all visible &mdash; including when a stage got it
        wrong.
      </p>

      <PipelineDiagram className="mt-14" />

      <div className="mt-20 space-y-14">
        {STAGES.map((s) => (
          <section key={s.n} className="grid gap-5 md:grid-cols-[88px_minmax(0,1fr)]">
            <div className="text-title-lg tnum font-mono text-subtle-foreground">{s.n}</div>
            <div>
              <h2 className="text-display-3 text-foreground-strong">{s.title}</h2>
              <p className="text-body-lg mt-3 max-w-[68ch] text-muted-foreground">{s.body}</p>
              <p className="text-ui-sm mt-4 max-w-[68ch] border-l-2 border-primary-border pl-4 text-muted-foreground">
                {s.note}
              </p>
            </div>
          </section>
        ))}
      </div>

      <div className="mt-20 rounded-xl border border-border bg-surface p-8">
        <h2 className="text-display-3 text-foreground-strong">
          Does any of it actually help?
        </h2>
        <p className="text-body mt-3 max-w-[66ch] text-muted-foreground">
          Each stage above adds latency, so each one should have to justify
          itself. The evaluation page gives the ablation &mdash; dense only,
          keyword only, hybrid, hybrid plus reranking &mdash; and names the one
          feature that does not clearly earn its cost.
        </p>
        <Link
          href="/evaluation"
          className="text-ui mt-6 inline-flex h-10 items-center rounded-md bg-primary px-4 font-medium text-primary-foreground transition-colors duration-[120ms] hover:bg-primary-hover"
        >
          See the measurements
        </Link>
      </div>
    </div>
  );
}
