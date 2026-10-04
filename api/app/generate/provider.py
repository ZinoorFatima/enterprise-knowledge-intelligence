"""The single seam between this system and Claude.

There is exactly ONE place in this codebase that constructs an Anthropic client:
AnthropicProvider._client. Everything else depends on the LLMProvider protocol.

Offline mode is a first-class mode, not a test fixture. With LLM_MODE=offline the
entire retrieval half runs for real -- PDF parsing, OCR, bge-m3 embeddings,
hybrid SQL, cross-encoder reranking are all local and cost nothing -- and only
generation is stubbed. The stub returns well-formed responses whose citations
point at GENUINELY retrieved chunks, so every UI surface (streaming, citation
clicks, page highlighting, the verification panel) is fully exercisable without
an API key.

What offline mode CANNOT do honestly: report measured Faithfulness or Answer
Relevancy, because both are LLM-judged. Answers produced offline are stamped
llm_mode='offline' and any eval run over them is marked provisional. Showing
0.94 for a metric nothing computed is precisely what this project argues against.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import AsyncIterator, Literal, Protocol, Sequence

from app.config import settings

# Anthropic list pricing, USD per million tokens (input, output).
PRICING: dict[str, tuple[float, float]] = {
    "claude-opus-5": (5.00, 25.00),
    "claude-sonnet-5": (2.00, 10.00),
    "claude-haiku-4-5": (1.00, 5.00),
    "claude-opus-4-8": (5.00, 25.00),
}


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0

    def cost_usd(self, model: str) -> float:
        rate_in, rate_out = PRICING.get(model, (0.0, 0.0))
        # Cache reads bill at 0.1x, cache writes at 1.25x.
        billed_in = (
            self.input_tokens
            + self.cache_read_input_tokens * 0.10
            + self.cache_creation_input_tokens * 1.25
        )
        return (billed_in * rate_in + self.output_tokens * rate_out) / 1_000_000


@dataclass
class Citation:
    """A citation as returned by the model, before page resolution.

    Every index here points into a structure WE built, so resolution is index
    arithmetic. No string matching anywhere in the path, and therefore no
    "citation failed to resolve" class of bug.
    """

    evidence_index: int
    start_block_index: int
    end_block_index: int
    cited_text: str


@dataclass
class StreamEvent:
    type: Literal["text", "citation", "usage", "done", "error"]
    text: str = ""
    citation: Citation | None = None
    usage: Usage | None = None
    error: str = ""


@dataclass
class EvidenceBlock:
    """One evidence unit handed to the model.

    Split into 2-3 sentence sub-blocks: the block is the minimal citable unit,
    so smaller blocks mean tighter cited_text and a more precise page highlight.
    """

    chunk_id: int
    document_id: str
    title: str
    page_start: int
    page_end: int
    char_start: int
    blocks: list[str] = field(default_factory=list)
    block_spans: list[tuple[int, int]] = field(default_factory=list)


class LLMProvider(Protocol):
    async def stream_answer(
        self,
        question: str,
        evidence: Sequence[EvidenceBlock],
        history: Sequence[dict] | None = ...,
    ) -> AsyncIterator[StreamEvent]: ...

    async def complete_json(
        self, *, model: str, system: str, user: str, schema: dict, max_tokens: int = ...
    ) -> tuple[dict, Usage]: ...


_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+")
_WORD = re.compile(r"\w+")
_WS = re.compile(r"(\s+)")
_IDENTIFIER = re.compile(r"\b(?:[A-Z][\w-]{2,}|\d+\.\d+|\d{4})\b")


class OfflineProvider:
    """Deterministic, no network, no key. Grounded in real retrieved evidence.

    Determinism matters: the same question over the same corpus must give the
    same answer, so UI work and snapshot tests stay stable.
    """

    name = "offline"

    async def stream_answer(
        self,
        question: str,
        evidence: Sequence[EvidenceBlock],
        history: Sequence[dict] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        if not evidence:
            # The honest path. Retrieval genuinely found nothing above the score
            # floors, so refusal is correct -- and offline mode must model that
            # faithfully rather than inventing an answer.
            msg = (
                "I could not find support for that question in the indexed documents. "
                "Nothing in the corpus scored above the relevance threshold, so there "
                "is no evidence to cite."
            )
            for tok in _tokenize(msg):
                yield StreamEvent("text", text=tok)
            yield StreamEvent(
                "usage",
                usage=Usage(input_tokens=len(question) // 4, output_tokens=len(msg) // 4),
            )
            yield StreamEvent("done")
            return

        # Extractive synthesis over evidence actually retrieved: every sentence
        # emitted is a real span from a real chunk, and every citation marker
        # points at the block it came from.
        plural = "s" if len(evidence) > 1 else ""
        for tok in _tokenize(f"Based on {len(evidence)} retrieved passage{plural}: "):
            yield StreamEvent("text", text=tok)

        shown = list(evidence)[:4]
        for i, ev in enumerate(shown):
            if not ev.blocks:
                continue
            # Pick the block with most lexical overlap with the question, so the
            # stub is at least weakly responsive rather than always block 0.
            bi = _best_block(question, ev.blocks)
            snippet = ev.blocks[bi].strip()
            if len(snippet) > 320:
                snippet = snippet[:317].rsplit(" ", 1)[0] + "..."
            for tok in _tokenize(snippet):
                yield StreamEvent("text", text=tok)
            yield StreamEvent(
                "citation",
                citation=Citation(
                    evidence_index=i,
                    start_block_index=bi,
                    end_block_index=bi + 1,
                    cited_text=ev.blocks[bi],
                ),
            )
            if i < len(shown) - 1:
                yield StreamEvent("text", text=" ")

        yield StreamEvent(
            "text",
            text=(
                "\n\n_Offline mode: retrieval, ranking and citations above are real. "
                "The synthesis is extractive, not model-generated._"
            ),
        )
        in_tok = sum(len(b) for ev in evidence for b in ev.blocks) // 4
        yield StreamEvent("usage", usage=Usage(input_tokens=in_tok, output_tokens=120))
        yield StreamEvent("done")

    async def complete_json(
        self, *, model: str, system: str, user: str, schema: dict, max_tokens: int = 4000
    ) -> tuple[dict, Usage]:
        """Schema-shaped deterministic stubs, keyed off the schema's own fields so
        callers receive a structurally valid object and process it normally."""
        props = (schema or {}).get("properties", {})
        out: dict

        if "standalone_query" in props:
            last = user.strip().splitlines()[-1] if user.strip() else ""
            out = {
                "standalone_query": last,
                "subqueries": [],
                "hyde": "",
                "keywords": _keywords(last),
                "filters": {},
                "intent": "lookup",
            }
        elif "claims" in props:
            sents = [s.strip() for s in _SENT_SPLIT.split(user.strip()) if len(s.strip()) > 15]
            claims: list[dict] = []
            cursor = 0
            for idx, s in enumerate(sents[:20]):
                start = user.find(s, cursor)
                if start < 0:
                    start = cursor
                cursor = start + len(s)
                claims.append(
                    {
                        "id": idx,
                        "text": s,
                        "span": [start, cursor],
                        "type": "meta" if s.lower().startswith("offline mode") else "factual",
                    }
                )
            out = {"claims": claims}
        elif "verdicts" in props:
            # Offline cannot judge entailment. Return nothing rather than
            # fabricating verdicts -- the caller marks faithfulness unavailable.
            out = {"verdicts": []}
        elif "questions" in props:
            out = {"questions": []}
        else:
            out = dict.fromkeys(props)

        out["_offline"] = True
        return out, Usage(input_tokens=len(user) // 4, output_tokens=len(str(out)) // 4)


def _tokenize(text: str) -> list[str]:
    """Word-ish tokens, so the client sees a realistic stream and the rAF
    coalescing path is genuinely exercised."""
    return [t for t in _WS.split(text) if t]


def _best_block(question: str, blocks: Sequence[str]) -> int:
    q = {w for w in _WORD.findall(question.lower()) if len(w) > 3}
    if not q:
        return 0
    scores = [len(q & set(_WORD.findall(b.lower()))) for b in blocks]
    return max(range(len(blocks)), key=lambda i: scores[i])


def _keywords(text: str) -> list[str]:
    """Identifier-ish tokens: clause numbers, part numbers, capitalized terms.
    These drive the keyword-only retrieval lane."""
    return list(dict.fromkeys(_IDENTIFIER.findall(text)))[:8]


def get_provider() -> LLMProvider:
    """The one dispatch point. Flipping LLM_MODE is the only change needed."""
    if settings.is_offline:
        return OfflineProvider()
    from app.generate.anthropic_provider import AnthropicProvider  # deferred: SDK stays optional

    return AnthropicProvider()
