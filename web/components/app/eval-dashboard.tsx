"use client";

import { useCallback, useState } from "react";

interface MetricSummary {
  mean: number | null;
  n: number;
  undefined: number;
  ci_low: number | null;
  ci_high: number | null;
}

interface RunSummary {
  n_items: number;
  llm_mode: string;
  provisional: boolean;
  provisional_reasons: string[];
  metrics: Record<string, MetricSummary>;
  refusal_accuracy: number | null;
  refusal_n: number;
  p50_latency_ms: number;
  p95_latency_ms: number;
  config: Record<string, unknown>;
  results: {
    item_id: string;
    refused: boolean;
    expected_behavior: string;
    context_precision: number | null;
    context_recall: number | null;
    faithfulness: number | null;
    error: string | null;
  }[];
}

const HEADLINE = [
  ["context_precision", "Context Precision"],
  ["context_recall", "Context Recall"],
  ["faithfulness", "Faithfulness"],
  ["answer_relevancy", "Answer Relevancy"],
] as const;

const SECONDARY = [
  ["ndcg_at_10", "nDCG@10"],
  ["mrr", "MRR"],
  ["recall_at_20", "Recall@20"],
] as const;

export function EvalDashboard() {
  const [run, setRun] = useState<RunSummary | null>(null);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const start = useCallback(async () => {
    setPending(true);
    setError(null);
    setRun(null);
    try {
      const res = await fetch("/api/eval", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ dataset: "smoke" }),
      });
      const body = await res.json();
      if (!res.ok) {
        setError(body?.detail ?? body?.error ?? `Run failed (${res.status})`);
        return;
      }
      setRun(body as RunSummary);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Run failed");
    } finally {
      setPending(false);
    }
  }, []);

  return (
    <div className="mx-auto max-w-[1120px] px-6 py-10">
      <div className="flex items-start justify-between gap-6">
        <div>
          <h1 className="text-title-lg text-foreground-strong">Quality</h1>
          <p className="text-ui-sm mt-1 max-w-[70ch] text-muted-foreground">
            Runs the golden dataset through the same pipeline that serves
            answers. If measurement used a separate implementation, its numbers
            would describe a system nobody ships.
          </p>
        </div>
        <button
          onClick={start}
          disabled={pending}
          className="text-ui inline-flex h-10 shrink-0 items-center rounded-md bg-primary px-4 font-medium text-primary-foreground transition-colors duration-[120ms] hover:bg-primary-hover disabled:opacity-60"
        >
          {pending ? "Running…" : "Run evaluation"}
        </button>
      </div>

      {pending && (
        <p className="text-ui-sm mt-8 text-muted-foreground" aria-live="polite">
          Each item walks the full graph &mdash; rewrite, retrieval, fusion,
          reranking, generation and verification. This takes a while.
        </p>
      )}

      {error && (
        <div role="alert" className="mt-8 rounded-md border border-danger bg-danger-subtle px-4 py-3">
          <p className="text-ui-sm text-danger">{error}</p>
        </div>
      )}

      {run && (
        <div className="mt-8 space-y-8">
          {/* The provisional banner is the mechanism that stops synthetic or
              partially-measured numbers being read as results. */}
          {run.provisional && (
            <div className="rounded-lg border border-warning bg-warning-subtle px-5 py-4">
              <p className="text-title-sm text-warning">
                Provisional &mdash; these numbers are not publishable as measured
              </p>
              <ul className="mt-2 space-y-1">
                {run.provisional_reasons.map((r) => (
                  <li key={r} className="text-ui-sm text-warning">
                    &bull; {r}
                  </li>
                ))}
              </ul>
            </div>
          )}

          <section>
            <h2 className="text-title-sm text-foreground-strong">Answer quality</h2>
            <div className="mt-3 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
              {HEADLINE.map(([key, label]) => (
                <MetricCard key={key} label={label} m={run.metrics[key]} />
              ))}
            </div>
          </section>

          <section>
            <h2 className="text-title-sm text-foreground-strong">
              Retrieval quality
              <span className="text-caption ml-2 font-normal text-muted-foreground">
                deterministic &mdash; no judge involved
              </span>
            </h2>
            <div className="mt-3 grid gap-4 sm:grid-cols-3">
              {SECONDARY.map(([key, label]) => (
                <MetricCard key={key} label={label} m={run.metrics[key]} />
              ))}
            </div>
          </section>

          <section className="grid gap-4 sm:grid-cols-3">
            <Stat
              label="Refusal accuracy"
              value={run.refusal_accuracy === null ? null : run.refusal_accuracy.toFixed(3)}
              note={
                run.refusal_n
                  ? `over ${run.refusal_n} unanswerable items`
                  : "no unanswerable items in dataset"
              }
            />
            <Stat label="p50 latency" value={`${Math.round(run.p50_latency_ms)} ms`} note="per item" />
            <Stat label="p95 latency" value={`${Math.round(run.p95_latency_ms)} ms`} note="per item" />
          </section>

          <section>
            <h2 className="text-title-sm text-foreground-strong">Per item</h2>
            <div className="mt-3 overflow-hidden rounded-lg border border-border">
              <table className="w-full">
                <thead className="bg-surface-sunken">
                  <tr className="text-overline text-muted-foreground">
                    <th className="px-4 py-2.5 text-left font-semibold">Item</th>
                    <th className="px-4 py-2.5 text-left font-semibold">Expected</th>
                    <th className="px-4 py-2.5 text-right font-semibold">Ctx Prec</th>
                    <th className="px-4 py-2.5 text-right font-semibold">Ctx Rec</th>
                    <th className="px-4 py-2.5 text-right font-semibold">Faith</th>
                    <th className="px-4 py-2.5 text-left font-semibold">Outcome</th>
                  </tr>
                </thead>
                <tbody>
                  {run.results.map((r) => (
                    <tr key={r.item_id} className="border-t border-border">
                      <td className="text-ui-sm tnum px-4 py-2.5 font-mono text-foreground">
                        {r.item_id}
                      </td>
                      <td className="text-ui-sm px-4 py-2.5 text-muted-foreground">
                        {r.expected_behavior}
                      </td>
                      <Cell v={r.context_precision} />
                      <Cell v={r.context_recall} />
                      <Cell v={r.faithfulness} />
                      <td className="text-ui-sm px-4 py-2.5">
                        {r.error ? (
                          <span className="text-danger">{r.error.slice(0, 40)}</span>
                        ) : r.refused ? (
                          <span
                            style={{
                              color:
                                r.expected_behavior === "refuse"
                                  ? "var(--success)"
                                  : "var(--warning)",
                            }}
                          >
                            refused
                          </span>
                        ) : (
                          <span className="text-muted-foreground">answered</span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className="text-caption mt-2 text-subtle-foreground">
              A dash means the metric is undefined for that item, not zero
              &mdash; context precision has no value when nothing relevant was
              retrieved, and averaging a zero there would flatter the system.
            </p>
          </section>

          <details className="rounded-lg border border-border bg-surface px-5 py-4">
            <summary className="text-title-sm cursor-pointer text-foreground-strong">
              Configuration snapshot
            </summary>
            <dl className="mt-3 grid gap-x-8 gap-y-1 sm:grid-cols-2">
              {Object.entries(run.config).map(([k, v]) => (
                <div key={k} className="flex justify-between gap-4">
                  <dt className="text-ui-sm text-muted-foreground">{k}</dt>
                  <dd className="text-ui-sm tnum font-mono text-foreground">{String(v)}</dd>
                </div>
              ))}
            </dl>
          </details>
        </div>
      )}
    </div>
  );
}

function MetricCard({ label, m }: { label: string; m?: MetricSummary }) {
  const unmeasured = !m || m.mean === null;
  return (
    <div className="rounded-lg border border-border bg-surface-raised p-4">
      <p className="text-caption text-muted-foreground">{label}</p>
      {unmeasured ? (
        <>
          {/* Never render a number nothing computed. */}
          <p className="text-title mt-1 text-muted-foreground">not measured</p>
          <p className="text-caption mt-1 text-subtle-foreground">
            {m ? `${m.undefined} item(s) undefined` : "unavailable"}
          </p>
        </>
      ) : (
        <>
          <p className="text-title-lg tnum mt-1 font-mono text-foreground-strong">
            {m.mean!.toFixed(3)}
          </p>
          <div className="mt-2 h-[3px] overflow-hidden rounded-full bg-surface-sunken">
            <div className="h-full rounded-full bg-foreground" style={{ width: `${m.mean! * 100}%` }} />
          </div>
          <p className="text-caption tnum mt-1.5 font-mono text-subtle-foreground">
            n={m.n}
            {m.ci_low !== null && ` · CI [${m.ci_low.toFixed(2)}, ${m.ci_high!.toFixed(2)}]`}
            {m.undefined > 0 && ` · ${m.undefined} undef`}
          </p>
        </>
      )}
    </div>
  );
}

function Stat({ label, value, note }: { label: string; value: string | null; note: string }) {
  return (
    <div className="rounded-lg border border-border bg-surface-raised p-4">
      <p className="text-caption text-muted-foreground">{label}</p>
      <p className="text-title-lg tnum mt-1 font-mono text-foreground-strong">
        {value ?? "—"}
      </p>
      <p className="text-caption mt-0.5 text-subtle-foreground">{note}</p>
    </div>
  );
}

function Cell({ v }: { v: number | null }) {
  return (
    <td className="text-ui-sm tnum px-4 py-2.5 text-right font-mono text-foreground">
      {v === null ? <span className="text-subtle-foreground">&mdash;</span> : v.toFixed(2)}
    </td>
  );
}
