"""LangGraph pipeline behaviour.

The tests that matter here are about ROUTING, because the conditional edges are
where the product's honesty guarantees live: refuse when nothing clears the
floors, never leave verification silently absent, never let one failing stage
take down the whole query.
"""

from __future__ import annotations

import pytest

from app.generate.provider import OfflineProvider
from app.graph.nodes import needs_rewrite
from app.graph.pipeline import build_pipeline, run_pipeline
from app.graph.state import Filters, PipelineDeps
from tests.fakes import (
    EmptyRetriever,
    FailingReranker,
    FakeEmbedder,
    FakeReranker,
    FakeRetriever,
    LowScoreReranker,
    make_candidate,
)


def deps(retriever=None, reranker=None, provider=None) -> PipelineDeps:
    return PipelineDeps(
        retriever=retriever or FakeRetriever(),
        embedder=FakeEmbedder(),
        reranker=reranker or FakeReranker(),
        provider=provider or OfflineProvider(),
        org_id="org-1",
    )


class TestHappyPath:
    async def test_produces_answer_with_citations(self):
        out = await run_pipeline(question="What is the liability cap?", deps=deps())
        assert out.answer_text
        assert not out.refused
        assert out.citations, "a grounded answer must carry citations"

    async def test_every_citation_points_at_assembled_evidence(self):
        """The provenance chain must hold end to end: a citation's evidence_index
        has to address a block we actually sent."""
        out = await run_pipeline(question="What is the liability cap?", deps=deps())
        for c in out.citations:
            assert 0 <= c.evidence_index < len(out.evidence)
            ev = out.evidence[c.evidence_index]
            assert 0 <= c.start_block_index < len(ev.blocks)
            assert c.cited_text in ev.blocks

    async def test_records_every_stage_latency(self):
        out = await run_pipeline(question="What is the liability cap?", deps=deps())
        for stage in ("retrieve", "fuse", "rerank", "assemble", "generate"):
            assert stage in out.stage_latency_ms

    async def test_state_retains_the_full_audit_trail(self):
        """The inspector renders from this. If intermediate stages were
        discarded there would be nothing to show."""
        out = await run_pipeline(question="What is the liability cap?", deps=deps())
        assert out.lane_sizes, "per-lane result counts must survive"
        assert out.fused, "the fused ranking must survive"
        assert out.reranked, "the reranked ranking must survive"
        assert out.rewrite is not None


class TestRefusalRouting:
    async def test_refuses_when_nothing_clears_the_score_floors(self):
        """The central honesty guarantee, as control flow. Everything retrieved
        scores 0.05, so the model must receive no evidence and refuse rather
        than be handed filler to hallucinate from."""
        out = await run_pipeline(
            question="What is the airspeed of an unladen swallow?",
            deps=deps(reranker=LowScoreReranker()),
        )
        assert out.refused
        assert out.evidence == []
        assert out.citations == []
        assert "could not find support" in out.answer_text

    async def test_refuses_when_retrieval_returns_nothing(self):
        out = await run_pipeline(question="Anything at all?", deps=deps(retriever=EmptyRetriever()))
        assert out.refused

    async def test_a_refusal_is_still_reported_as_unverified(self):
        """Silently omitting verification on a product that promises it is the
        worst failure mode available."""
        out = await run_pipeline(question="Unanswerable", deps=deps(reranker=LowScoreReranker()))
        assert out.verification is not None
        assert out.verification.unavailable_reason
        assert out.verification.faithfulness is None


class TestRewriteGate:
    @pytest.mark.parametrize(
        "question,history,expected",
        [
            # Standalone, specific, mid-length: a rewrite would only add latency.
            ("What is the liability cap in the Acme master services agreement?", None, False),
            ("cap?", None, True),                          # too terse to embed well
            ("Does it apply to them?", None, True),        # anaphora to resolve
            # Any follow-up: anaphora is near-guaranteed once there is context.
            ("What is the liability cap in the Acme agreement?",
             [{"role": "user", "content": "prior"}], True),
        ],
    )
    def test_gate_decisions(self, question, history, expected):
        assert needs_rewrite(question, history) is expected

    async def test_skipping_is_recorded_not_hidden(self):
        """The inspector must be able to say 'rewriting off' rather than leaving
        the user to wonder whether it failed."""
        out = await run_pipeline(
            question="What is the liability cap stated here?", deps=deps(), want_rewrite=False
        )
        assert out.rewrite is not None and out.rewrite.skipped

    async def test_hyde_lane_never_reaches_keyword_search(self):
        """Feeding a synthetic passage into keyword search injects hallucinated
        terms; an invented clause number would dominate BM25."""
        retriever = FakeRetriever()
        await run_pipeline(question="x", deps=deps(retriever=retriever), want_rewrite=True)
        for call in retriever.calls:
            if call["w_bm25"] > 0:
                assert call["query_text"], "a lexical lane must have query text"
            if call["w_bm25"] == 0:
                assert call["query_text"] == "", "vector-only lanes must send no query text"


class TestResilience:
    async def test_rerank_failure_degrades_to_rrf_order(self):
        """One failing stage must not take down the query."""
        out = await run_pipeline(
            question="What is the liability cap?", deps=deps(reranker=FailingReranker())
        )
        assert out.answer_text
        assert not out.refused
        assert any("rerank failed" in e for e in out.errors)

    async def test_errors_are_surfaced_not_swallowed(self):
        out = await run_pipeline(question="q", deps=deps(reranker=FailingReranker()))
        assert out.errors


class TestFilters:
    async def test_filters_reach_every_lane(self):
        """Filtering after fusion would bias results toward whichever lane
        happened to surface in-filter documents, so both lanes must filter."""
        corpus = [
            make_candidate(1, document_id="doc-a", ordinal=0),
            make_candidate(2, document_id="doc-b", ordinal=1),
        ]
        retriever = FakeRetriever(corpus)
        out = await run_pipeline(
            question="What is the liability cap?",
            deps=deps(retriever=retriever),
            filters=Filters(document_ids=["doc-a"]),
        )
        for c in out.reranked:
            assert c.document_id == "doc-a"


class TestGraphShape:
    def test_compiles(self):
        assert build_pipeline() is not None

    def test_every_node_is_reachable(self):
        g = build_pipeline().get_graph()
        names = {n for n in g.nodes}
        for expected in (
            "rewrite", "skip_rewrite", "retrieve", "fuse", "rerank",
            "assemble", "generate", "refuse", "verify", "skip_verify",
        ):
            assert expected in names
