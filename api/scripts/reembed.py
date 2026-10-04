"""Re-embed every chunk with the configured model.

    python scripts/reembed.py [--batch 32]

Needed whenever the embedding model changes -- including the move off the
HashEmbedder placeholder. Embeddings from different models are not comparable,
so a corpus must be embedded by exactly one model or the vector lane silently
ranks against a mixture.

Chunks are embedded with the same title/section prefix the ingestion pipeline
uses, because the stored vector must correspond to what was indexed, not to the
bare chunk text.
"""

from __future__ import annotations

import asyncio
import sys
import time
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


async def main() -> int:
    batch = 32
    if "--batch" in sys.argv:
        batch = int(sys.argv[sys.argv.index("--batch") + 1])
    # bge-m3 is 568M params and this box is CPU-only (~0.1 chunk/s), so
    # re-embedding a whole bulk corpus is impractical. --exclude-tag lets you
    # re-embed only the documents whose retrieval quality actually matters.
    exclude_tag = None
    if "--exclude-tag" in sys.argv:
        exclude_tag = sys.argv[sys.argv.index("--exclude-tag") + 1]

    embedder = BgeEmbedder(batch_size=batch)
    conn = await asyncpg.connect(settings.asyncpg_dsn, timeout=15)
    try:
        rows = await conn.fetch(
            """
            SELECT c.id, c.text, c.section_path, d.title
            FROM rag.chunks c
            JOIN rag.documents d ON d.id = c.document_id
            WHERE ($1::text IS NULL OR NOT (c.tags && ARRAY[$1::text]))
            ORDER BY c.id
            """,
            exclude_tag,
        )
        total = len(rows)
        if not total:
            print("No chunks to embed.")
            return 0

        print(f"Re-embedding {total} chunks with {settings.embed_model} (batch={batch})")
        print("First call loads ~2.2GB of weights; subsequent batches are fast.\n")

        t0 = time.perf_counter()
        done = 0
        for i in range(0, total, batch):
            window = rows[i : i + batch]
            # Same prefix as ingestion: the vector must match what was indexed.
            texts = [
                f"{r['title']} | {r['section_path']}\n{r['text']}"
                if r["section_path"]
                else f"{r['title']}\n{r['text']}"
                for r in window
            ]
            vectors = await embedder.embed(texts)
            await conn.executemany(
                "UPDATE rag.chunks SET embedding = $2::halfvec WHERE id = $1",
                [(r["id"], to_vector_literal(v)) for r, v in zip(window, vectors)],
            )
            done += len(window)
            elapsed = time.perf_counter() - t0
            rate = done / elapsed if elapsed else 0
            eta = (total - done) / rate if rate else 0
            # One line per batch, flushed. A \r progress line is invisible when
            # stdout is redirected to a file, which makes a long run look hung.
            print(
                f"  {done:>5}/{total}  {done / total * 100:5.1f}%  "
                f"{rate:5.1f} chunk/s  eta {eta:5.0f}s",
                flush=True,
            )

        print()
        # The HNSW graph is maintained on write, but statistics are not.
        await conn.execute("ANALYZE rag.chunks")
        missing = await conn.fetchval("SELECT count(*) FROM rag.chunks WHERE embedding IS NULL")
        print(f"\nDone in {time.perf_counter() - t0:.1f}s. Chunks without an embedding: {missing}")
        return 0 if missing == 0 else 1
    finally:
        await conn.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
