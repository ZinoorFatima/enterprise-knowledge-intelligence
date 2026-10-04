"""RAG evaluation metrics.

Design rules that keep these numbers honest:

  * Undefined is not zero. Context Precision is undefined when nothing relevant
    was retrieved. Scoring it 0 would drag the mean down misleadingly; scoring it
    1 would flatter. It returns None, is excluded from the mean, and the
    exclusion COUNT is reported -- otherwise a system that retrieves nothing can
    quietly post a strong precision figure.

  * Refusal is not failure. On a question the corpus genuinely cannot answer, a
    refusal is the correct output. Those items are scored separately as refusal
    accuracy instead of being folded into relevancy, where they would penalize
    the behaviour we want.

  * Page labels, not chunk ids. Chunk ids die the moment the chunker changes --
    which is exactly the experiment the eval exists to measure.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Iterable, Sequence


@dataclass(frozen=True)
class RelevantPage:
    document_id: str
    page: int


@dataclass
class RetrievedChunk:
    """Minimal view of a retrieved chunk for scoring."""

    chunk_id: int
    document_id: str
    page_start: int
    page_end: int
    text: str = ""


def is_relevant(chunk: RetrievedChunk, relevant_pages: Sequence[RelevantPage]) -> bool:
    """Deterministic relevance: does this chunk cover a labelled page?

    Page containment, not chunk-id equality, so the label survives re-chunking.
    """
    for rp in relevant_pages:
        if rp.document_id != chunk.document_id:
            continue
        if chunk.page_start <= rp.page <= chunk.page_end:
            return True
    return False


# ───────────────────────────────────────────────── Context Precision


def context_precision(
    retrieved: Sequence[RetrievedChunk],
    relevant_pages: Sequence[RelevantPage],
    *,
    k: int | None = None,
) -> float | None:
    """Rank-weighted precision over the final context.

        P@i  = (relevant in ranks 1..i) / i
        CP   = sum(P@i * v_i) / sum(v_i)      v_i in {0,1}

    Weighted so an irrelevant chunk near the TOP hurts more than one at the
    bottom, which matches how attention actually degrades.

    Returns None when nothing relevant was retrieved -- undefined, not zero.
    """
    items = list(retrieved)[: k or len(retrieved)]
    if not items:
        return None

    hits = 0
    weighted_sum = 0.0
    for i, chunk in enumerate(items, start=1):
        v = 1 if is_relevant(chunk, relevant_pages) else 0
        if v:
            hits += 1
            weighted_sum += hits / i
    if hits == 0:
        return None
    return weighted_sum / hits


# ───────────────────────────────────────────────── Context Recall


def context_recall_by_pages(
    retrieved: Sequence[RetrievedChunk], relevant_pages: Sequence[RelevantPage]
) -> float | None:
    """Deterministic recall: what fraction of labelled pages did we retrieve?

    This is the metric that moves when the chunker, lane_k, or RRF k changes --
    the retrieval health signal.
    """
    if not relevant_pages:
        return None
    covered = {
        (rp.document_id, rp.page)
        for rp in relevant_pages
        if any(is_relevant(c, [rp]) for c in retrieved)
    }
    return len(covered) / len(set((rp.document_id, rp.page) for rp in relevant_pages))


def context_recall_by_claims(attributable: Sequence[bool]) -> float | None:
    """LLM-judged recall: of the ground-truth answer's atomic claims, how many
    are attributable to the context we actually assembled?"""
    if not attributable:
        return None
    return sum(1 for a in attributable if a) / len(attributable)


# ───────────────────────────────────────────────── Faithfulness
# Imported from the live verifier rather than reimplemented: if faithfulness
# were computed twice, one of the implementations would be lying.
from app.generate.verify import compute_faithfulness  # noqa: E402  (intentional)

__all__ = ["compute_faithfulness"]


# ───────────────────────────────────────────────── Answer Relevancy


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    num = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return num / (na * nb)


def answer_relevancy(
    original_embedding: Sequence[float], generated_embeddings: Sequence[Sequence[float]]
) -> float | None:
    """Reverse-generate N questions from the answer, embed, mean cosine to the
    original question.

    Measures whether the answer addressed the question ASKED -- not whether it
    is correct, which is faithfulness's job. Must use the SAME encoder as
    retrieval, or the number is not comparable with anything else in the system.
    """
    if not generated_embeddings:
        return None
    sims = [cosine(original_embedding, g) for g in generated_embeddings]
    return sum(sims) / len(sims)


# ───────────────────────────────────────────────── deterministic extras


def ndcg_at_k(
    retrieved: Sequence[RetrievedChunk], relevant_pages: Sequence[RelevantPage], k: int = 10
) -> float | None:
    """nDCG@k over labelled pages.

    Each labelled page is credited AT MOST ONCE. Several chunks routinely cover
    the same page -- especially when chunks are coarse -- and crediting each one
    separately makes DCG exceed IDCG, producing scores above 1.0. nDCG is
    bounded at 1.0 by definition, so a value above it is always a counting bug
    rather than an unusually good result.
    """
    if not relevant_pages:
        return None
    items = list(retrieved)[:k]
    if not items:
        return 0.0

    wanted = {(rp.document_id, rp.page) for rp in relevant_pages}
    credited: set[tuple[str, int]] = set()
    dcg = 0.0
    for i, c in enumerate(items, start=1):
        # Which labelled pages does this chunk cover that are still uncredited?
        newly = {
            (doc, page)
            for (doc, page) in wanted
            if doc == c.document_id and c.page_start <= page <= c.page_end
        } - credited
        if newly:
            credited |= newly
            dcg += 1.0 / math.log2(i + 1)

    n_rel = min(len(wanted), k)
    idcg = sum(1.0 / math.log2(i + 1) for i in range(1, n_rel + 1))
    return (dcg / idcg) if idcg else 0.0


def reciprocal_rank(
    retrieved: Sequence[RetrievedChunk], relevant_pages: Sequence[RelevantPage]
) -> float:
    for i, c in enumerate(retrieved, start=1):
        if is_relevant(c, relevant_pages):
            return 1.0 / i
    return 0.0


def recall_at_k(
    retrieved: Sequence[RetrievedChunk], relevant_pages: Sequence[RelevantPage], k: int = 20
) -> float | None:
    return context_recall_by_pages(list(retrieved)[:k], relevant_pages)


def citation_page_accuracy(
    citations: Sequence[tuple[int, str]], page_text_lookup
) -> float | None:
    """Fraction of citations whose resolved page actually contains the cited text.

    This should be exactly 1.0. It is not a quality metric -- it is a CORRECTNESS
    check on citation resolution. Anything below 1.0 means the provenance chain
    is broken, which is a bug, not a tuning opportunity.
    """
    cits = list(citations)
    if not cits:
        return None
    ok = 0
    for page, cited_text in cits:
        page_text = page_text_lookup(page) or ""
        needle = " ".join(cited_text.split())[:120]
        if needle and needle in " ".join(page_text.split()):
            ok += 1
    return ok / len(cits)


# ───────────────────────────────────────────────── aggregation


@dataclass
class MetricSummary:
    mean: float | None
    n: int
    undefined: int
    ci_low: float | None = None
    ci_high: float | None = None


def aggregate(values: Iterable[float | None], *, bootstrap: int = 1000, seed: int = 7) -> MetricSummary:
    """Mean over defined values, with a bootstrap 95% CI.

    The CI is not decoration. With ~100 items the half-width is routinely
    0.04-0.06, so without it every run looks like an improvement and the team
    optimizes noise.
    """
    vals = [v for v in values if v is not None]
    undefined = sum(1 for v in values if v is None)
    if not vals:
        return MetricSummary(None, 0, undefined)

    mean = sum(vals) / len(vals)
    if len(vals) < 2 or bootstrap <= 0:
        return MetricSummary(mean, len(vals), undefined)

    rng = random.Random(seed)
    means = []
    n = len(vals)
    for _ in range(bootstrap):
        sample = [vals[rng.randrange(n)] for _ in range(n)]
        means.append(sum(sample) / n)
    means.sort()
    lo = means[int(0.025 * bootstrap)]
    hi = means[min(int(0.975 * bootstrap), bootstrap - 1)]
    return MetricSummary(mean, len(vals), undefined, lo, hi)


@dataclass
class RunComparison:
    metric: str
    baseline: float | None
    candidate: float | None
    delta: float | None
    ci_low: float | None
    ci_high: float | None
    significant: bool


def compare(
    metric: str,
    baseline: Sequence[float | None],
    candidate: Sequence[float | None],
    *,
    bootstrap: int = 1000,
    seed: int = 7,
) -> RunComparison:
    """Paired bootstrap on the delta.

    A delta whose CI crosses zero is reported as 'no significant change', which
    is the single most important guard against celebrating noise.
    """
    pairs = [(b, c) for b, c in zip(baseline, candidate) if b is not None and c is not None]
    if not pairs:
        return RunComparison(metric, None, None, None, None, None, False)

    b_mean = sum(p[0] for p in pairs) / len(pairs)
    c_mean = sum(p[1] for p in pairs) / len(pairs)
    delta = c_mean - b_mean

    rng = random.Random(seed)
    deltas = []
    n = len(pairs)
    for _ in range(bootstrap):
        sample = [pairs[rng.randrange(n)] for _ in range(n)]
        deltas.append(sum(c - b for b, c in sample) / n)
    deltas.sort()
    lo = deltas[int(0.025 * bootstrap)]
    hi = deltas[min(int(0.975 * bootstrap), bootstrap - 1)]
    return RunComparison(metric, b_mean, c_mean, delta, lo, hi, significant=(lo > 0 or hi < 0))
