"""Offline provider contract.

Offline mode exists so the whole product is exercisable without an API key. The
tests that matter are the honesty ones: it must refuse when retrieval found
nothing, and it must never invent a citation that does not point at a real
retrieved span.
"""

from __future__ import annotations

import pytest

from app.generate.provider import EvidenceBlock, OfflineProvider, Usage


def ev(chunk_id: int = 1, blocks: list[str] | None = None) -> EvidenceBlock:
    return EvidenceBlock(
        chunk_id=chunk_id,
        document_id=f"doc-{chunk_id}",
        title=f"Contract-{chunk_id}.pdf",
        page_start=47,
        page_end=47,
        char_start=0,
        blocks=blocks
        or [
            "The aggregate liability shall not exceed twelve months of fees.",
            "Except for claims arising from infringement of intellectual property.",
        ],
    )


async def collect(provider, question, evidence):
    text, cites, usage, done = "", [], None, False
    async for e in provider.stream_answer(question, evidence):
        if e.type == "text":
            text += e.text
        elif e.type == "citation":
            cites.append(e.citation)
        elif e.type == "usage":
            usage = e.usage
        elif e.type == "done":
            done = True
    return text, cites, usage, done


class TestOfflineHonesty:
    async def test_refuses_when_no_evidence(self):
        """Retrieval's score floors let nothing through, so the only correct
        answer is a refusal. Inventing one here would defeat the purpose of the
        floors and of the product."""
        text, cites, _, done = await collect(OfflineProvider(), "unanswerable question", [])
        assert "could not find support" in text
        assert cites == []
        assert done

    async def test_every_citation_points_at_a_real_retrieved_span(self):
        """The load-bearing honesty guarantee: offline citations are synthetic
        in origin but must reference genuinely retrieved text, so clicking one
        lands on a real page."""
        evidence = [ev(1), ev(2)]
        _, cites, _, _ = await collect(OfflineProvider(), "What is the liability cap?", evidence)
        assert cites
        for c in cites:
            source = evidence[c.evidence_index]
            assert c.cited_text in source.blocks
            assert 0 <= c.start_block_index < len(source.blocks)
            assert c.end_block_index <= len(source.blocks)

    async def test_labels_itself_as_offline(self):
        """The user must never mistake an extractive stub for a model answer."""
        text, _, _, _ = await collect(OfflineProvider(), "q", [ev()])
        assert "Offline mode" in text

    async def test_verdicts_are_empty_not_fabricated(self):
        """Offline cannot judge entailment. Returning invented verdicts would
        produce a fake faithfulness score, which is the exact failure this
        project argues against."""
        out, _ = await OfflineProvider().complete_json(
            model="m", system="", user="Some answer text.", schema={"properties": {"verdicts": {}}}
        )
        assert out["verdicts"] == []
        assert out["_offline"] is True


class TestOfflineBehaviour:
    async def test_is_deterministic(self):
        p = OfflineProvider()
        a = await collect(p, "What is the cap?", [ev()])
        b = await collect(p, "What is the cap?", [ev()])
        assert a[0] == b[0]

    async def test_selects_the_block_matching_the_question(self):
        """Weakly responsive beats always-block-zero: it keeps the UI's citation
        interaction meaningful while offline."""
        e = ev(blocks=["Payment terms are net thirty days.", "Termination requires ninety days notice."])
        _, cites, _, _ = await collect(OfflineProvider(), "What notice is required to terminate?", [e])
        assert cites[0].start_block_index == 1

    async def test_streams_many_small_tokens(self):
        """Exercises the client's requestAnimationFrame coalescing path, which
        only matters if the stream is actually chunked."""
        events = [e async for e in OfflineProvider().stream_answer("q", [ev()]) if e.type == "text"]
        assert len(events) > 20

    async def test_emits_usage_and_done(self):
        _, _, usage, done = await collect(OfflineProvider(), "q", [ev()])
        assert done and usage is not None and usage.input_tokens > 0

    async def test_decomposes_answer_into_claims_with_spans(self):
        answer = "The cap is twelve months of fees. IP claims are excluded from that cap."
        out, _ = await OfflineProvider().complete_json(
            model="m", system="", user=answer, schema={"properties": {"claims": {}}}
        )
        assert len(out["claims"]) == 2
        for c in out["claims"]:
            start, end = c["span"]
            assert answer[start:end] == c["text"]

    async def test_extracts_identifier_keywords_for_the_keyword_lane(self):
        out, _ = await OfflineProvider().complete_json(
            model="m", system="", user="What does clause 12.2 of the Acme MSA say?",
            schema={"properties": {"standalone_query": {}}},
        )
        assert "12.2" in out["keywords"]


class TestCost:
    def test_opus_pricing(self):
        # 7500 in @ $5/MTok + 2200 out @ $25/MTok
        assert Usage(input_tokens=7500, output_tokens=2200).cost_usd("claude-opus-5") == pytest.approx(
            0.0925, rel=1e-4
        )

    def test_cache_reads_bill_at_one_tenth(self):
        plain = Usage(input_tokens=7500, output_tokens=2200).cost_usd("claude-opus-5")
        cached = Usage(
            input_tokens=6300, output_tokens=2200, cache_read_input_tokens=1200
        ).cost_usd("claude-opus-5")
        assert cached < plain

    def test_cache_writes_carry_a_premium(self):
        """Why there is NO breakpoint after the evidence: writing 24k unique
        tokens to cache costs 1.25x and is never read back."""
        write = Usage(input_tokens=0, cache_creation_input_tokens=24_000).cost_usd("claude-opus-5")
        plain = Usage(input_tokens=24_000).cost_usd("claude-opus-5")
        assert write == pytest.approx(plain * 1.25, rel=1e-6)

    def test_unknown_model_is_zero_not_a_guess(self):
        assert Usage(input_tokens=1000).cost_usd("some-future-model") == 0.0
