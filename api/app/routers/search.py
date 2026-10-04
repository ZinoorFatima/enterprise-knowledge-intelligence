"""Retrieval-only endpoint.

Exists separately from /ask on purpose: it is the debug surface. With
explain=true it returns the query plan and per-lane results, which is how you
diagnose "why didn't it find that document" without reading server logs.
"""

from __future__ import annotations

import time

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from app.config import settings
from app.core.security import Principal, PrincipalDep
from app.db.session import PgRetriever
from app.graph.state import Filters
from app.retrieve.fuse import fuse_lanes

router = APIRouter(tags=["search"])


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=4000)
    filters: Filters = Field(default_factory=Filters)
    top_k: int = Field(default=20, ge=1, le=100)
    mode: str = Field(default="hybrid", pattern="^(hybrid|vector|bm25)$")
    explain: bool = False


@router.post("/search")
async def search(req: SearchRequest, request: Request, principal: Principal = PrincipalDep) -> dict:
    from app.retrieve.embedder import get_embedder

    t0 = time.perf_counter()
    embedder = request.app.state.embedder or get_embedder()
    request.app.state.embedder = embedder

    w_bm25 = settings.w_bm25 if req.mode in ("hybrid", "bm25") else 0.0
    w_vec = settings.w_vec if req.mode in ("hybrid", "vector") else 0.0

    vec = None
    if w_vec > 0:
        vec = (await embedder.embed([req.query]))[0]

    retriever = PgRetriever(request.app.state.pool)
    candidates = await retriever.search(
        org_id=principal.org_id,
        query_text=req.query if w_bm25 > 0 else "",
        query_embedding=vec,
        filters=req.filters,
        w_bm25=w_bm25,
        w_vec=w_vec,
        lane_k=settings.lane_k,
        out_n=req.top_k,
    )
    fused = fuse_lanes({"original": candidates}, {"original": 1.0}, k=settings.rrf_k, out_n=req.top_k)

    return {
        "results": [
            {
                "chunk_id": c.chunk_id,
                "document_id": c.document_id,
                "title": c.title,
                "page_start": c.page_start,
                "page_end": c.page_end,
                "text": c.text,
                "section_path": c.section_path,
                "scores": {
                    "rrf": c.rrf_score,
                    "bm25_rank": c.bm25_rank,
                    "vec_rank": c.vec_rank,
                    "bm25": c.bm25_score,
                    "cosine": c.cos_sim,
                },
            }
            for c in fused
        ],
        "timings_ms": {"total": round((time.perf_counter() - t0) * 1000, 1)},
        "embedder": getattr(embedder, "name", "unknown"),
        # Honest flag: a non-semantic embedder makes the vector lane noise.
        "semantic_embeddings": bool(getattr(embedder, "semantic", False)),
    }
