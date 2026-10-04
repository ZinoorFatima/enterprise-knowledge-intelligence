"""asyncpg connection pool and the hybrid-retrieval query.

Raw asyncpg rather than an ORM for the search path: the hybrid query is the
performance centre of the system and its exact plan matters, so it lives in a
.sql file that can be EXPLAINed verbatim in CI.
"""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

import asyncpg

from app.config import settings
from app.graph.state import Filters
from app.retrieve.fuse import Candidate

_SQL_DIR = Path(__file__).parent / "sql"
HYBRID_SQL = (_SQL_DIR / "hybrid.sql").read_text(encoding="utf-8")

_pool: asyncpg.Pool | None = None


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(
            settings.asyncpg_dsn,
            min_size=settings.db_pool_min,
            max_size=settings.db_pool_max,
            command_timeout=30,
        )
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


def _to_vector_literal(embedding: Sequence[float] | None) -> str | None:
    """pgvector accepts its text input form; asyncpg has no native codec for it."""
    if embedding is None:
        return None
    return "[" + ",".join(f"{float(x):.6f}" for x in embedding) + "]"


class PgRetriever:
    """Executes the hybrid query. Implements the Retriever protocol, so the
    graph and the eval harness are unchanged by swapping this in for a fake."""

    def __init__(self, pool: asyncpg.Pool | None = None):
        self._pool = pool

    async def _acquire(self):
        pool = self._pool or await get_pool()
        return pool

    async def search(
        self,
        *,
        org_id: str,
        query_text: str,
        query_embedding: Sequence[float] | None,
        filters: Filters,
        w_bm25: float,
        w_vec: float,
        lane_k: int,
        out_n: int,
    ) -> list[Candidate]:
        if w_bm25 <= 0 and w_vec <= 0:
            return []

        pool = await self._acquire()
        async with pool.acquire() as conn, conn.transaction():
            # Session GUCs for the vector lane. iterative_scan is 'relaxed_order'
            # rather than 'strict_order' because RRF consumes ranks only -- paying
            # for a strict global ordering would buy nothing downstream.
            # SET does not accept bind parameters, so these are interpolated.
            # Both values are ints coerced from typed config, never user input.
            ef = int(settings.hnsw_ef_search)
            max_scan = int(settings.hnsw_max_scan_tuples)
            try:
                # vector.so must be loaded before its GUCs are registered in
                # this backend; a pooled connection may be serving its first
                # vector query.
                await conn.execute("LOAD 'vector'")
                await conn.execute(f"SET LOCAL hnsw.ef_search = {ef}")
                await conn.execute("SET LOCAL hnsw.iterative_scan = 'relaxed_order'")
                await conn.execute(f"SET LOCAL hnsw.max_scan_tuples = {max_scan}")
            except asyncpg.PostgresError:
                # GUC only exists once vector.so is loaded in this backend; a
                # fresh connection may not have it yet. Harmless to skip.
                pass

            rows = await conn.fetch(
                HYBRID_SQL,
                org_id,
                query_text or "",
                _to_vector_literal(query_embedding),
                filters.document_ids or None,
                filters.date_from,
                filters.date_to,
                filters.tags or None,
                lane_k,
                settings.rrf_k,
                out_n,
                float(w_bm25),
                float(w_vec),
            )

        return [
            Candidate(
                chunk_id=r["chunk_id"],
                document_id=str(r["document_id"]),
                title=r["title"],
                text=r["text"],
                token_count=r["token_count"],
                ordinal=r["ordinal"],
                page_start=r["page_start"],
                page_end=r["page_end"],
                char_start=r["char_start"],
                char_end=r["char_end"],
                section_path=r["section_path"],
                kind=str(r["kind"]),
                bm25_rank=r["bm25_rank"],
                vec_rank=r["vec_rank"],
                bm25_score=r["bm25_score"],
                cos_sim=r["cos_sim"],
                rrf_score=float(r["rrf_score"]),
            )
            for r in rows
        ]
