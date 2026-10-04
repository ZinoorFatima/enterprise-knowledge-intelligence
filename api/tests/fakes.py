"""Fakes implementing the Retriever/Embedder/Reranker protocols.

These exist so the entire pipeline is testable with no Postgres, no model
weights, and no API key -- which is also exactly why those seams were defined
as protocols in the first place.
"""

from __future__ import annotations

import hashlib
from typing import Sequence

from app.graph.state import Filters
from app.retrieve.fuse import Candidate


def make_candidate(
    chunk_id: int,
    *,
    document_id: str = "doc-1",
    ordinal: int = 0,
    text: str = "",
    page: int = 1,
    token_count: int = 50,
    bm25_rank: int | None = None,
    vec_rank: int | None = None,
) -> Candidate:
    return Candidate(
        chunk_id=chunk_id,
        document_id=document_id,
        title=f"{document_id}.pdf",
        text=text or f"Chunk {chunk_id} content about contractual obligations.",
        token_count=token_count,
        ordinal=ordinal,
        page_start=page,
        page_end=page,
        char_start=chunk_id * 1000,
        char_end=chunk_id * 1000 + 400,
        bm25_rank=bm25_rank,
        vec_rank=vec_rank,
    )


class FakeRetriever:
    """Returns a fixed corpus, honouring lane weights and metadata filters.

    It applies filters the same way the real SQL does -- inside each lane -- so a
    test can catch a regression where filtering moved after fusion.
    """

    def __init__(self, corpus: Sequence[Candidate] | None = None):
        self.corpus = list(corpus or [make_candidate(i, ordinal=i) for i in range(1, 11)])
        self.calls: list[dict] = []

    async def search(
        self, *, org_id, query_text, query_embedding, filters: Filters,
        w_bm25, w_vec, lane_k, out_n,
    ) -> list[Candidate]:
        self.calls.append(
            {"query_text": query_text, "has_vector": query_embedding is not None,
             "w_bm25": w_bm25, "w_vec": w_vec}
        )
        if w_bm25 <= 0 and w_vec <= 0:
            return []

        pool = [c for c in self.corpus]
        if filters.document_ids:
            pool = [c for c in pool if c.document_id in filters.document_ids]
        if filters.tags:
            pool = [c for c in pool if set(filters.tags) & set(c.tags)]

        out = []
        for rank, c in enumerate(pool[:out_n], start=1):
            copy = make_candidate(
                c.chunk_id, document_id=c.document_id, ordinal=c.ordinal,
                text=c.text, page=c.page_start, token_count=c.token_count,
                bm25_rank=rank if w_bm25 > 0 else None,
                vec_rank=rank if w_vec > 0 else None,
            )
            out.append(copy)
        return out


class EmptyRetriever:
    async def search(self, **kwargs) -> list[Candidate]:
        return []


class FakeEmbedder:
    """Deterministic pseudo-embeddings. Stable across runs so tests do not flake."""

    def __init__(self, dim: int = 16):
        self.dim = dim
        self.calls: list[list[str]] = []

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        out = []
        for t in texts:
            h = hashlib.sha256(t.encode("utf-8")).digest()
            vec = [(h[i % len(h)] / 255.0) - 0.5 for i in range(self.dim)]
            norm = sum(v * v for v in vec) ** 0.5 or 1.0
            out.append([v / norm for v in vec])
        return out


class FakeReranker:
    """Scores by position unless given an explicit score map."""

    def __init__(self, scores: dict[str, float] | None = None, default: float = 0.9):
        self.scores = scores or {}
        self.default = default
        self.batches: list[int] = []

    async def rerank(self, query: str, passages: Sequence[str]) -> list[float]:
        self.batches.append(len(passages))
        return [self.scores.get(p, self.default) for p in passages]


class LowScoreReranker:
    """Everything below the floors -- the unanswerable-question case."""

    async def rerank(self, query: str, passages: Sequence[str]) -> list[float]:
        return [0.05] * len(passages)


class FailingReranker:
    async def rerank(self, query: str, passages: Sequence[str]) -> list[float]:
        raise RuntimeError("cross-encoder unavailable")
