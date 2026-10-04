"""Cross-lane Reciprocal Rank Fusion, dedup, and contiguous-run merging.

The SQL in hybrid.sql fuses the two lanes (lexical + vector) WITHIN one query
text. This module fuses across the several query texts produced by rewriting
(original, subqueries, HyDE, keywords), which is a second, separate RRF pass.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Iterable, Sequence


@dataclass
class Candidate:
    """One retrieved chunk, carrying enough lane provenance for the inspector to
    show WHY it surfaced — which is the product's whole argument."""

    chunk_id: int
    document_id: str
    title: str
    text: str
    token_count: int
    ordinal: int
    page_start: int
    page_end: int
    char_start: int
    char_end: int
    section_path: str | None = None
    kind: str = "prose"

    # Per-lane provenance. None means "this lane never retrieved it", which is
    # the most informative state in the inspector and must survive to the UI.
    bm25_rank: int | None = None
    vec_rank: int | None = None
    bm25_score: float | None = None
    cos_sim: float | None = None

    rrf_score: float = 0.0
    rerank_score: float | None = None
    rerank_delta: int | None = None
    in_context: bool = False
    # Which rewrite lanes surfaced this chunk, e.g. {"original", "hyde"}.
    lanes: set[str] = field(default_factory=set)


def rrf_contribution(rank: int, k: int = 60, weight: float = 1.0) -> float:
    """RRF gives a document weight/(k + rank).

    k controls curve steepness. Small k (~10) makes rank 1 worth ~6x rank 5, so
    the fused list is dominated by whichever lane produced the single best hit —
    good for precision@1 when there is NO reranker, brittle when one lane
    misfires. Large k (60) flattens it, promoting documents that appear decently
    in BOTH lanes: worse precision@1, materially better recall@50.

    We have a cross-encoder downstream, so recall into the reranker is the
    objective and precision@1 out of SQL is nearly worthless. Hence k=60.
    """
    if rank < 1:
        raise ValueError(f"rank is 1-indexed, got {rank}")
    return weight / (k + rank)


def fuse_lanes(
    lane_results: dict[str, Sequence[Candidate]],
    lane_weights: dict[str, float],
    *,
    k: int = 60,
    out_n: int = 60,
) -> list[Candidate]:
    """Fuse per-lane ranked lists into one, keyed by chunk_id.

    Dedup is exact on chunk_id: one sentence of overlap between chunks means
    near-duplicate detection is unnecessary.
    """
    merged: dict[int, Candidate] = {}
    scores: dict[int, float] = {}

    for lane_name, results in lane_results.items():
        weight = lane_weights.get(lane_name, 1.0)
        if weight <= 0:
            continue
        for rank, cand in enumerate(results, start=1):
            scores[cand.chunk_id] = scores.get(cand.chunk_id, 0.0) + rrf_contribution(
                rank, k=k, weight=weight
            )
            existing = merged.get(cand.chunk_id)
            if existing is None:
                merged[cand.chunk_id] = replace(cand, lanes={lane_name})
            else:
                existing.lanes.add(lane_name)
                # Keep the best rank seen in each lane across all query variants,
                # so the inspector reports the chunk's strongest evidence.
                existing.bm25_rank = _min_opt(existing.bm25_rank, cand.bm25_rank)
                existing.vec_rank = _min_opt(existing.vec_rank, cand.vec_rank)
                existing.bm25_score = _max_opt(existing.bm25_score, cand.bm25_score)
                existing.cos_sim = _max_opt(existing.cos_sim, cand.cos_sim)

    for cid, score in scores.items():
        merged[cid].rrf_score = score

    ordered = sorted(merged.values(), key=lambda c: (-c.rrf_score, c.chunk_id))
    return ordered[:out_n]


def _min_opt(a: int | None, b: int | None) -> int | None:
    return b if a is None else (a if b is None else min(a, b))


def _max_opt(a: float | None, b: float | None) -> float | None:
    return b if a is None else (a if b is None else max(a, b))


def merge_contiguous_runs(candidates: Sequence[Candidate]) -> list[list[Candidate]]:
    """Group chunks that are adjacent in the same document into runs.

    Emitting a run as one evidence unit reads better than two fragments and
    produces cleaner citation spans. Runs preserve the best RRF score in the run
    for ordering.
    """
    by_doc: dict[str, list[Candidate]] = {}
    for c in candidates:
        by_doc.setdefault(c.document_id, []).append(c)

    runs: list[list[Candidate]] = []
    for doc_chunks in by_doc.values():
        doc_chunks.sort(key=lambda c: c.ordinal)
        current: list[Candidate] = []
        for c in doc_chunks:
            if current and c.ordinal == current[-1].ordinal + 1:
                current.append(c)
            else:
                if current:
                    runs.append(current)
                current = [c]
        if current:
            runs.append(current)

    runs.sort(key=lambda run: -max(c.rrf_score for c in run))
    return runs


def apply_score_floors(
    ranked: Sequence[Candidate], *, absolute: float = 0.25, relative: float = 0.50
) -> list[Candidate]:
    """Drop weak candidates before they reach the model.

    This is the main structural defence against hallucination-by-context-
    stuffing. A question with no answer in the corpus MUST arrive at the answer
    call with little or no evidence, so the model can correctly refuse. Padding
    the context to a fixed top-n regardless of score is what turns "I don't
    know" into a confident fabrication.
    """
    if not ranked:
        return []
    scored = [c for c in ranked if c.rerank_score is not None]
    if not scored:
        return list(ranked)
    top = max(c.rerank_score for c in scored)
    floor = max(absolute, relative * top)
    return [c for c in scored if c.rerank_score >= floor]


def assign_rerank_deltas(
    pre_rerank: Sequence[Candidate], post_rerank: Sequence[Candidate]
) -> None:
    """Record how far the cross-encoder moved each chunk.

    One column in the inspector that explains what reranking did better than any
    chart: a row promoted from #14 to #1 is the reranker earning its latency.
    """
    before = {c.chunk_id: i for i, c in enumerate(pre_rerank, start=1)}
    for new_rank, c in enumerate(post_rerank, start=1):
        old = before.get(c.chunk_id)
        c.rerank_delta = None if old is None else old - new_rank


def batch_by_token_budget(
    texts: Sequence[str], *, budget: int = 8192, max_pair_tokens: int = 512
) -> list[list[int]]:
    """Pack rerank pairs into batches by TOKEN budget, not row count.

    Sort by length first so each batch pads to a similar max. On a mixed-length
    batch, padding waste routinely hits 35-45%; length bucketing recovers most
    of it. Returns lists of indices into `texts`.
    """
    order = sorted(range(len(texts)), key=lambda i: len(texts[i]))
    batches: list[list[int]] = []
    current: list[int] = []
    used = 0
    for i in order:
        est = min(len(texts[i]) // 4 + 32, max_pair_tokens)
        if current and used + est > budget:
            batches.append(current)
            current, used = [], 0
        current.append(i)
        used += est
    if current:
        batches.append(current)
    return batches
