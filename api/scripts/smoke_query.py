"""End-to-end smoke: real Postgres -> LangGraph pipeline -> answer + citations.

    python scripts/smoke_query.py "What is the liability cap?"

Exercises the production path: PgRetriever against the seeded corpus, the real
graph with its routing, and the configured LLM provider.
"""

from __future__ import annotations

import asyncio
import sys

# Windows consoles default to cp1252 and will raise on any non-ASCII output.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import asyncpg  # noqa: E402

from app.config import settings  # noqa: E402
from app.db.session import PgRetriever  # noqa: E402
from app.generate.provider import get_provider  # noqa: E402
from app.graph.pipeline import run_pipeline  # noqa: E402
from app.graph.state import PipelineDeps  # noqa: E402
from app.ingest.pipeline import HashEmbedder  # noqa: E402


class LocalReranker:
    """Placeholder cross-encoder.

    Scores by lexical overlap with the query. Enough to exercise reranking and
    the score floors; NOT a semantic reranker, so its ordering should not be
    read as a quality signal.
    """

    async def rerank(self, query: str, passages):
        import re

        q = {w for w in re.findall(r"\w+", query.lower()) if len(w) > 3}
        out = []
        for p in passages:
            words = set(re.findall(r"\w+", p.lower()))
            overlap = len(q & words) / max(len(q), 1)
            out.append(min(0.3 + overlap, 1.0))
        return out


async def main() -> int:
    question = sys.argv[1] if len(sys.argv) > 1 else "What is the aggregate liability cap?"

    pool = await asyncpg.create_pool(settings.asyncpg_dsn, min_size=1, max_size=4)
    try:
        async with pool.acquire() as c:
            org = await c.fetchval(
                "SELECT org_id FROM rag.chunks GROUP BY org_id ORDER BY count(*) DESC LIMIT 1"
            )
            n = await c.fetchval("SELECT count(*) FROM rag.chunks")
        if not org:
            print("Empty corpus. Run: python scripts/seed_dev.py --bulk 90")
            return 1

        deps = PipelineDeps(
            retriever=PgRetriever(pool),
            embedder=HashEmbedder(settings.embed_dim),
            reranker=LocalReranker(),
            provider=get_provider(),
            org_id=str(org),
        )

        print(f"\ncorpus: {n} chunks   mode: {settings.llm_mode}")
        print(f"question: {question}\n" + "=" * 78)

        out = await run_pipeline(question=question, deps=deps)

        print("\nRETRIEVAL")
        for lane, size in out.lane_sizes.items():
            print(f"  lane {lane:<12} {size:>4} candidates")
        print(f"  fused        {len(out.fused):>4}")
        print(f"  reranked     {len(out.reranked):>4} (into context)")

        if out.reranked:
            print("\nTOP EVIDENCE")
            for i, c in enumerate(out.reranked[:5], start=1):
                lanes = []
                if c.bm25_rank is not None:
                    lanes.append(f"L#{c.bm25_rank}")
                else:
                    lanes.append("L  -")
                if c.vec_rank is not None:
                    lanes.append(f"S#{c.vec_rank}")
                else:
                    lanes.append("S  -")
                delta = "" if c.rerank_delta is None else (
                    f" +{c.rerank_delta}" if c.rerank_delta > 0
                    else (f" -{abs(c.rerank_delta)}" if c.rerank_delta < 0 else "  =")
                )
                print(
                    f"  {i}. {c.title[:34]:<34} p.{c.page_start:<3} "
                    f"[{' '.join(lanes):<12}] rrf={c.rrf_score:.4f} "
                    f"rr={c.rerank_score:.3f}{delta}"
                )
                print(f"     {' '.join(c.text[:96].split())}...")

        print("\nANSWER")
        print("  " + (out.answer_text[:400].replace("\n", "\n  ") or "(empty)"))
        print(f"\n  refused: {out.refused}   citations: {len(out.citations)}")

        if out.citations:
            print("\nCITATIONS")
            for c in out.citations[:4]:
                ev = out.evidence[c.evidence_index]
                print(f"  [{c.evidence_index + 1}] {ev.title} p.{ev.page_start}")
                print(f"      \"{c.cited_text[:84].strip()}...\"")

        if out.verification:
            v = out.verification
            print("\nVERIFICATION")
            if v.unavailable_reason:
                print(f"  not checked: {v.unavailable_reason}")
            else:
                print(f"  faithfulness {v.faithfulness:.3f} over {len(v.factual_claims)} claims")

        print("\nLATENCY (ms)")
        for stage, ms in out.stage_latency_ms.items():
            print(f"  {stage:<10} {ms:>8.1f}")
        if out.errors:
            print("\nERRORS")
            for e in out.errors:
                print(f"  - {e}")
        print()
        return 0
    finally:
        await pool.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
