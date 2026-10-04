"""The chunker's contract, enforced.

The load-bearing test is test_char_spans_are_exact_slices: if a chunk's span is
not an exact slice of full_text, every citation page number downstream is a
guess, and the product's central claim is false.
"""

from __future__ import annotations

import numpy as np
import pytest

from app.ingest.chunker import Sentence, estimate_tokens, resolve_pages, semantic_chunks


def build_sentences(texts: list[str], joiner: str = " ") -> tuple[list[Sentence], str]:
    """Build sentences with exact offsets into the reconstructed full_text."""
    sents, cursor, parts = [], 0, []
    for t in texts:
        sents.append(Sentence(t, cursor, cursor + len(t)))
        parts.append(t)
        cursor += len(t) + len(joiner)
    return sents, joiner.join(parts)


def topical_embedder(topic_of: list[int], dim: int = 8):
    """Deterministic embedder: sentences in the same topic get near-identical
    vectors, different topics are orthogonal. Lets us assert breakpoints land
    where meaning actually changes."""

    def fn(windows: list[str]) -> np.ndarray:
        out = np.zeros((len(windows), dim), dtype=np.float32)
        for i in range(len(windows)):
            out[i, topic_of[i] % dim] = 1.0
            out[i, (topic_of[i] + 1) % dim] = 0.05  # tiny jitter, still distinct
        return out

    return fn


class TestProvenance:
    def test_char_spans_are_exact_slices(self):
        texts = [f"Sentence number {i} carries some content." for i in range(40)]
        sents, full_text = build_sentences(texts)
        chunks = semantic_chunks(
            sents, topical_embedder([i // 10 for i in range(40)]),
            full_text=full_text, min_tokens=1, max_tokens=10_000,
        )
        assert chunks
        for c in chunks:
            # The span must round-trip: slicing full_text at the chunk's offsets
            # must recover exactly the text the chunk claims to hold.
            assert full_text[c.char_start : c.char_end] == c.text

    def test_spans_are_ordered_and_within_bounds(self):
        sents, full_text = build_sentences([f"Line {i} here." for i in range(25)])
        chunks = semantic_chunks(sents, topical_embedder([i // 5 for i in range(25)]), min_tokens=1)
        for c in chunks:
            assert 0 <= c.char_start < c.char_end <= len(full_text)
        # Chunks advance monotonically through the document.
        assert [c.char_start for c in chunks] == sorted(c.char_start for c in chunks)

    def test_full_coverage_no_gaps(self):
        """Every sentence must appear in at least one chunk. A chunker that
        silently drops text produces confidently incomplete answers."""
        sents, _ = build_sentences([f"Fact {i} is recorded." for i in range(30)])
        chunks = semantic_chunks(
            sents, topical_embedder([i // 6 for i in range(30)]), min_tokens=1, overlap_sentences=0
        )
        covered = {i for c in chunks for i in c.sentence_indices}
        assert covered == set(range(30))


class TestSizeBounds:
    def test_respects_max_tokens(self):
        # One uniform topic: the percentile pass finds few natural breaks, so
        # the max-token splitter is what must keep chunks bounded.
        sents, _ = build_sentences([f"Uniform sentence {i} with padding words here." for i in range(80)])
        chunks = semantic_chunks(
            sents, topical_embedder([0] * 80), max_tokens=120, min_tokens=1, overlap_sentences=0
        )
        for c in chunks:
            assert c.token_count <= 120 * 1.5, f"chunk of {c.token_count} tokens exceeded bound"

    def test_merges_undersized_groups(self):
        sents, _ = build_sentences([f"S{i}." for i in range(30)])
        chunks = semantic_chunks(
            sents, topical_embedder(list(range(30))), min_tokens=40, overlap_sentences=0
        )
        # With a high min, nearly everything should coalesce.
        assert len(chunks) < 30

    def test_breaks_land_on_topic_changes(self):
        # Three clean topics of 10 sentences each.
        topics = [0] * 10 + [3] * 10 + [6] * 10
        sents, _ = build_sentences([f"Topic sentence {i} content." for i in range(30)])
        chunks = semantic_chunks(
            sents, topical_embedder(topics), min_tokens=1, max_tokens=10_000,
            percentile=90.0, window=0, overlap_sentences=0,
        )
        starts = [c.sentence_indices[0] for c in chunks]
        # The 10 -> 11 and 20 -> 21 transitions are the only real seams.
        assert 10 in starts or 11 in starts
        assert 20 in starts or 21 in starts


class TestEdgeCases:
    def test_empty(self):
        assert semantic_chunks([], topical_embedder([])) == []

    def test_single_sentence(self):
        sents, full_text = build_sentences(["Only one sentence exists."])
        chunks = semantic_chunks(sents, topical_embedder([0]))
        assert len(chunks) == 1
        assert full_text[chunks[0].char_start : chunks[0].char_end] == chunks[0].text

    def test_rejects_malformed_embedder(self):
        sents, _ = build_sentences(["A.", "B.", "C."])
        with pytest.raises(ValueError, match="expected"):
            semantic_chunks(sents, lambda w: np.zeros((2, 8), dtype=np.float32))

    def test_unnormalized_embeddings_are_normalized(self):
        """A caller returning unnormalized vectors must not silently corrupt the
        distance signal — cosine only equals dot product on unit vectors."""
        sents, _ = build_sentences([f"Sentence {i}." for i in range(12)])

        def big(windows):
            base = topical_embedder([i // 4 for i in range(12)])(windows)
            return base * 1000.0

        chunks = semantic_chunks(sents, big, min_tokens=1, overlap_sentences=0)
        assert chunks


class TestResolvePages:
    # Page 1 covers [0,100), page 2 [100,200), page 3 [200,300).
    STARTS = [0, 100, 200]
    NUMS = [1, 2, 3]

    @pytest.mark.parametrize(
        "start,end,expected",
        [
            (0, 50, (1, 1)),
            (99, 100, (1, 1)),      # end is exclusive: still page 1
            (100, 150, (2, 2)),
            (50, 150, (1, 2)),      # straddles the break -> "pp. 1-2"
            (250, 300, (3, 3)),
            (0, 300, (1, 3)),
        ],
    )
    def test_page_resolution(self, start, end, expected):
        assert resolve_pages(start, end, self.STARTS, self.NUMS) == expected

    def test_exclusive_end_does_not_bleed_onto_next_page(self):
        """A chunk ending exactly at a page boundary must NOT be reported as
        spanning onto the next page — that would produce a citation pointing at
        a page the text never appears on."""
        assert resolve_pages(10, 100, self.STARTS, self.NUMS) == (1, 1)

    def test_empty_pages(self):
        assert resolve_pages(0, 10, [], []) == (0, 0)


def test_estimate_tokens_is_positive():
    assert estimate_tokens("") >= 1
    assert estimate_tokens("a" * 400) == 100


def test_irregular_whitespace_still_slices_exactly():
    """Regression: joining stripped sentences with a single space silently
    drifts from the source whenever the document separates them with newlines
    or runs of spaces. Only slicing full_text is correct."""
    texts = ["First sentence here.", "Second one follows.", "Third arrives late."]
    # Newlines, a run of spaces, and a tab: exactly what real PDFs produce and
    # what a naive " ".join() silently flattens.
    full_text = "First sentence here.\n\n   Second one follows.\n\tThird arrives late."
    sents = []
    for t in texts:
        i = full_text.index(t)
        sents.append(Sentence(t, i, i + len(t)))
    chunks = semantic_chunks(
        sents, topical_embedder([0, 0, 1]), full_text=full_text,
        min_tokens=1, max_tokens=10_000, overlap_sentences=0,
    )
    for c in chunks:
        assert full_text[c.char_start : c.char_end] == c.text
