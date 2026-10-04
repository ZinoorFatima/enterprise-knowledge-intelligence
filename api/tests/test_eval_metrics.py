"""Evaluation metrics.

The recurring theme: undefined is not zero, and a metric must never be able to
flatter a system that is failing. Several of these tests exist specifically to
pin down cases where a naive implementation would silently report a good number.
"""

from __future__ import annotations

import pytest

from app.eval.metrics import (
    RelevantPage,
    RetrievedChunk,
    aggregate,
    answer_relevancy,
    citation_page_accuracy,
    compare,
    context_precision,
    context_recall_by_pages,
    is_relevant,
    ndcg_at_k,
    recall_at_k,
    reciprocal_rank,
)


def chunk(cid: int, page: int, doc: str = "d1", page_end: int | None = None) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=cid, document_id=doc, page_start=page, page_end=page_end or page,
        text=f"chunk {cid}",
    )


REL = [RelevantPage("d1", 5)]


class TestRelevanceLabel:
    def test_page_containment(self):
        assert is_relevant(chunk(1, 5), REL)
        assert not is_relevant(chunk(1, 6), REL)

    def test_chunk_spanning_a_page_break_counts(self):
        """Page labels survive re-chunking; a chunk covering pages 4-6 contains
        the labelled page 5."""
        assert is_relevant(chunk(1, 4, page_end=6), REL)

    def test_same_page_in_a_different_document_is_not_relevant(self):
        assert not is_relevant(chunk(1, 5, doc="d2"), REL)


class TestContextPrecision:
    def test_relevant_first_beats_relevant_last(self):
        good = context_precision([chunk(1, 5), chunk(2, 9)], REL)
        bad = context_precision([chunk(1, 9), chunk(2, 5)], REL)
        assert good > bad

    def test_all_relevant_is_one(self):
        rel = [RelevantPage("d1", 5), RelevantPage("d1", 6)]
        assert context_precision([chunk(1, 5), chunk(2, 6)], rel) == pytest.approx(1.0)

    def test_undefined_when_nothing_relevant_retrieved(self):
        """Scoring this 0 would drag the mean down misleadingly; scoring it 1
        would flatter. It is genuinely undefined."""
        assert context_precision([chunk(1, 9), chunk(2, 8)], REL) is None

    def test_undefined_on_empty_retrieval(self):
        assert context_precision([], REL) is None


class TestContextRecall:
    def test_counts_covered_pages(self):
        rel = [RelevantPage("d1", 5), RelevantPage("d1", 6), RelevantPage("d1", 7)]
        assert context_recall_by_pages([chunk(1, 5), chunk(2, 6)], rel) == pytest.approx(2 / 3)

    def test_full_recall(self):
        assert context_recall_by_pages([chunk(1, 5)], REL) == 1.0

    def test_zero_recall_is_zero_not_none(self):
        """Unlike precision, recall IS defined when nothing relevant was found:
        we know the denominator, so the answer is genuinely 0."""
        assert context_recall_by_pages([chunk(1, 9)], REL) == 0.0

    def test_undefined_without_labels(self):
        assert context_recall_by_pages([chunk(1, 5)], []) is None


class TestRankingMetrics:
    def test_ndcg_rewards_earlier_hits(self):
        early = ndcg_at_k([chunk(1, 5), chunk(2, 9)], REL)
        late = ndcg_at_k([chunk(1, 9), chunk(2, 5)], REL)
        assert early > late

    def test_reciprocal_rank(self):
        assert reciprocal_rank([chunk(1, 9), chunk(2, 5)], REL) == pytest.approx(0.5)

    def test_reciprocal_rank_is_zero_when_absent(self):
        assert reciprocal_rank([chunk(1, 9)], REL) == 0.0

    def test_recall_at_k_truncates(self):
        chunks = [chunk(i, 9) for i in range(1, 21)] + [chunk(99, 5)]
        assert recall_at_k(chunks, REL, k=20) == 0.0
        assert recall_at_k(chunks, REL, k=21) == 1.0


class TestAnswerRelevancy:
    def test_identical_direction_is_one(self):
        assert answer_relevancy([1.0, 0.0], [[1.0, 0.0]]) == pytest.approx(1.0)

    def test_orthogonal_is_zero(self):
        assert answer_relevancy([1.0, 0.0], [[0.0, 1.0]]) == pytest.approx(0.0)

    def test_averages_over_generated_questions(self):
        got = answer_relevancy([1.0, 0.0], [[1.0, 0.0], [0.0, 1.0]])
        assert got == pytest.approx(0.5)

    def test_none_without_generated_questions(self):
        assert answer_relevancy([1.0, 0.0], []) is None


class TestCitationPageAccuracy:
    def test_should_be_one_when_resolution_is_correct(self):
        """Not a quality metric -- a correctness check on citation resolution.
        Below 1.0 means the provenance chain is broken."""
        pages = {47: "The aggregate liability shall not exceed twelve months of fees."}
        got = citation_page_accuracy(
            [(47, "shall not exceed twelve months")], lambda p: pages.get(p)
        )
        assert got == 1.0

    def test_detects_a_citation_pointing_at_the_wrong_page(self):
        pages = {47: "Some other text entirely.", 48: "The cap is twelve months."}
        got = citation_page_accuracy([(47, "The cap is twelve months.")], lambda p: pages.get(p))
        assert got == 0.0

    def test_tolerates_whitespace_differences(self):
        pages = {1: "The  cap\nis   twelve months."}
        assert citation_page_accuracy([(1, "The cap is twelve months.")], lambda p: pages.get(p)) == 1.0


class TestAggregation:
    def test_excludes_undefined_and_counts_them(self):
        """A system that retrieves nothing relevant must not quietly post a
        strong precision figure -- the exclusion count is how you catch it."""
        s = aggregate([1.0, None, 0.5, None])
        assert s.mean == pytest.approx(0.75)
        assert s.n == 2
        assert s.undefined == 2

    def test_all_undefined(self):
        s = aggregate([None, None])
        assert s.mean is None and s.n == 0 and s.undefined == 2

    def test_bootstrap_ci_brackets_the_mean(self):
        s = aggregate([0.8, 0.9, 0.85, 0.95, 0.7, 0.88, 0.92, 0.81])
        assert s.ci_low is not None and s.ci_high is not None
        assert s.ci_low <= s.mean <= s.ci_high

    def test_is_deterministic(self):
        vals = [0.8, 0.9, 0.85, 0.95, 0.7]
        assert aggregate(vals).ci_low == aggregate(vals).ci_low


class TestRunComparison:
    def test_small_delta_on_noisy_data_is_not_significant(self):
        """Without this guard every run looks like an improvement and the team
        optimizes noise."""
        baseline = [0.5, 0.9, 0.3, 0.8, 0.6, 0.2, 0.95, 0.4]
        candidate = [0.52, 0.88, 0.33, 0.79, 0.63, 0.18, 0.97, 0.42]
        res = compare("context_recall", baseline, candidate)
        assert res.delta is not None and abs(res.delta) < 0.05
        assert res.significant is False

    def test_large_consistent_delta_is_significant(self):
        baseline = [0.40] * 12
        candidate = [0.75] * 12
        res = compare("context_recall", baseline, candidate)
        assert res.significant is True
        assert res.delta == pytest.approx(0.35)

    def test_handles_no_overlapping_items(self):
        res = compare("m", [None, None], [None, None])
        assert res.delta is None and res.significant is False


class TestNdcgBound:
    """nDCG is bounded at 1.0 by definition. A value above it is always a
    counting bug -- here, crediting one labelled page once per covering chunk.
    This shipped and produced 1.105 on a real run."""

    def test_never_exceeds_one_when_many_chunks_cover_one_page(self):
        rel = [RelevantPage("d1", 5)]
        # Five coarse chunks all spanning the single labelled page.
        chunks = [RetrievedChunk(i, "d1", 4, 6) for i in range(5)]
        got = ndcg_at_k(chunks, rel, 10)
        assert got is not None and got <= 1.0, f"nDCG was {got}, which is impossible"

    def test_perfect_ranking_is_exactly_one(self):
        rel = [RelevantPage("d1", 5), RelevantPage("d1", 6)]
        chunks = [chunk(1, 5), chunk(2, 6), chunk(3, 99)]
        assert ndcg_at_k(chunks, rel, 10) == pytest.approx(1.0)

    def test_still_rewards_ranking_the_relevant_page_earlier(self):
        rel = [RelevantPage("d1", 5)]
        early = ndcg_at_k([chunk(1, 5), chunk(2, 99), chunk(3, 99)], rel, 10)
        late = ndcg_at_k([chunk(1, 99), chunk(2, 99), chunk(3, 5)], rel, 10)
        assert early is not None and late is not None
        assert early > late
        assert early <= 1.0 and late <= 1.0

    def test_duplicate_page_coverage_adds_nothing_after_the_first(self):
        rel = [RelevantPage("d1", 5)]
        once = ndcg_at_k([chunk(1, 5)], rel, 10)
        twice = ndcg_at_k([chunk(1, 5), chunk(2, 5)], rel, 10)
        assert once == twice
