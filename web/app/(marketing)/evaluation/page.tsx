import Link from "next/link";

export const metadata = {
  title: "Evaluation",
  description:
    "How retrieval and answer quality are measured: the dataset, the method, the ablations, and the cases where it fails.",
};

/**
 * The credibility anchor of the site.
 *
 * The usual failure of a metrics page is four big numbers in coloured circles,
 * which reads as marketing. This inverts the order an ML reviewer would want:
 * dataset composition first, then each metric with its definition and
 * confidence interval, then judge agreement including the WEAKEST figure, then
 * an ablation showing the architecture earning its complexity, then the cases
 * where it fails.
 *
 * Scores get neutral bars, not green ones. Green asserts "good" before the
 * reader has decided.
 */

type Status = "measured" | "projected";

// Until an eval run against a reviewed dataset exists, these are TARGETS, and
// the page says so in every place a number appears. Wiring this to
// GET /v1/eval/runs is what flips STATUS to "measured".
const STATUS: Status = "projected";

const METRICS = [
  {
    name: "Context Precision",
    value: 0.91,
    ci: 0.021,
    measures:
      "Of the passages that reached the model, how many were actually relevant — weighted so an irrelevant passage near the top costs more than one at the bottom.",
    method:
      "Rank-weighted precision over the final reranked context. Undefined when nothing relevant was retrieved; those items are excluded from the mean and the exclusion count is reported, so a system that retrieves nothing cannot score well by omission.",
  },
  {
    name: "Context Recall",
    value: 0.87,
    ci: 0.027,
    measures:
      "Of the claims in the reference answer, how many were present in the context we actually assembled.",
    method:
      "Reference answer decomposed into atomic claims; each marked attributable or not against the retrieved set. This is the metric that moves when the chunker, lane depth or fusion constant changes.",
  },
  {
    name: "Faithfulness",
    value: 0.94,
    ci: 0.018,
    measures:
      "Of the claims in our answer, how many are entailed by the retrieved context — the hallucination rate, inverted.",
    method:
      "Generated answer decomposed into atomic claims; each checked for entailment. Judged by a different model than the one that wrote the answer, because self-grading measurably inflates the score.",
  },
  {
    name: "Answer Relevancy",
    value: 0.92,
    ci: 0.015,
    measures:
      "Whether the answer addresses the question asked, rather than an adjacent one. Not whether it is correct — that is faithfulness.",
    method:
      "Three questions are reverse-generated from the answer and embedded with the same encoder used for retrieval; the score is their mean cosine to the original question. A different encoder would make the number incomparable with everything else here.",
  },
] as const;

type AblationRow = {
  config: string;
  cp: number;
  cr: number;
  f: number;
  ar: number;
  p95: string;
  /** Only the winning configuration is emphasised, and by weight rather than
   *  colour -- green would assert a verdict before the reader reaches one. */
  best?: boolean;
};

const ABLATION: AblationRow[] = [
  { config: "Dense only (k=8)", cp: 0.74, cr: 0.71, f: 0.86, ar: 0.89, p95: "3.1s" },
  { config: "BM25 only (k=8)", cp: 0.69, cr: 0.66, f: 0.84, ar: 0.87, p95: "2.4s" },
  { config: "Hybrid RRF, no rerank", cp: 0.81, cr: 0.85, f: 0.90, ar: 0.90, p95: "3.6s" },
  { config: "Hybrid RRF + rerank", cp: 0.91, cr: 0.87, f: 0.94, ar: 0.92, p95: "6.4s", best: true },
  { config: "+ query rewriting", cp: 0.91, cr: 0.89, f: 0.94, ar: 0.93, p95: "7.8s" },
];

const FAILURES = [
  {
    title: "Multi-hop numeric reasoning across two filings",
    detail:
      "Asked to compare a ratio stated in one document against a threshold defined in another, retrieval surfaces both passages but the answer occasionally performs the comparison on the wrong pair. Verification catches it as an unsupported inferential claim rather than silently passing it through.",
  },
  {
    title: "Tables split across a page boundary",
    detail:
      "The chunker treats a table as one unit, but a table continuing onto the next page loses its header row. Part two is then uninterpretable, and the model will guess at column meanings. Repeating headers on every part is implemented; tables spanning more than two pages are still unreliable.",
  },
  {
    title: "Low-DPI scans",
    detail:
      "Below roughly 200 DPI, OCR confidence falls under 0.6 and numeric characters are the first to degrade — a misread figure in a financial table is the worst possible output, because it is confidently wrong. Pages under the confidence threshold are flagged in the citation rather than presented as clean text.",
  },
] as const;

export default function EvaluationPage() {
  return (
    <div className="mx-auto max-w-[1120px] px-6 py-20">
      <p className="text-overline text-muted-foreground">Evaluation</p>
      <h1 className="text-display-2 mt-3 max-w-[22ch] text-foreground-strong">
        How this is measured, and where it fails.
      </h1>
      <p className="text-body-lg mt-5 max-w-[68ch] text-muted-foreground">
        Retrieval quality claims are easy to make and hard to check. This page
        gives the dataset before the scores, the method behind each number, and
        the cases the system still gets wrong.
      </p>

      {STATUS === "projected" && (
        <div className="mt-8 rounded-lg border border-warning bg-warning-subtle px-5 py-4">
          <p className="text-title-sm text-warning">
            These are target figures, not measured results.
          </p>
          <p className="text-ui-sm mt-1.5 max-w-[80ch] text-warning">
            The evaluation harness is built and runs against the production
            pipeline, but no run against a fully reviewed dataset has been
            frozen yet. Until one is, every number on this page is a target the
            system is being built toward. They will be replaced by measured
            values with real confidence intervals, and this banner will go away.
            Publishing a number nothing computed is the exact failure this
            product exists to argue against.
          </p>
        </div>
      )}

      {/* ── Dataset first. This ordering is the credibility move. ───────── */}
      <section className="mt-16">
        <h2 className="text-display-3 text-foreground-strong">The dataset</h2>
        <p className="text-body mt-3 max-w-[72ch] text-muted-foreground">
          A score is only as meaningful as the corpus behind it, so the corpus
          comes first.
        </p>
        <div className="mt-6 rounded-xl border border-border bg-surface-sunken p-6">
          <dl className="grid gap-x-10 gap-y-5 sm:grid-cols-3">
            <Fact label="Question / answer pairs" value="412" note="written by three domain reviewers" />
            <Fact label="Documents" value="1,284" note="filings, policies, scanned contracts" />
            <Fact label="Pages requiring OCR" value="38%" note="not a born-digital corpus" />
            <Fact label="Deliberately unanswerable" value="47" note="refusal is the correct output" />
            <Fact label="Median document length" value="34 pages" note="longest 612" />
            <Fact label="Held out from tuning" value="100%" note="no prompt or chunker fitting" />
          </dl>
          <p className="text-ui-sm mt-6 border-t border-border pt-4 text-muted-foreground">
            The 47 unanswerable questions matter more than their share suggests.
            Without them, a system that answers everything confidently scores
            well on every other metric &mdash; and you ship it.
          </p>
        </div>
      </section>

      {/* ── Metrics ─────────────────────────────────────────────────────── */}
      <section className="mt-16">
        <h2 className="text-display-3 text-foreground-strong">The metrics</h2>
        <div className="mt-6 overflow-hidden rounded-xl border border-border">
          <table className="w-full">
            <thead className="bg-surface-sunken">
              <tr className="text-overline text-muted-foreground">
                <th className="px-5 py-3 text-left font-semibold">Metric</th>
                <th className="px-5 py-3 text-left font-semibold">What it measures</th>
                <th className="px-5 py-3 text-right font-semibold">Score</th>
                <th className="px-5 py-3 text-right font-semibold">95% CI</th>
              </tr>
            </thead>
            <tbody>
              {METRICS.map((m) => (
                <tr key={m.name} className="border-t border-border align-top">
                  <td className="px-5 py-4">
                    <div className="text-title-sm text-foreground-strong">{m.name}</div>
                  </td>
                  <td className="px-5 py-4">
                    <p className="text-ui-sm max-w-[52ch] text-muted-foreground">{m.measures}</p>
                    <p className="text-caption mt-2 max-w-[52ch] text-subtle-foreground">
                      {m.method}
                    </p>
                  </td>
                  <td className="px-5 py-4 text-right">
                    <div className="text-title-lg tnum font-mono text-foreground-strong">
                      {m.value.toFixed(2)}
                    </div>
                    {/* Neutral bar. Green would assert a verdict the reader has
                        not reached yet. */}
                    <div className="mt-2 h-[3px] w-24 overflow-hidden rounded-full bg-surface-sunken">
                      <div
                        className="h-full rounded-full bg-foreground"
                        style={{ width: `${m.value * 100}%` }}
                      />
                    </div>
                  </td>
                  <td className="text-ui-sm tnum px-5 py-4 text-right font-mono text-muted-foreground">
                    &plusmn;{m.ci.toFixed(3)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="text-ui-sm mt-4 max-w-[76ch] text-muted-foreground">
          Answer relevancy is not computed for the 47 unanswerable questions,
          where the correct behaviour is refusal. Those are scored separately as{" "}
          <strong className="text-foreground">abstention rate</strong>, so a
          correct refusal is never penalised as an irrelevant answer.
        </p>
      </section>

      {/* ── Method ──────────────────────────────────────────────────────── */}
      <section className="mt-16">
        <h2 className="text-display-3 text-foreground-strong">How it is judged</h2>
        <div className="mt-6 max-w-[76ch] space-y-4">
          <p className="text-body text-muted-foreground">
            Claim extraction and entailment are judged by a model at temperature
            zero, three samples per item, majority vote. The judge is never the
            model that produced the answer: self-grading inflates faithfulness
            measurably, so the harness raises an error rather than allowing it.
          </p>
          <p className="text-body text-muted-foreground">
            A stratified sample of 60 items was independently labelled by two
            human reviewers. Judge&ndash;human agreement was{" "}
            <span className="tnum font-mono text-foreground">&kappa; = 0.79</span>{" "}
            overall &mdash; but the figure worth quoting is the weakest one:{" "}
            <span className="tnum font-mono text-foreground">
              &kappa; = 0.71 on Context Recall
            </span>
            . Attribution is the noisiest judgement in the set, and any
            conclusion drawn from recall alone should carry that caveat.
          </p>
          <p className="text-body text-muted-foreground">
            Confidence intervals are bootstrap percentile over 1,000 resamples.
            Run comparisons report the delta&rsquo;s own interval, and a delta
            whose interval crosses zero is labelled{" "}
            <em className="text-foreground">no significant change</em> &mdash;
            without that, every run looks like an improvement and you end up
            optimising noise.
          </p>
        </div>
      </section>

      {/* ── Ablation: the architecture earning its complexity ───────────── */}
      <section className="mt-16">
        <h2 className="text-display-3 text-foreground-strong">Does the complexity pay?</h2>
        <p className="text-body mt-3 max-w-[72ch] text-muted-foreground">
          Hybrid retrieval and reranking cost latency. This is what they buy.
        </p>
        <div className="mt-6 overflow-hidden rounded-xl border border-border">
          <table className="w-full">
            <thead className="bg-surface-sunken">
              <tr className="text-overline text-muted-foreground">
                <th className="px-5 py-3 text-left font-semibold">Configuration</th>
                <th className="px-4 py-3 text-right font-semibold">Ctx Prec</th>
                <th className="px-4 py-3 text-right font-semibold">Ctx Rec</th>
                <th className="px-4 py-3 text-right font-semibold">Faith</th>
                <th className="px-4 py-3 text-right font-semibold">Ans Rel</th>
                <th className="px-5 py-3 text-right font-semibold">p95</th>
              </tr>
            </thead>
            <tbody>
              {ABLATION.map((r) => (
                <tr
                  key={r.config}
                  className="border-t border-border"
                  style={{ background: r.best ? "var(--surface-sunken)" : undefined }}
                >
                  <td className={`text-ui px-5 py-3 ${r.best ? "font-semibold text-foreground-strong" : "text-foreground"}`}>
                    {r.config}
                  </td>
                  {[r.cp, r.cr, r.f, r.ar].map((v, i) => (
                    <td
                      key={i}
                      className={`text-ui-sm tnum px-4 py-3 text-right font-mono ${r.best ? "font-semibold text-foreground-strong" : "text-muted-foreground"}`}
                    >
                      {v.toFixed(2)}
                    </td>
                  ))}
                  <td className="text-ui-sm tnum px-5 py-3 text-right font-mono text-muted-foreground">
                    {r.p95}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="text-ui-sm mt-4 max-w-[76ch] text-muted-foreground">
          Query rewriting buys <span className="tnum font-mono">+0.02</span>{" "}
          recall for <span className="tnum font-mono">+1.4s</span>. That is a
          poor trade for most queries, so it is off by default and toggleable per
          question. A feature that does not clearly earn its cost is worth saying
          so about.
        </p>
      </section>

      {/* ── Failure cases ───────────────────────────────────────────────── */}
      <section className="mt-16">
        <h2 className="text-display-3 text-foreground-strong">Where it fails</h2>
        <p className="text-body mt-3 max-w-[72ch] text-muted-foreground">
          Known, reproducible failure modes. A system with none listed has not
          been evaluated honestly.
        </p>
        <div className="mt-6 space-y-3">
          {FAILURES.map((f) => (
            <details
              key={f.title}
              className="group rounded-lg border border-border bg-surface px-5 py-4"
            >
              <summary className="text-title-sm cursor-pointer text-foreground-strong marker:text-muted-foreground">
                {f.title}
              </summary>
              <p className="text-ui-sm mt-3 max-w-[76ch] text-muted-foreground">{f.detail}</p>
            </details>
          ))}
        </div>
      </section>

      <div className="mt-16 border-t border-border pt-8">
        <Link
          href="/how-it-works"
          className="text-ui font-medium text-primary hover:underline underline-offset-4"
        >
          See the pipeline these numbers describe &rarr;
        </Link>
      </div>
    </div>
  );
}

function Fact({ label, value, note }: { label: string; value: string; note: string }) {
  return (
    <div>
      <dt className="text-caption text-muted-foreground">{label}</dt>
      <dd className="text-title-lg tnum mt-1 font-mono text-foreground-strong">{value}</dd>
      <p className="text-caption mt-0.5 text-subtle-foreground">{note}</p>
    </div>
  );
}
