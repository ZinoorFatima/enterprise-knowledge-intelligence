"""Run the eval harness and print a report.

    python scripts/run_eval.py ../eval/golden/smoke.jsonl

With LLM_MODE=offline this measures the retrieval half for real and reports the
LLM-judged metrics as unmeasured. That distinction is the point: a dashboard that
prints 0.94 for something nothing computed is the failure this project argues
against.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings  # noqa: E402
from app.eval.metrics import MetricSummary  # noqa: E402
from app.eval.runner import load_dataset, run_eval  # noqa: E402
from app.generate.provider import get_provider  # noqa: E402
from app.graph.state import PipelineDeps  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))
from fakes import FakeEmbedder, FakeReranker, FakeRetriever, make_candidate  # noqa: E402


def demo_corpus():
    """Stand-in until Postgres is up; swap for PgRetriever and the real
    embed service and nothing else in this script changes."""
    return [
        make_candidate(1, document_id="doc-a", ordinal=0, page=47,
                       text="The aggregate liability shall not exceed twelve months of fees."),
        make_candidate(2, document_id="doc-a", ordinal=1, page=14,
                       text="Either party may terminate for convenience on sixty days notice."),
        make_candidate(3, document_id="doc-b", ordinal=0, page=31,
                       text="Indemnity is capped at the total contract value."),
        make_candidate(4, document_id="doc-b", ordinal=1, page=2,
                       text="This agreement is governed by the laws of Delaware."),
    ]


def fmt(m: MetricSummary) -> str:
    if m.mean is None:
        return f"{'not measured':>14}  (n=0, undefined={m.undefined})"
    ci = ""
    if m.ci_low is not None:
        ci = f"  95% CI [{m.ci_low:.3f}, {m.ci_high:.3f}]"
    undef = f"  undefined={m.undefined}" if m.undefined else ""
    return f"{m.mean:>14.3f}  n={m.n}{ci}{undef}"


async def main() -> int:
    path = sys.argv[1] if len(sys.argv) > 1 else "../eval/golden/smoke.jsonl"
    items = load_dataset(path)

    deps = PipelineDeps(
        retriever=FakeRetriever(demo_corpus()),
        embedder=FakeEmbedder(),
        reranker=FakeReranker(),
        provider=get_provider(),
        org_id="demo-org",
    )

    summary = await run_eval(items, deps, embedder=FakeEmbedder())

    print()
    print("=" * 78)
    print(f"  EVAL RUN   dataset={Path(path).name}   items={summary.n_items}   mode={summary.llm_mode}")
    print("=" * 78)

    if summary.provisional:
        print("\n  [PROVISIONAL] This run's numbers are not publishable as measured:")
        for r in summary.provisional_reasons:
            print(f"    - {r}")

    print("\n  Retrieval quality (deterministic - no judge, real even offline)")
    print(f"    Context Precision   {fmt(summary.context_precision)}")
    print(f"    Context Recall      {fmt(summary.context_recall)}")
    print(f"    nDCG@10             {fmt(summary.ndcg_at_10)}")
    print(f"    MRR                 {fmt(summary.mrr)}")
    print(f"    Recall@20           {fmt(summary.recall_at_20)}")

    print("\n  Answer quality (LLM-judged)")
    print(f"    Faithfulness        {fmt(summary.faithfulness)}")
    print(f"    Answer Relevancy    {fmt(summary.answer_relevancy)}")

    print("\n  Refusal behaviour (scored separately - refusing can be correct)")
    if summary.refusal_accuracy is None:
        print("    Refusal Accuracy         no expected-refusal items in dataset")
    else:
        print(f"    Refusal Accuracy    {summary.refusal_accuracy:>14.3f}  n={summary.refusal_n}")

    print("\n  Latency")
    print(f"    p50                 {summary.p50_latency_ms:>14.1f} ms")
    print(f"    p95                 {summary.p95_latency_ms:>14.1f} ms")

    print("\n  Per item")
    for r in summary.results:
        cp = "   --" if r.context_precision is None else f"{r.context_precision:5.2f}"
        cr = "   --" if r.context_recall is None else f"{r.context_recall:5.2f}"
        flag = "REFUSED" if r.refused else ""
        exp = "(refusal expected)" if r.expected_behavior == "refuse" else ""
        err = f"  ERROR: {r.error}" if r.error else ""
        print(f"    {r.item_id}  CP={cp}  CR={cr}  {flag:<8}{exp}{err}")

    out = Path(__file__).resolve().parents[1] / "eval_last_run.json"
    out.write_text(json.dumps(summary.to_dict(), indent=2, default=str), encoding="utf-8")
    print(f"\n  Full results written to {out.name}")
    print(f"  Config snapshot: rrf_k={summary.config['rrf_k']} "
          f"rerank_top_n={summary.config['rerank_top_n']} "
          f"floors={summary.config['rerank_score_floor']}/{summary.config['rerank_relative_floor']}")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
