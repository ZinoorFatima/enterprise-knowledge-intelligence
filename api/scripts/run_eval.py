"""Run the eval harness against the REAL stack and print a report.

    python scripts/run_eval.py ../eval/golden/smoke.jsonl

Uses PgRetriever, the configured embedder and the configured reranker -- the
same components the API serves with. An earlier version of this script wired
fake in-memory stand-ins, which made it report zeros against a golden set
labelled with real document ids: the script measured something nobody ships.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import asyncpg  # noqa: E402

from app.config import settings  # noqa: E402
from app.db.session import PgRetriever  # noqa: E402
from app.eval.metrics import MetricSummary  # noqa: E402
from app.eval.runner import load_dataset, run_eval  # noqa: E402
from app.generate.provider import get_provider  # noqa: E402
from app.graph.state import PipelineDeps  # noqa: E402


def fmt(m: MetricSummary) -> str:
    if m.mean is None:
        return f"{'not measured':>14}  (n=0, undefined={m.undefined})"
    ci = "" if m.ci_low is None else f"  95% CI [{m.ci_low:.3f}, {m.ci_high:.3f}]"
    undef = f"  undefined={m.undefined}" if m.undefined else ""
    return f"{m.mean:>14.3f}  n={m.n}{ci}{undef}"


async def main() -> int:
    path = sys.argv[1] if len(sys.argv) > 1 else "../eval/golden/smoke.jsonl"
    items = load_dataset(path)

    from app.retrieve.embedder import get_embedder, get_reranker

    pool = await asyncpg.create_pool(settings.asyncpg_dsn, min_size=1, max_size=4)
    try:
        embedder = get_embedder()
        deps = PipelineDeps(
            retriever=PgRetriever(pool),
            embedder=embedder,
            reranker=get_reranker(),
            provider=get_provider(),
            org_id="",  # resolved below from the corpus that owns the chunks
        )
        async with pool.acquire() as c:
            org = await c.fetchval(
                "SELECT org_id FROM rag.chunks WHERE embedding IS NOT NULL "
                "GROUP BY org_id ORDER BY count(*) DESC LIMIT 1"
            )
        if not org:
            print("No semantically indexed chunks. Run seed_dev.py then reembed.py.")
            return 1
        deps.org_id = str(org)

        summary = await run_eval(items, deps, embedder=embedder)

        print()
        print("=" * 78)
        print(
            f"  EVAL RUN  dataset={Path(path).name}  items={summary.n_items}  "
            f"mode={summary.llm_mode}  reranker={getattr(deps.reranker, 'name', '?')}"
        )
        print("=" * 78)

        if summary.provisional:
            print("\n  [PROVISIONAL] Not publishable as measured:")
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
            print(
                f"    Refusal Accuracy    {summary.refusal_accuracy:>14.3f}  n={summary.refusal_n}"
            )

        print("\n  Latency")
        print(f"    p50                 {summary.p50_latency_ms:>14.1f} ms")
        print(f"    p95                 {summary.p95_latency_ms:>14.1f} ms")

        print("\n  Per item")
        for r in summary.results:
            cp = "   --" if r.context_precision is None else f"{r.context_precision:5.2f}"
            cr = "   --" if r.context_recall is None else f"{r.context_recall:5.2f}"
            flag = "REFUSED" if r.refused else "answered"
            exp = " (refusal expected)" if r.expected_behavior == "refuse" else ""
            ok = ""
            if r.expected_behavior == "refuse":
                ok = "  OK" if r.refused else "  <-- should have refused"
            err = f"  ERROR: {r.error}" if r.error else ""
            print(f"    {r.item_id}  CP={cp}  CR={cr}  {flag:<9}{exp}{ok}{err}")

        out = Path(__file__).resolve().parents[1] / "eval_last_run.json"
        out.write_text(json.dumps(summary.to_dict(), indent=2, default=str), encoding="utf-8")
        print(f"\n  Full results written to {out.name}")
        print(
            f"  Config: rrf_k={summary.config['rrf_k']} "
            f"rerank_top_n={summary.config['rerank_top_n']} "
            f"backend={summary.config.get('rerank_backend')} "
            f"floors={summary.config['rerank_score_floor']}/"
            f"{summary.config['rerank_relative_floor']}"
        )
        print()
        return 0
    finally:
        await pool.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
