"""Semantic chunking with exact character provenance.

The invariant that matters: every chunk's (char_start, char_end) is an exact
slice of the document's normalized full_text. Pages record offsets in the SAME
coordinate space, so chunk -> page is a bisect, not a heuristic. That exactness
is the entire reason clickable, page-accurate citations are possible.
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass, field
from typing import Callable, Sequence

import numpy as np


@dataclass(frozen=True)
class Sentence:
    text: str
    char_start: int
    char_end: int


@dataclass
class Chunk:
    text: str
    char_start: int
    char_end: int
    token_count: int
    page_start: int = 0
    page_end: int = 0
    section_path: str | None = None
    kind: str = "prose"
    sentence_indices: list[int] = field(default_factory=list)


EmbedFn = Callable[[list[str]], np.ndarray]
CountFn = Callable[[str], int]


def estimate_tokens(text: str) -> int:
    """Cheap estimator for use inside tight loops. The real count_tokens call is
    a network round-trip and is made exactly once, on the finished payload."""
    return max(1, len(text) // 4)


def semantic_chunks(
    sentences: Sequence[Sentence],
    embed_fn: EmbedFn,
    *,
    full_text: str | None = None,
    count_tokens: CountFn = estimate_tokens,
    target_tokens: int = 400,
    min_tokens: int = 128,
    max_tokens: int = 512,
    percentile: float = 95.0,
    window: int = 1,
    overlap_sentences: int = 1,
) -> list[Chunk]:
    """Break text where its meaning turns, then enforce hard size bounds.

    Why a percentile threshold and not an absolute cosine cutoff: adjacent-
    sentence similarity distributions differ wildly by document type. A fixed
    0.25 produces one chunk for a dense contract and one chunk per sentence for
    a slide deck. The percentile is scale-free and transfers. Its cost is that
    it always finds breakpoints even in genuinely homogeneous text, which is
    exactly why the min/max token bounds below are not optional.
    """
    # Always materialize a chunk's text as a SLICE of the source when we have it.
    # Re-joining stripped sentences with " " looks equivalent but is not: real
    # documents separate sentences with newlines and runs of spaces, so the
    # rejoined string drifts from its own char offsets and every citation
    # derived from them points at slightly the wrong span. Verified by
    # tests/test_ingest.py against a real PDF.
    def materialize(start: int, end: int, idxs: list[int]) -> str:
        if full_text is not None:
            return full_text[start:end]
        return " ".join(sentences[i].text for i in idxs)

    n = len(sentences)
    if n == 0:
        return []
    if n == 1:
        s = sentences[0]
        text = materialize(s.char_start, s.char_end, [0])
        return [Chunk(text, s.char_start, s.char_end, count_tokens(text), sentence_indices=[0])]

    # 1. Window each sentence with its neighbours before embedding. "See Section 4."
    #    has no standalone meaning; with neighbours it does. This denoises the
    #    distance signal considerably on legal and technical text.
    windows = [
        " ".join(sentences[j].text for j in range(max(0, i - window), min(n, i + window + 1)))
        for i in range(n)
    ]

    emb = np.asarray(embed_fn(windows), dtype=np.float32)
    if emb.ndim != 2 or emb.shape[0] != n:
        raise ValueError(f"embed_fn returned {emb.shape}, expected ({n}, dim)")
    # Defensive: cosine == dot only if rows are unit length.
    norms = np.linalg.norm(emb, axis=1, keepdims=True)
    emb = emb / np.clip(norms, 1e-12, None)

    # 2. Adjacent distances. A spike means the topic moved.
    dists = 1.0 - np.sum(emb[:-1] * emb[1:], axis=1)

    threshold = float(np.percentile(dists, percentile))
    breakpoints = [i for i, d in enumerate(dists) if d > threshold]

    groups = _split_at(n, breakpoints)
    groups = _merge_small(groups, sentences, count_tokens, min_tokens)
    groups = _split_large(groups, sentences, count_tokens, dists, max_tokens)

    out: list[Chunk] = []
    for gi, g in enumerate(groups):
        if not g:
            continue
        # 3. One sentence of overlap, carried from the previous group — not a
        #    token window. Semantic boundaries already preserve continuity, and
        #    heavy overlap inflates the index, duplicates BM25 hits, and produces
        #    two citations for one fact.
        lead = groups[gi - 1][-overlap_sentences:] if gi > 0 and overlap_sentences else []
        idxs = list(lead) + list(g)
        start = sentences[idxs[0]].char_start
        end = sentences[idxs[-1]].char_end
        text = materialize(start, end, idxs)
        out.append(
            Chunk(
                text=text,
                char_start=start,
                char_end=end,
                token_count=count_tokens(text),
                sentence_indices=idxs,
            )
        )
    return out


def _split_at(n: int, breakpoints: list[int]) -> list[list[int]]:
    """breakpoint i means: cut between sentence i and i+1."""
    groups, start = [], 0
    for b in breakpoints:
        groups.append(list(range(start, b + 1)))
        start = b + 1
    if start < n:
        groups.append(list(range(start, n)))
    return [g for g in groups if g]


def _merge_small(groups, sentences, count_tokens, min_tokens) -> list[list[int]]:
    """Fold an undersized group into its predecessor."""
    out: list[list[int]] = []
    for g in groups:
        text = " ".join(sentences[i].text for i in g)
        if out and count_tokens(text) < min_tokens:
            out[-1].extend(g)
        else:
            out.append(list(g))
    return out


def _split_large(groups, sentences, count_tokens, dists, max_tokens) -> list[list[int]]:
    """Recursively split oversized groups at their WEAKEST internal seam.

    Splitting at an arbitrary token offset would cut mid-topic; splitting at the
    largest interior distance respects the structure the embedding found.
    """
    out: list[list[int]] = []
    stack = [list(g) for g in reversed(groups)]
    while stack:
        g = stack.pop()
        text = " ".join(sentences[i].text for i in g)
        if len(g) < 2 or count_tokens(text) <= max_tokens:
            out.append(g)
            continue
        # Interior seams only: position k means cut between g[k] and g[k+1].
        interior = [(float(dists[g[k]]), k) for k in range(len(g) - 1)]
        _, k = max(interior)
        stack.append(g[k + 1 :])
        stack.append(g[: k + 1])
    return out


# ─────────────────────────────────────────────────────── provenance
def resolve_pages(
    char_start: int, char_end: int, page_starts: Sequence[int], page_numbers: Sequence[int]
) -> tuple[int, int]:
    """Map a character span to the 1-indexed page range containing it. O(log n).

    page_starts must be ascending. A chunk straddling a page break returns
    page_start != page_end, which the UI renders as "pp. 13-14".
    """
    if not page_starts:
        return 0, 0
    i = max(0, bisect.bisect_right(page_starts, char_start) - 1)
    # char_end is exclusive, so probe the last character actually covered.
    j = max(0, bisect.bisect_right(page_starts, max(char_start, char_end - 1)) - 1)
    return page_numbers[i], page_numbers[j]
