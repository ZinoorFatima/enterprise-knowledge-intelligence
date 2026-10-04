"""Diagnose the vector lane directly, bypassing the API and the graph.

Embeds a query, runs a plain nearest-neighbour query, and prints the true
ordering. If this looks right but /v1/search does not, the fault is upstream of
the SQL.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import asyncpg  # noqa: E402

from app.config import settings  # noqa: E402
from app.ingest.pipeline import to_vector_literal  # noqa: E402
from app.retrieve.embedder import BgeEmbedder  # noqa: E402

QUERY = sys.argv[1] if len(sys.argv) > 1 else "which state court decides disputes"


async def main() -> int:
    conn = await asyncpg.connect(settings.asyncpg_dsn, timeout=15)
    try:
        vec = (await BgeEmbedder(batch_size=1).embed([QUERY]))[0]
        lit = to_vector_literal(vec)

        print(f"query: {QUERY!r}")
        print(f"query vector: dim={len(vec)} norm={sum(x * x for x in vec) ** 0.5:.4f}\n")

        rows = await conn.fetch(
            """
            SELECT c.id, d.title, c.page_start,
                   c.embedding <=> $1::halfvec AS dist,
                   1 - (c.embedding <=> $1::halfvec) AS cos_sim,
                   left(c.text, 90) AS snippet
            FROM rag.chunks c
            JOIN rag.documents d ON d.id = c.document_id
            WHERE NOT (c.tags && ARRAY['filler'])
            ORDER BY c.embedding <=> $1::halfvec
            LIMIT 5
            """,
            lit,
        )
        print("TRUE nearest neighbours (plain ORDER BY distance):")
        for i, r in enumerate(rows, 1):
            print(f"  {i}. cos={r['cos_sim']:.4f} dist={r['dist']:.4f}  {r['title'][:28]:<28} p.{r['page_start']}")
            print(f"     {' '.join(r['snippet'].split())}")

        # Are the stored vectors actually distinct, or did they collapse?
        stats = await conn.fetchrow(
            """
            SELECT count(*) AS n, count(DISTINCT c.embedding::text) AS distinct_vecs
            FROM rag.chunks c WHERE NOT (c.tags && ARRAY['filler'])
            """
        )
        print(f"\nstored vectors: {stats['n']} chunks, {stats['distinct_vecs']} distinct")

        spread = await conn.fetch(
            """
            SELECT 1 - (c.embedding <=> $1::halfvec) AS cos
            FROM rag.chunks c WHERE NOT (c.tags && ARRAY['filler'])
            """,
            lit,
        )
        vals = sorted(float(r["cos"]) for r in spread)
        print(f"cosine spread: min={vals[0]:.4f} max={vals[-1]:.4f} range={vals[-1] - vals[0]:.4f}")
        if vals[-1] - vals[0] < 0.15:
            print(
                "  -> Vectors are nearly equidistant from the query. Either the "
                "corpus text is too homogeneous to discriminate, or the stored "
                "embeddings do not correspond to the chunk text."
            )
        return 0
    finally:
        await conn.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
