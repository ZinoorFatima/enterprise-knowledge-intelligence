"""Compare generation backends on the same golden set, same corpus, same retrieval.

    python scripts/compare_providers.py --a anthropic --b gemini

Only the generation backend differs between runs. Retrieval, chunking, ranking
and the judging model are identical, so a difference in the answer metrics is
attributable to the generator rather than to the pipeline around it.

Reported with a PAIRED BOOTSTRAP confidence interval on the delta. A delta whose
interval crosses zero is reported as "no significant change" -- without that,
every comparison looks like a win and you end up tuning noise. On a dataset this
small almost nothing will be significant, and saying so is the point.

Two caveats the output repeats, because they affect how the numbers should be read:

  * Faithfulness is judged by a third model, which is deliberately neither of the
    two being compared. Judging a model with itself inflates its score.
  * Citation support is not like-for-like. Anthropic returns span-level citations
    from the API; the Gemini path recovers block-level citations from markers the
    model emits. The citation columns are therefore not directly comparable, and
    the report labels them.
"""

from __future__ import annotations

import argparse
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
from app.eval.metrics import compare  # noqa: E402
from app.eval.runner import RunSummary, load_dataset, run_eval  # noqa: E402
from app.generate.provider import get_provider  # noqa: E402
from app.graph.state import PipelineDeps  # noqa: E402

METRICS = [
    ("context_precision", "Context Precision", "retrieval"),
    ("context_recall", "Context Recall", "retrieval"),
    ("faithfulness", "Faithfulness", "generation"),
    ("answer_relevancy", "Answer Relevancy", "generation"),
]


def _per_item(summary: RunSummary, field: str) -> list[float | None]:
    return [getattr(r, field) for r in summary.results if r.expected_behavior == "answer"]


async def _run(provider_name: str, items, pool, org: str) -> RunSummary:
    from app.retrieve.embedder import get_embedder, get_reranker

    embedder = get_embedder()
    deps = PipelineDeps(
        retriever=PgRetriever(pool),
        embedder=embedder,
        reranker=get_reranker(),
        provider=get_provider(provider_name),
        org_id=org,
    )
    return await run_eval(items, deps, embedder=embedder)


def _missing_key(provider: str) -> str | None:
    if provider == "anthropic" and not settings.anthropic_api_key:
        return "ANTHROPIC_API_KEY"
    if provider == "gemini" and not settings.gemini_api_key:
        return "GEMINI_API_KEY"
    return None


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--a", default="anthropic", help="baseline provider")
    ap.add_argument("--b", default="gemini", help="candidate provider")
    ap.add_argument("--dataset", default=None)
    args = ap.parse_args()

    # Fail before spending anything, naming exactly what is missing and where.
    missing = [(p, k) for p in (args.a, args.b) if (k := _missing_key(p))]
    if missing:
        print("Cannot run the comparison - missing credentials:\n")
        for provider, key in missing:
            print(f"  {provider:<10} needs {key}")
        print(
            "\nAdd them to the .env file at the repository root:\n"
            "    ANTHROPIC_API_KEY=sk-ant-...\n"
            "    GEMINI_API_KEY=...\n\n"
            ".env is gitignored and never committed. .env.example documents both."
        )
        return 2

    dataset = args.dataset or str(
        Path(__file__).resolve().parents[2] / "eval" / "golden" / "smoke.jsonl"
    )
    items = load_dataset(dataset)

    pool = await asyncpg.create_pool(settings.asyncpg_dsn, min_size=1, max_size=4)
    try:
        async with pool.acquire() as c:
            org = await c.fetchval(
                "SELECT org_id FROM rag.chunks WHERE embedding IS NOT NULL "
                "GROUP BY org_id ORDER BY count(*) DESC LIMIT 1"
            )
        if not org:
            print("No semantically indexed chunks. Run seed_dev.py then reembed.py.")
            return 1
        org = str(org)

        print(f"Running {args.a} ...")
        a = await _run(args.a, items, pool, org)
        print(f"Running {args.b} ...")
        b = await _run(args.b, items, pool, org)

        print()
        print("=" * 82)
        print(f"  {args.a}  vs  {args.b}      dataset={Path(dataset).name}  items={a.n_items}")
        print("=" * 82)
        print(
            "\n  Retrieval is identical in both runs by construction; only the generation\n"
            "  backend differs. Retrieval rows are shown as a control -- if they move,\n"
            "  something other than the generator changed and the comparison is invalid.\n"
        )
        print(f"  {'Metric':<22}{args.a:>12}{args.b:>12}{'delta':>10}   95% CI          verdict")
        print("  " + "-" * 78)

        control_moved = False
        for field, label, kind in METRICS:
            res = compare(field, _per_item(a, field), _per_item(b, field))
            # Each run's OWN aggregate, not the paired subset. A delta needs
            # items both runs scored; a per-run mean does not, and reporting
            # only the paired view hides a number that was genuinely measured
            # (e.g. one provider can judge faithfulness and the other cannot).
            ma, mb = a.metrics_by_name(field), b.metrics_by_name(field)
            av = "       --" if ma is None or ma.mean is None else f"{ma.mean:9.3f}"
            bv = "       --" if mb is None or mb.mean is None else f"{mb.mean:9.3f}"
            na = 0 if ma is None else ma.n
            nb = 0 if mb is None else mb.n
            if res.delta is None:
                why = (
                    "not measured by either run"
                    if na == 0 and nb == 0
                    else f"no paired items (n={na} vs {nb}) - delta not computable"
                )
                print(f"  {label:<22}{av:>12}{bv:>12}{'--':>10}   {why}")
                continue
            ci = f"[{res.ci_low:+.3f}, {res.ci_high:+.3f}]"
            verdict = "significant" if res.significant else "no significant change"
            if kind == "retrieval" and res.significant:
                control_moved = True
                verdict += "  <-- CONTROL MOVED"
            print(f"  {label:<22}{av:>12}{bv:>12}{res.delta:+10.3f}   {ci:<16}{verdict}")

        print("\n  Refusal behaviour (scored separately - refusing can be correct)")
        for name, s in ((args.a, a), (args.b, b)):
            val = "--" if s.refusal_accuracy is None else f"{s.refusal_accuracy:.3f}"
            print(f"    {name:<12} {val:>8}  over {s.refusal_n} unanswerable items")

        print("\n  Latency / cost")
        for name, s in ((args.a, a), (args.b, b)):
            spent = sum(float(r.cost_usd or 0) for r in s.results)
            cost = f"${spent:.4f}" if spent else "--"
            print(
                f"    {name:<12} p50 {s.p50_latency_ms:7.0f} ms   "
                f"p95 {s.p95_latency_ms:7.0f} ms   {cost}"
            )

        print("\n  How to read this")
        print("    - A delta whose CI crosses zero is noise, not an improvement.")
        print("    - Faithfulness is judged by a third model, neither of the two compared.")
        print("    - Citation support is NOT like-for-like: Anthropic returns span-level")
        print("      citations from the API; the Gemini path recovers block-level citations")
        print("      from markers the model emits. Do not read them as equivalent.")
        if control_moved:
            print("\n    WARNING: a retrieval control metric moved significantly. Something")
            print("    other than the generator differed between runs; treat the generation")
            print("    rows as unreliable until that is explained.")
        for s, n in ((a, args.a), (b, args.b)):
            if s.provisional:
                print(f"\n    {n} run is PROVISIONAL:")
                for r in s.provisional_reasons:
                    print(f"      - {r}")

        out = Path(__file__).resolve().parents[1] / "provider_comparison.json"
        out.write_text(
            json.dumps({args.a: a.to_dict(), args.b: b.to_dict()}, indent=2, default=str),
            encoding="utf-8",
        )
        print(f"\n  Full results written to {out.name}\n")
        return 0
    finally:
        await pool.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
