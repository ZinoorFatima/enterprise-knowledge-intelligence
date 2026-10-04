"""Fusion, score floors, and rerank batching."""

from __future__ import annotations

import pytest

from app.retrieve.fuse import (
    Candidate,
    apply_score_floors,
    assign_rerank_deltas,
    batch_by_token_budget,
    fuse_lanes,
    merge_contiguous_runs,
    rrf_contribution,
)


def cand(cid: int, doc: str = "d1", ordinal: int = 0, **kw) -> Candidate:
    return Candidate(
        chunk_id=cid, document_id=doc, title=f"Doc {doc}", text=f"chunk {cid}",
        token_count=100, ordinal=ordinal, page_start=1, page_end=1,
        char_start=cid * 100, char_end=cid * 100 + 90, **kw,
    )


class TestRRF:
    def test_rank_one_scores_highest(self):
        assert rrf_contribution(1) > rrf_contribution(2) > rrf_contribution(50)

    def test_k_flattens_the_curve(self):
        """The core tradeoff: large k makes rank 1 much less dominant, which is
        what we want when a reranker will re-order everything anyway."""
        steep = rrf_contribution(1, k=10) / rrf_contribution(5, k=10)
        flat = rrf_contribution(1, k=60) / rrf_contribution(5, k=60)
        assert steep > flat
        assert flat == pytest.approx(65 / 61, rel=1e-6)

    def test_rejects_zero_rank(self):
        with pytest.raises(ValueError, match="1-indexed"):
            rrf_contribution(0)


class TestFuseLanes:
    def test_appearing_in_two_lanes_beats_one_strong_lane(self):
        """The reason RRF is used at all: agreement across lanes is a stronger
        signal than a single lane's confidence."""
        both = cand(1)
        only = cand(2)
        fused = fuse_lanes(
            {"original": [only, both], "hyde": [both]},
            {"original": 1.0, "hyde": 1.0},
        )
        assert fused[0].chunk_id == 1, "chunk in both lanes should win"

    def test_lane_weights_apply(self):
        a, b = cand(1), cand(2)
        fused = fuse_lanes(
            {"original": [a], "hyde": [b]}, {"original": 1.5, "hyde": 0.8}
        )
        assert fused[0].chunk_id == 1

    def test_zero_weight_lane_is_skipped(self):
        """The HyDE lane passes w_bm25=0 to avoid injecting hallucinated
        keywords into lexical search; a zero-weighted lane must contribute
        nothing at all."""
        fused = fuse_lanes({"a": [cand(1)], "b": [cand(2)]}, {"a": 1.0, "b": 0.0})
        assert [c.chunk_id for c in fused] == [1]

    def test_dedup_records_all_contributing_lanes(self):
        c1 = cand(1)
        fused = fuse_lanes(
            {"original": [c1], "hyde": [cand(1)], "keyword": [cand(1)]},
            {"original": 1.0, "hyde": 1.0, "keyword": 1.0},
        )
        assert len(fused) == 1
        assert fused[0].lanes == {"original", "hyde", "keyword"}

    def test_keeps_best_rank_per_lane(self):
        """Inspector must report the chunk's strongest evidence, not the last
        one written."""
        fused = fuse_lanes(
            {
                "a": [cand(1, bm25_rank=9, vec_rank=None, bm25_score=0.2)],
                "b": [cand(1, bm25_rank=3, vec_rank=4, bm25_score=0.7)],
            },
            {"a": 1.0, "b": 1.0},
        )
        assert fused[0].bm25_rank == 3
        assert fused[0].vec_rank == 4
        assert fused[0].bm25_score == pytest.approx(0.7)

    def test_none_lane_rank_is_preserved(self):
        """'This lane never retrieved it' must survive fusion — it is the most
        informative cell in the inspector table."""
        fused = fuse_lanes({"a": [cand(1, bm25_rank=None, vec_rank=2)]}, {"a": 1.0})
        assert fused[0].bm25_rank is None
        assert fused[0].vec_rank == 2

    def test_out_n_truncates(self):
        fused = fuse_lanes({"a": [cand(i) for i in range(100)]}, {"a": 1.0}, out_n=10)
        assert len(fused) == 10

    def test_empty(self):
        assert fuse_lanes({}, {}) == []


class TestContiguousRuns:
    def test_adjacent_ordinals_merge(self):
        runs = merge_contiguous_runs([cand(1, ordinal=5), cand(2, ordinal=6), cand(3, ordinal=7)])
        assert len(runs) == 1
        assert [c.ordinal for c in runs[0]] == [5, 6, 7]

    def test_gap_splits_the_run(self):
        runs = merge_contiguous_runs([cand(1, ordinal=1), cand(2, ordinal=2), cand(3, ordinal=9)])
        assert len(runs) == 2

    def test_different_documents_never_merge(self):
        runs = merge_contiguous_runs([cand(1, doc="a", ordinal=1), cand(2, doc="b", ordinal=2)])
        assert len(runs) == 2


class TestScoreFloors:
    def test_absolute_floor_drops_weak_evidence(self):
        cands = [cand(1, rerank_score=0.9), cand(2, rerank_score=0.1)]
        kept = apply_score_floors(cands, absolute=0.25, relative=0.0)
        assert [c.chunk_id for c in kept] == [1]

    def test_relative_floor_drops_trailing_evidence(self):
        cands = [cand(1, rerank_score=0.90), cand(2, rerank_score=0.40)]
        kept = apply_score_floors(cands, absolute=0.0, relative=0.50)
        assert [c.chunk_id for c in kept] == [1]

    def test_unanswerable_question_yields_no_evidence(self):
        """The defence against hallucination-by-context-stuffing: when nothing
        scores well, the model must receive nothing and refuse — not a padded
        top-n of irrelevant chunks."""
        cands = [cand(i, rerank_score=0.05) for i in range(1, 6)]
        assert apply_score_floors(cands, absolute=0.25, relative=0.50) == []

    def test_empty_input(self):
        assert apply_score_floors([]) == []


class TestRerankDeltas:
    def test_promotion_is_positive(self):
        pre = [cand(i) for i in range(1, 6)]
        post = [cand(5), cand(1), cand(2), cand(3), cand(4)]
        assign_rerank_deltas(pre, post)
        assert post[0].rerank_delta == 4   # was #5, now #1
        assert post[1].rerank_delta == -1  # was #1, now #2

    def test_unmoved_is_zero(self):
        pre = [cand(1), cand(2)]
        post = [cand(1), cand(2)]
        assign_rerank_deltas(pre, post)
        assert all(c.rerank_delta == 0 for c in post)


class TestTokenBudgetBatching:
    def test_respects_budget(self):
        texts = ["x" * 2000] * 20
        batches = batch_by_token_budget(texts, budget=2048, max_pair_tokens=512)
        assert all(len(b) <= 4 for b in batches)

    def test_every_index_appears_exactly_once(self):
        texts = ["y" * (100 * i) for i in range(1, 31)]
        batches = batch_by_token_budget(texts, budget=4096)
        flat = [i for b in batches for i in b]
        assert sorted(flat) == list(range(30))

    def test_sorts_by_length_to_reduce_padding_waste(self):
        texts = ["a" * 4000, "b" * 10, "c" * 4000, "d" * 10]
        batches = batch_by_token_budget(texts, budget=100_000)
        lengths = [len(texts[i]) for i in batches[0]]
        assert lengths == sorted(lengths)

    def test_empty(self):
        assert batch_by_token_budget([]) == []
