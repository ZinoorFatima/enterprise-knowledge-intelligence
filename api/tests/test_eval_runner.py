"""End-to-end eval harness.

Proves the harness drives the production graph and that its honesty mechanisms
actually fire: provisional stamping, separate refusal scoring, and refusing to
report a faithfulness number nothing computed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.config import settings
from app.eval.runner import EvalItem, load_dataset, run_eval
from app.generate.provider import OfflineProvider
from app.graph.state import PipelineDeps
from tests.fakes import FakeEmbedder, FakeReranker, FakeRetriever, LowScoreReranker, make_candidate

GOLDEN = Path(__file__).resolve().parents[2] / "eval" / "golden" / "smoke.jsonl"


def corpus():
    """Two documents whose pages line up with the golden set's labels."""
    return [
        make_candidate(1, document_id="doc-a", ordinal=0, page=47,
                       text="The aggregate liability shall not exceed twelve months of fees."),
        make_candidate(2, document_id="doc-a", ordinal=1, page=14,
                       text="Either party may terminate for convenience on sixty days notice."),
        make_candidate(3, document_id="doc-b", ordinal=0, page=31,
                       text="Indemnity is capped at the total contract value."),
        make_candidate(4, document_id="doc-b", ordinal=1, page=2,
                       text="This agreement is governed by the laws of Delaware."),
    ]


def deps(reranker=None) -> PipelineDeps:
    return PipelineDeps(
        retriever=FakeRetriever(corpus()),
        embedder=FakeEmbedder(),
        reranker=reranker or FakeReranker(),
        provider=OfflineProvider(),
        org_id="org-1",
    )


class TestDataset:
    def test_golden_set_loads(self):
        items = load_dataset(GOLDEN)
        assert items, "golden dataset is empty"
        assert all(i.question for i in items)

    def test_contains_expected_refusal_items(self):
        """Without these, a system that answers everything confidently scores
        well and you ship it."""
        items = load_dataset(GOLDEN)
        refuse = [i for i in items if i.expected_behavior == "refuse"]
        assert refuse, "a healthy dataset needs unanswerable questions"

    def test_labels_are_pages_not_chunk_ids(self):
        """Chunk ids die when the chunker changes, which is the experiment the
        eval exists to measure."""
        items = load_dataset(GOLDEN)
        answerable = [i for i in items if i.expected_behavior == "answer"]
        assert all(i.relevant_pages for i in answerable)


class TestRunEval:
    async def test_runs_the_production_graph_end_to_end(self):
        items = load_dataset(GOLDEN)
        summary = await run_eval(items, deps(), embedder=FakeEmbedder())
        assert summary.n_items == len(items)
        assert len(summary.results) == len(items)

    async def test_retrieval_metrics_are_actually_measured(self):
        """These need no LLM, so they are real numbers even offline."""
        items = load_dataset(GOLDEN)
        summary = await run_eval(items, deps(), embedder=FakeEmbedder())
        assert summary.context_recall.mean is not None
        assert summary.ndcg_at_10.mean is not None
        assert summary.mrr.mean is not None

    async def test_offline_run_is_stamped_provisional(self):
        """Offline cannot judge entailment, so the run must declare itself
        provisional rather than presenting its numbers as measured."""
        items = load_dataset(GOLDEN)
        summary = await run_eval(items, deps(), embedder=FakeEmbedder())
        if settings.is_offline:
            assert summary.provisional
            assert any("offline" in r.lower() for r in summary.provisional_reasons)

    async def test_faithfulness_is_unmeasured_offline_not_invented(self):
        """The single most important honesty guarantee in the harness."""
        items = load_dataset(GOLDEN)
        summary = await run_eval(items, deps(), embedder=FakeEmbedder())
        if settings.is_offline:
            assert summary.faithfulness.mean is None

    async def test_unreviewed_items_make_a_run_provisional(self):
        items = [
            EvalItem(id="x", question="What is the liability cap here?", reviewed=False)
        ]
        summary = await run_eval(items, deps(), embedder=FakeEmbedder())
        assert summary.provisional
        assert any("unreviewed" in r for r in summary.provisional_reasons)

    async def test_dataset_without_refusal_items_is_flagged(self):
        items = [EvalItem(id="x", question="What is the liability cap here?", reviewed=True)]
        summary = await run_eval(items, deps(), embedder=FakeEmbedder())
        assert any("refusal" in r for r in summary.provisional_reasons)

    async def test_refusal_accuracy_is_scored_separately(self):
        """A correct refusal must not be averaged into relevancy, where it would
        penalize exactly the behaviour the score floors exist to produce."""
        items = load_dataset(GOLDEN)
        expected = sum(1 for i in items if i.expected_behavior == "refuse")
        summary = await run_eval(items, deps(reranker=LowScoreReranker()), embedder=FakeEmbedder())
        assert summary.refusal_n == expected
        # Nothing clears the floors, so every item refuses -- and on the items
        # where refusing is correct, accuracy is perfect.
        assert summary.refusal_accuracy == 1.0

    async def test_records_latency_percentiles(self):
        items = load_dataset(GOLDEN)
        summary = await run_eval(items, deps(), embedder=FakeEmbedder())
        assert summary.p50_latency_ms > 0
        assert summary.p95_latency_ms >= summary.p50_latency_ms

    async def test_snapshots_the_config_that_produced_it(self):
        """A number with no record of the settings behind it is not reproducible."""
        items = load_dataset(GOLDEN)
        summary = await run_eval(items, deps(), embedder=FakeEmbedder())
        assert summary.config["rrf_k"] == settings.rrf_k
        assert summary.config["rerank_top_n"] == settings.rerank_top_n
        assert "embed_model" in summary.config

    async def test_serializes_to_dict(self):
        items = load_dataset(GOLDEN)
        summary = await run_eval(items, deps(), embedder=FakeEmbedder())
        d = summary.to_dict()
        assert d["metrics"]["context_recall"]["n"] >= 0
        assert "provisional_reasons" in d

    async def test_a_failing_item_does_not_abort_the_run(self):
        class Boom:
            async def search(self, **kw):
                raise RuntimeError("db down")

        bad = PipelineDeps(
            retriever=Boom(), embedder=FakeEmbedder(),
            reranker=FakeReranker(), provider=OfflineProvider(), org_id="o",
        )
        items = load_dataset(GOLDEN)
        summary = await run_eval(items, bad, embedder=FakeEmbedder())
        assert summary.n_items == len(items)  # all items still accounted for
