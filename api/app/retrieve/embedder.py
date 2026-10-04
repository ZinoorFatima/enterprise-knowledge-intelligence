"""Local embedding and reranking.

Both models run in-process here. In the compose topology they live behind the
`embed` service instead, because bge-m3 (~2.2GB) plus bge-reranker-v2-m3
(~2.2GB) loaded into every uvicorn worker AND every ingestion worker is
4.4GB x N. The protocols are identical either way, so nothing upstream changes.

Loading is lazy and cached: importing this module must stay cheap, since the
API process imports it at startup but may never embed anything.
"""

from __future__ import annotations

import asyncio
import logging
import re
import threading
from typing import Sequence

from app.config import settings

log = logging.getLogger(__name__)

_lock = threading.Lock()
_embed_model = None
_rerank_model = None


def _load_embedder():
    global _embed_model
    if _embed_model is None:
        with _lock:
            if _embed_model is None:
                from sentence_transformers import SentenceTransformer

                _embed_model = SentenceTransformer(settings.embed_model)
    return _embed_model


def _load_reranker():
    global _rerank_model
    if _rerank_model is None:
        with _lock:
            if _rerank_model is None:
                from sentence_transformers import CrossEncoder

                _rerank_model = CrossEncoder(settings.rerank_model)
    return _rerank_model


class BgeEmbedder:
    """bge-m3 dense embeddings, L2-normalized so cosine == dot product."""

    name = "bge-m3"
    semantic = True

    def __init__(self, dim: int | None = None, batch_size: int = 16):
        self.dim = dim or settings.embed_dim
        self.batch_size = batch_size

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        # Encoding is CPU/GPU-bound and releases the GIL inside torch; run it off
        # the event loop so concurrent requests are not blocked.
        return await asyncio.to_thread(self._encode, list(texts))

    def _encode(self, texts: list[str]) -> list[list[float]]:
        model = _load_embedder()
        vecs = model.encode(
            texts,
            batch_size=self.batch_size,
            normalize_embeddings=True,
            show_progress_bar=False,
            convert_to_numpy=True,
        )
        got = vecs.shape[1]
        if got != self.dim:
            raise ValueError(
                f"{settings.embed_model} produced {got}-dim vectors but the "
                f"chunks.embedding column is halfvec({self.dim}). Change "
                f"EMBED_DIM and re-create the column, or use a matching model."
            )
        return [v.tolist() for v in vecs]


class BgeReranker:
    """bge-reranker-v2-m3 cross-encoder.

    Scores (query, passage) pairs jointly, which is why it beats bi-encoder
    similarity for final ordering -- and why it cannot be precomputed.
    """

    name = "bge-reranker-v2-m3"

    def __init__(self, batch_size: int = 16, max_length: int = 512):
        self.batch_size = batch_size
        self.max_length = max_length

    async def rerank(self, query: str, passages: Sequence[str]) -> list[float]:
        if not passages:
            return []
        return await asyncio.to_thread(self._score, query, list(passages))

    def _score(self, query: str, passages: list[str]) -> list[float]:
        model = _load_reranker()
        pairs = [(query, p) for p in passages]
        scores = model.predict(
            pairs,
            batch_size=self.batch_size,
            show_progress_bar=False,
            apply_softmax=False,
        )
        # Logits -> (0,1) so the absolute score floor in config is meaningful.
        import math

        return [1.0 / (1.0 + math.exp(-float(s))) for s in scores]


# Interrogatives and filler that appear in almost any question. Left in, they
# inflate the denominator and let a single incidental content-word match look
# like partial relevance.
_QUERY_STOPWORDS = frozenset(
    """
    what when where which whose does did done have has had been were was will
    would should could shall must many much more most some such that this these
    those them they their there here into over under than then about also just
    only very with from your ours able need needs required require any each
    """.split()
)


class LexicalReranker:
    """Query-term overlap, computed in process. No model, no download.

    Markedly weaker than a cross-encoder -- it cannot score paraphrase, so a
    passage that answers the question in different words scores low. It earns
    its place because it is sub-millisecond and never blocks: a reranker that
    makes a query hang for minutes is not a usable reranker.

    Scoring contract, and it matters more than the ranking: a passage with NO
    lexical evidence must score BELOW settings.rerank_score_floor, not at it.
    An earlier version returned `0.25 + 0.75 * overlap`, so zero overlap scored
    exactly 0.25 -- precisely the configured floor, which is compared with >=.
    Every irrelevant passage therefore survived, unanswerable questions arrived
    at the model with a full context, and the system answered them instead of
    refusing. Refusal accuracy caught it at 0.000.
    """

    name = "lexical"
    semantic = False

    _WORD = re.compile(r"\w+")

    def _terms(self, query: str) -> set[str]:
        return {
            w
            for w in self._WORD.findall(query.lower())
            if len(w) > 3 and w not in _QUERY_STOPWORDS
        }

    async def rerank(self, query: str, passages: Sequence[str]) -> list[float]:
        terms = self._terms(query)
        if not terms:
            # No discriminating terms to judge on. Stay neutral rather than
            # inventing an ordering, and let RRF's ranking stand.
            return [0.5] * len(passages)

        out: list[float] = []
        for p in passages:
            words = set(self._WORD.findall(p.lower()))
            matched = len(terms & words)
            if matched == 0:
                out.append(0.0)
                continue
            coverage = matched / len(terms)
            # A single incidental term match is not evidence -- "address"
            # appears in a termination clause without making it an answer about
            # someone's home address. Halve single-match passages so they fall
            # below the floor unless the query is itself a single term.
            confidence = min(1.0, matched / 2.0)
            out.append(round(min(coverage * confidence, 1.0), 6))
        return out


class NoopReranker:
    """Keeps RRF order. Scores are uniform and above the floor, so nothing is
    dropped for lack of a reranker."""

    name = "none"
    semantic = False

    async def rerank(self, query: str, passages: Sequence[str]) -> list[float]:
        return [0.6] * len(passages)


def _model_is_cached(repo_id: str) -> bool:
    """True when the weights are already on disk.

    Checked explicitly so a first request can never trigger a multi-gigabyte
    download inline and blow past the client's timeout.
    """
    try:
        from huggingface_hub import scan_cache_dir

        return any(r.repo_id == repo_id for r in scan_cache_dir().repos)
    except Exception:
        return False


def get_reranker():
    """Pick a reranker from config, degrading loudly rather than hanging."""
    backend = settings.rerank_backend
    if backend == "none":
        return NoopReranker()
    if backend == "cross-encoder":
        if not _model_is_cached(settings.rerank_model):
            log.warning(
                "rerank_backend=cross-encoder but %s is not cached; falling back to "
                "lexical so the request does not block on a multi-GB download. "
                "Pre-fetch the model, then restart.",
                settings.rerank_model,
            )
            return LexicalReranker()
        return BgeReranker()
    return LexicalReranker()


def get_embedder():
    """Real model when available, deterministic placeholder otherwise.

    The fallback keeps the pipeline runnable before the ~2.2GB download
    finishes, but it carries NO semantic meaning -- anything measured with it
    must be labelled accordingly.
    """
    try:
        import sentence_transformers  # noqa: F401

        return BgeEmbedder()
    except ImportError:
        from app.ingest.pipeline import HashEmbedder

        return HashEmbedder(settings.embed_dim)
