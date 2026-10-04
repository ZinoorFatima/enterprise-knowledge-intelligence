"""Lexical reranker scoring contract.

The load-bearing property is not the ranking, it is the FLOOR RELATIONSHIP: a
passage with no lexical evidence must score strictly below
settings.rerank_score_floor, so that a question the corpus cannot answer
arrives at the model with no evidence and is refused.

A previous version returned `0.25 + 0.75 * overlap`. Zero overlap scored
exactly 0.25 -- precisely the configured floor, compared with `>=` -- so every
irrelevant passage survived and unanswerable questions were answered. These
tests exist so that cannot come back.
"""

from __future__ import annotations

import pytest

from app.config import settings
from app.retrieve.embedder import LexicalReranker
from app.retrieve.fuse import apply_score_floors
from tests.fakes import make_candidate

FLOOR = settings.rerank_score_floor

# Real clauses from the dev corpus.
LIABILITY = (
    "Aggregate liability of either party under this agreement shall not exceed "
    "the total fees paid during the twelve months immediately preceding the claim."
)
TERMINATION = (
    "Either party may terminate this agreement for convenience upon sixty days "
    "prior written notice delivered to the other party at the address stated in "
    "the preamble."
)
GOVERNING_LAW = (
    "This agreement is governed by the laws of the State of Delaware, and the "
    "parties submit to the exclusive jurisdiction of the Delaware Court of Chancery."
)
ENCRYPTION = (
    "All production data must be encrypted at rest using AES-256 or stronger, "
    "and in transit using TLS 1.2 or above."
)
CORPUS = [LIABILITY, TERMINATION, GOVERNING_LAW, ENCRYPTION]


async def score(query: str, passages=None) -> list[float]:
    # `is None`, not `or`: an explicitly empty list is a real case to test and
    # must not be silently replaced by the default corpus.
    return await LexicalReranker().rerank(query, CORPUS if passages is None else passages)


class TestFloorRelationship:
    """The property that makes refusal work."""

    async def test_no_overlap_scores_zero_not_the_floor(self):
        scores = await score("quantum chromodynamics lagrangian formulation")
        assert max(scores) == 0.0
        assert max(scores) < FLOOR

    @pytest.mark.parametrize(
        "question",
        [
            "What is the chief executive's home address?",
            "How many employees does the vendor have in Singapore?",
            "What was the share price on the day of the merger?",
        ],
    )
    async def test_unanswerable_questions_fall_below_the_floor(self, question):
        """These are the exact shapes the golden set uses. Each shares at most
        one incidental word with the corpus, which is not evidence."""
        scores = await score(question)
        assert max(scores) < FLOOR, (
            f"{question!r} scored {max(scores)} >= floor {FLOOR}; "
            "an unanswerable question would reach the model with evidence"
        )

    async def test_single_incidental_match_is_not_evidence(self):
        """'address' appears in the termination clause, but a question about a
        home address is not answered by it."""
        scores = await score("What is the chief executive's home address?", [TERMINATION])
        assert scores[0] < FLOOR

    async def test_score_floors_drop_everything_when_nothing_matches(self):
        """End to end through the real floor logic: no evidence survives."""
        scores = await score("What is the chief executive's home address?")
        cands = [make_candidate(i) for i in range(len(scores))]
        for c, s in zip(cands, scores):
            c.rerank_score = s
        kept = apply_score_floors(
            cands,
            absolute=settings.rerank_score_floor,
            relative=settings.rerank_relative_floor,
        )
        assert kept == [], "evidence survived for an unanswerable question"


class TestRelevantQuestionsStillPass:
    """The fix must not make the reranker so strict that real questions refuse."""

    @pytest.mark.parametrize(
        "question,expected_passage",
        [
            ("What is the aggregate liability cap?", LIABILITY),
            ("How much notice is needed to terminate for convenience?", TERMINATION),
            ("Which state law governs the vendor agreement?", GOVERNING_LAW),
            ("What encryption is required for production data at rest?", ENCRYPTION),
            ("which state court settles disagreements", GOVERNING_LAW),
        ],
    )
    async def test_answerable_question_keeps_its_passage(self, question, expected_passage):
        scores = await score(question)
        best = CORPUS[scores.index(max(scores))]
        assert max(scores) >= FLOOR, f"{question!r} scored only {max(scores)}"
        assert best == expected_passage, f"{question!r} ranked the wrong passage first"

    async def test_ranks_the_relevant_passage_above_the_rest(self):
        scores = await score("What is the aggregate liability cap?")
        assert scores[0] == max(scores)
        assert scores[0] > scores[3]


class TestScoringShape:
    async def test_scores_stay_in_range(self):
        scores = await score("liability termination encryption Delaware agreement")
        assert all(0.0 <= s <= 1.0 for s in scores)

    async def test_stopwords_do_not_dilute_the_denominator(self):
        """'What is the ... ?' should not change the judgement of a passage."""
        bare = await score("aggregate liability", [LIABILITY])
        wrapped = await score("What is the aggregate liability?", [LIABILITY])
        assert bare[0] == pytest.approx(wrapped[0])

    async def test_query_of_only_stopwords_stays_neutral(self):
        """Nothing to judge on: do not invent an ordering, let RRF stand."""
        scores = await score("what is this about")
        assert all(s == 0.5 for s in scores)

    async def test_empty_passage_list(self):
        assert await score("anything", []) == []
