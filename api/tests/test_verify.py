"""Answer validation.

Most of these assert that the verifier FAILS CLOSED. A validation layer that
defaults to "supported" when it cannot actually check something is worse than no
validation at all, because it manufactures confidence.
"""

from __future__ import annotations

import pytest

from app.config import settings
from app.generate.provider import EvidenceBlock, OfflineProvider, Usage
from app.generate.verify import (
    SelfVerificationError,
    assert_independent_verifier,
    compute_faithfulness,
    find_uncited_spans,
    should_verify,
    verify_answer,
)
from app.graph.state import ClaimVerdict


def ev(chunk_id: int = 1, blocks=None) -> EvidenceBlock:
    return EvidenceBlock(
        chunk_id=chunk_id, document_id="doc-1", title="MSA.pdf",
        page_start=47, page_end=47, char_start=0,
        blocks=blocks or ["The aggregate liability shall not exceed twelve months of fees."],
    )


def claim(cid: int, label: str, ctype: str = "factual") -> ClaimVerdict:
    return ClaimVerdict(claim_id=cid, text=f"claim {cid}", span=(0, 5), claim_type=ctype, label=label)


class ScriptedProvider(OfflineProvider):
    """Returns canned JSON so verdict handling can be tested precisely."""

    def __init__(self, claims=None, verdicts=None):
        self._claims = claims
        self._verdicts = verdicts

    async def complete_json(self, *, model, system, user, schema, max_tokens=4000):
        props = (schema or {}).get("properties", {})
        if "claims" in props and self._claims is not None:
            return {"claims": self._claims}, Usage()
        if "verdicts" in props and self._verdicts is not None:
            return {"verdicts": self._verdicts}, Usage()
        return await super().complete_json(
            model=model, system=system, user=user, schema=schema, max_tokens=max_tokens
        )


class TestIndependentVerifier:
    def test_rejects_self_verification(self):
        """A model grading its own output inflates the score. This is a
        correctness constraint on the metric, so it raises rather than warns."""
        with pytest.raises(SelfVerificationError, match="same model"):
            assert_independent_verifier("claude-opus-5", "claude-opus-5")

    def test_allows_a_different_model(self):
        assert_independent_verifier("claude-opus-5", "claude-sonnet-5") is None

    def test_production_defaults_are_independent(self):
        assert settings.model_answer != settings.model_verify


class TestFailClosed:
    async def test_no_verdicts_means_unavailable_not_perfect(self):
        """Offline mode cannot judge entailment. It must report 'not checked',
        never a fabricated 0.94."""
        result = await verify_answer(
            provider=OfflineProvider(),
            answer_text="The cap is twelve months of fees. IP claims are excluded.",
            evidence=[ev()],
        )
        assert result.faithfulness is None
        assert result.unavailable_reason
        assert result.claims, "claims are still extracted so the UI can show them"

    async def test_supported_without_a_quote_is_downgraded(self):
        """The verifier's own rule: no decisive quote means unsupported. A
        verdict that cannot cite its evidence is not evidence."""
        p = ScriptedProvider(
            claims=[{"id": 0, "text": "The cap is twelve months.", "span": [0, 24], "type": "factual"}],
            verdicts=[{"claim_id": 0, "label": "supported", "evidence_chunk_ids": [1],
                       "quote": "   ", "reason": "looks right"}],
        )
        result = await verify_answer(
            provider=p, answer_text="The cap is twelve months.", evidence=[ev()]
        )
        assert result.claims[0].label == "unsupported"
        assert result.faithfulness == 0.0

    async def test_rejects_attribution_to_chunks_we_never_sent(self):
        """A verdict citing a hallucinated chunk id is not evidence of anything."""
        p = ScriptedProvider(
            claims=[{"id": 0, "text": "The cap is twelve months.", "span": [0, 24], "type": "factual"}],
            verdicts=[{"claim_id": 0, "label": "supported", "evidence_chunk_ids": [1, 9999],
                       "quote": "shall not exceed twelve months", "reason": "stated"}],
        )
        result = await verify_answer(
            provider=p, answer_text="The cap is twelve months.", evidence=[ev(chunk_id=1)]
        )
        assert result.claims[0].evidence_chunk_ids == [1]

    async def test_malformed_claim_is_dropped_not_assumed_supported(self):
        p = ScriptedProvider(
            claims=[
                {"id": "not-an-int", "text": "x", "span": [0, 1], "type": "factual"},
                {"id": 1, "text": "The cap is twelve months.", "span": [0, 24], "type": "factual"},
            ],
            verdicts=[{"claim_id": 1, "label": "supported", "evidence_chunk_ids": [1],
                       "quote": "twelve months", "reason": "stated"}],
        )
        result = await verify_answer(provider=p, answer_text="text", evidence=[ev()])
        assert len(result.claims) == 1

    async def test_empty_answer_is_unavailable(self):
        result = await verify_answer(provider=OfflineProvider(), answer_text="  ", evidence=[ev()])
        assert result.faithfulness is None
        assert result.unavailable_reason == "empty answer"


class TestFaithfulnessScore:
    def test_partial_counts_half(self):
        claims = [claim(0, "supported"), claim(1, "partially_supported"), claim(2, "unsupported")]
        assert compute_faithfulness(claims) == pytest.approx((1 + 0.5) / 3)

    def test_all_supported(self):
        assert compute_faithfulness([claim(0, "supported"), claim(1, "supported")]) == 1.0

    def test_meta_claims_are_excluded(self):
        """'The documents do not address X' is not checkable against sources."""
        claims = [claim(0, "supported"), claim(1, "unsupported", ctype="meta")]
        assert compute_faithfulness(claims) == 1.0

    def test_only_meta_claims_has_no_score(self):
        """An answer made entirely of meta statements has no faithfulness.
        Reporting 1.0 would be a lie by omission."""
        assert compute_faithfulness([claim(0, "supported", ctype="meta")]) is None

    def test_no_claims(self):
        assert compute_faithfulness([]) is None


class TestUncitedSpans:
    def test_flags_substantive_prose_with_no_citation(self):
        blocks = [
            {"type": "text", "text": "The cap is twelve months of fees.", "citations": [{"x": 1}]},
            {"type": "text",
             "text": "This means your exposure is strictly bounded in every scenario.",
             "citations": []},
        ]
        assert find_uncited_spans(blocks) == 1

    def test_ignores_short_connectives(self):
        assert find_uncited_spans([{"type": "text", "text": "However,", "citations": []}]) == 0

    def test_costs_no_api_call(self):
        assert find_uncited_spans([]) == 0


class TestVerificationGate:
    def test_uncited_spans_always_trigger(self):
        assert should_verify("short answer", uncited_spans=1, sample=0.0) is True

    def test_long_answers_always_trigger(self):
        long_answer = " ".join(["word"] * (settings.verify_always_over_words + 10))
        assert should_verify(long_answer, uncited_spans=0, sample=0.0) is True

    def test_short_clean_answers_can_be_sampled_out(self):
        assert should_verify("A short clean answer.", uncited_spans=0, sample=0.0) is False

    def test_full_sampling_always_verifies(self):
        assert should_verify("A short clean answer.", uncited_spans=0, sample=1.0) is True
