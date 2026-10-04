"""Eval harness.

The rule that makes the numbers mean anything: this runner calls
`run_pipeline` -- the SAME graph the product serves -- and `verify_answer` --
the SAME verifier the live path uses. Nothing here reimplements a pipeline
stage. If faithfulness were computed by two implementations, one of them would
be lying, and you would not know which.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Sequence

from app.config import settings
from app.eval.metrics import (
    MetricSummary,
    RelevantPage,
    RetrievedChunk,
    aggregate,
    answer_relevancy,
    context_precision,
    context_recall_by_pages,
    ndcg_at_k,
    recall_at_k,
    reciprocal_rank,
)
from app.graph.pipeline import run_pipeline
from app.graph.state import Filters, PipelineDeps


@dataclass
class EvalItem:
    id: str
    question: str
    ground_truth: str | None = None
    relevant_pages: list[RelevantPage] = field(default_factory=list)
    filters: Filters = field(default_factory=Filters)
    difficulty: str = "single_hop"
    # 10-15% of a healthy dataset. Without them, a system that answers
    # everything confidently scores well and you will ship it.
    expected_behavior: str = "answer"
    reviewed: bool = False
    notes: str = ""

    @classmethod
    def from_json(cls, raw: dict) -> "EvalItem":
        return cls(
            id=str(raw["id"]),
            question=raw["question"],
            ground_truth=raw.get("ground_truth"),
            relevant_pages=[
                RelevantPage(document_id=str(p["document_id"]), page=int(p["page"]))
                for p in raw.get("relevant_pages", [])
            ],
            filters=Filters(**raw.get("filters", {})),
            difficulty=raw.get("difficulty", "single_hop"),
            expected_behavior=raw.get("expected_behavior", "answer"),
            reviewed=bool(raw.get("reviewed", False)),
            notes=raw.get("notes", ""),
        )


@dataclass
class ItemResult:
    item_id: str
    answer: str
    refused: bool
    expected_behavior: str
    context_precision: float | None = None
    context_recall: float | None = None
    faithfulness: float | None = None
    answer_relevancy: float | None = None
    ndcg_at_10: float | None = None
    reciprocal_rank: float | None = None
    recall_at_20: float | None = None
    latency_ms: float = 0.0
    error: str | None = None


@dataclass
class RunSummary:
    """A run's headline numbers, plus the honesty flags that qualify them."""

    n_items: int
    llm_mode: str
    # True when ANY item is unreviewed, or when generation was stubbed. The
    # dashboard renders a provisional run greyed, with a badge. This is the
    # mechanism that stops synthetic numbers becoming the headline.
    provisional: bool
    provisional_reasons: list[str]
    context_precision: MetricSummary
    context_recall: MetricSummary
    faithfulness: MetricSummary
    answer_relevancy: MetricSummary
    ndcg_at_10: MetricSummary
    mrr: MetricSummary
    recall_at_20: MetricSummary
    refusal_accuracy: float | None
    refusal_n: int
    p50_latency_ms: float
    p95_latency_ms: float
    config: dict
    results: list[ItemResult] = field(default_factory=list)

    def to_dict(self) -> dict:
        def ms(m: MetricSummary) -> dict:
            return {
                "mean": m.mean, "n": m.n, "undefined": m.undefined,
                "ci_low": m.ci_low, "ci_high": m.ci_high,
            }

        return {
            "n_items": self.n_items,
            "llm_mode": self.llm_mode,
            "provisional": self.provisional,
            "provisional_reasons": self.provisional_reasons,
            "metrics": {
                "context_precision": ms(self.context_precision),
                "context_recall": ms(self.context_recall),
                "faithfulness": ms(self.faithfulness),
                "answer_relevancy": ms(self.answer_relevancy),
                "ndcg_at_10": ms(self.ndcg_at_10),
                "mrr": ms(self.mrr),
                "recall_at_20": ms(self.recall_at_20),
            },
            "refusal_accuracy": self.refusal_accuracy,
            "refusal_n": self.refusal_n,
            "p50_latency_ms": self.p50_latency_ms,
            "p95_latency_ms": self.p95_latency_ms,
            "config": self.config,
            "results": [asdict(r) for r in self.results],
        }


def load_dataset(path: str | Path) -> list[EvalItem]:
    """JSONL, version-controlled alongside the code."""
    items = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("//"):
            items.append(EvalItem.from_json(json.loads(line)))
    return items


def _percentile(values: Sequence[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    idx = min(int(p * len(s)), len(s) - 1)
    return s[idx]


async def run_eval(
    items: Sequence[EvalItem],
    deps: PipelineDeps,
    *,
    embedder=None,
    want_rewrite: bool = True,
) -> RunSummary:
    """Run a dataset through the production graph and score it."""
    results: list[ItemResult] = []
    latencies: list[float] = []

    for item in items:
        t0 = time.perf_counter()
        try:
            out = await run_pipeline(
                question=item.question,
                deps=deps,
                filters=item.filters,
                want_rewrite=want_rewrite,
                # Verification is forced on in eval: sampling would make
                # faithfulness a measurement of a random subset.
                want_verify=True,
            )
        except Exception as exc:
            results.append(
                ItemResult(
                    item_id=item.id, answer="", refused=False,
                    expected_behavior=item.expected_behavior, error=str(exc),
                )
            )
            continue

        elapsed = (time.perf_counter() - t0) * 1000
        latencies.append(elapsed)

        retrieved = [
            RetrievedChunk(
                chunk_id=c.chunk_id, document_id=c.document_id,
                page_start=c.page_start, page_end=c.page_end, text=c.text,
            )
            for c in out.reranked
        ]
        # Recall@20 is measured over the FUSED set, before the reranker trims:
        # it answers "did retrieval find it at all", which is a different
        # question from "did the reranker keep it".
        fused_chunks = [
            RetrievedChunk(
                chunk_id=c.chunk_id, document_id=c.document_id,
                page_start=c.page_start, page_end=c.page_end, text=c.text,
            )
            for c in out.fused
        ]

        relevancy = None
        if embedder is not None and out.answer_text and not out.refused:
            relevancy = await _answer_relevancy(embedder, item.question, out.answer_text)

        results.append(
            ItemResult(
                item_id=item.id,
                answer=out.answer_text,
                refused=out.refused,
                expected_behavior=item.expected_behavior,
                context_precision=context_precision(retrieved, item.relevant_pages),
                context_recall=context_recall_by_pages(retrieved, item.relevant_pages),
                faithfulness=(out.verification.faithfulness if out.verification else None),
                answer_relevancy=relevancy,
                ndcg_at_10=ndcg_at_k(retrieved, item.relevant_pages, 10),
                reciprocal_rank=reciprocal_rank(retrieved, item.relevant_pages),
                recall_at_20=recall_at_k(fused_chunks, item.relevant_pages, 20),
                latency_ms=elapsed,
            )
        )

    # Refusal accuracy is scored only over items where refusing is correct, and
    # reported separately. Folding it into relevancy would penalize the
    # behaviour the score floors exist to produce.
    refuse_items = [r for r in results if r.expected_behavior == "refuse"]
    refusal_accuracy = (
        sum(1 for r in refuse_items if r.refused) / len(refuse_items) if refuse_items else None
    )

    # Answering items only: a correct refusal has no meaningful relevancy or
    # faithfulness, and averaging it in would distort both.
    answering = [r for r in results if r.expected_behavior == "answer"]

    reasons: list[str] = []
    if settings.is_offline:
        reasons.append(
            "LLM_MODE=offline: generation is extractive and entailment is not judged, "
            "so faithfulness and answer relevancy are not measured"
        )
    unreviewed = [i for i in items if not i.reviewed]
    if unreviewed:
        reasons.append(f"{len(unreviewed)} of {len(items)} dataset items are unreviewed")
    if not refuse_items:
        reasons.append(
            "dataset contains no expected-refusal items, so over-answering is unmeasured"
        )

    return RunSummary(
        n_items=len(items),
        llm_mode=settings.llm_mode,
        provisional=bool(reasons),
        provisional_reasons=reasons,
        context_precision=aggregate(r.context_precision for r in answering),
        context_recall=aggregate(r.context_recall for r in answering),
        faithfulness=aggregate(r.faithfulness for r in answering),
        answer_relevancy=aggregate(r.answer_relevancy for r in answering),
        ndcg_at_10=aggregate(r.ndcg_at_10 for r in answering),
        mrr=aggregate(r.reciprocal_rank for r in answering),
        recall_at_20=aggregate(r.recall_at_20 for r in answering),
        refusal_accuracy=refusal_accuracy,
        refusal_n=len(refuse_items),
        p50_latency_ms=_percentile(latencies, 0.50),
        p95_latency_ms=_percentile(latencies, 0.95),
        config=settings.snapshot(),
        results=results,
    )


async def _answer_relevancy(embedder, question: str, answer: str) -> float | None:
    """Embed the question and sentences of the answer with the SAME encoder as
    retrieval; a different encoder makes the number incomparable."""
    import re

    sents = [s.strip() for s in re.split(r"(?<=[.!?])\s+", answer) if len(s.strip()) > 20][:3]
    if not sents:
        return None
    vecs = await embedder.embed([question, *sents])
    return answer_relevancy(vecs[0], vecs[1:])
