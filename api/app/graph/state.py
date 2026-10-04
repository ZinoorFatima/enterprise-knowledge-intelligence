"""Pipeline state, dependencies, and boundary validation.

The LangGraph state is deliberately a record of WHAT HAPPENED, not just the
final answer. Every stage writes its intermediate output into the state and
leaves it there, because the retrieval inspector in the UI renders from exactly
this structure. If the state only carried the answer, the product's central
claim -- that you can check the work -- would have nothing to render.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Annotated, Any, Literal, Protocol, Sequence, TypedDict

from pydantic import BaseModel, Field, field_validator

from app.generate.provider import Citation, EvidenceBlock, LLMProvider, Usage
from app.retrieve.fuse import Candidate

# ───────────────────────────────────────────────────── boundary validation
# Pydantic models validate at the HTTP edge. Everything inside the graph is
# already-validated plain structures, so no node re-parses untrusted input.


class Filters(BaseModel):
    """Metadata filters. These are pushed into BOTH retrieval lanes, never
    applied after fusion -- post-filtering biases the result toward whichever
    lane happened to surface in-filter documents."""

    document_ids: list[str] = Field(default_factory=list, max_length=500)
    tags: list[str] = Field(default_factory=list, max_length=50)
    date_from: date | None = None
    date_to: date | None = None

    @field_validator("date_to")
    @classmethod
    def _range_is_ordered(cls, v: date | None, info):
        start = info.data.get("date_from")
        if v is not None and start is not None and v < start:
            raise ValueError("date_to must not precede date_from")
        return v

    def is_empty(self) -> bool:
        return not (self.document_ids or self.tags or self.date_from or self.date_to)


class AskRequest(BaseModel):
    """Validated at the API edge before anything expensive runs."""

    question: str = Field(min_length=1, max_length=4000)
    thread_id: str | None = None
    filters: Filters = Field(default_factory=Filters)
    top_k: int = Field(default=12, ge=1, le=50)
    rewrite: bool = True
    verify: bool = True

    @field_validator("question")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("question must not be blank")
        return v.strip()


class RewriteResult(BaseModel):
    """Output of the query-understanding call. Validated because it comes back
    from a model and is then used to build SQL lanes."""

    standalone_query: str = ""
    subqueries: list[str] = Field(default_factory=list, max_length=4)
    hyde: str = ""
    keywords: list[str] = Field(default_factory=list, max_length=12)
    intent: Literal[
        "lookup", "comparison", "aggregation", "definition", "multi_hop", "no_retrieval"
    ] = "lookup"
    skipped: bool = False


class ClaimVerdict(BaseModel):
    claim_id: int
    text: str
    span: tuple[int, int]
    claim_type: Literal["factual", "inferential", "meta"] = "factual"
    label: Literal["supported", "partially_supported", "unsupported"] = "unsupported"
    evidence_chunk_ids: list[int] = Field(default_factory=list)
    quote: str = ""
    reason: str = ""


class VerificationResult(BaseModel):
    """Faithfulness measures groundedness in the retrieved context, NOT truth.
    A faithful summary of a wrong source scores 1.0. The UI must say so."""

    claims: list[ClaimVerdict] = Field(default_factory=list)
    faithfulness: float | None = None
    verifier_model: str = ""
    # Set when verification could not run (e.g. offline mode cannot judge
    # entailment). The UI shows "not checked" rather than a fabricated score.
    unavailable_reason: str | None = None
    uncited_spans: int = 0

    @property
    def factual_claims(self) -> list[ClaimVerdict]:
        return [c for c in self.claims if c.claim_type != "meta"]


# ───────────────────────────────────────────────────── retrieval interface


class Retriever(Protocol):
    """The seam between the graph and Postgres.

    The graph depends on this, not on asyncpg, which is what lets the whole
    pipeline be tested without a database.
    """

    async def search(
        self,
        *,
        org_id: str,
        query_text: str,
        query_embedding: Sequence[float] | None,
        filters: Filters,
        w_bm25: float,
        w_vec: float,
        lane_k: int,
        out_n: int,
    ) -> list[Candidate]: ...


class Embedder(Protocol):
    async def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


class Reranker(Protocol):
    async def rerank(self, query: str, passages: Sequence[str]) -> list[float]: ...


@dataclass
class PipelineDeps:
    """Injected through LangGraph's configurable channel, so a test supplies
    fakes and production supplies the real services -- with identical graph code
    in both cases. The eval harness runs the SAME graph, which is what makes its
    numbers mean anything."""

    retriever: Retriever
    embedder: Embedder
    reranker: Reranker
    provider: LLMProvider
    org_id: str = "test-org"


# ───────────────────────────────────────────────────── graph state


def _merge_latency(a: dict[str, float], b: dict[str, float]) -> dict[str, float]:
    """Reducer so concurrent lane nodes can each record timings without
    clobbering one another."""
    return {**a, **b}


def _merge_errors(a: list[str], b: list[str]) -> list[str]:
    return [*a, *b]


class PipelineState(TypedDict, total=False):
    """What happened, stage by stage.

    Keys are written once by the node that owns them. The annotated reducers
    exist only for the fields several nodes touch.
    """

    # Input
    question: str
    filters: Filters
    history: list[dict[str, Any]]
    want_rewrite: bool
    want_verify: bool
    top_k: int

    # Stage 1 - query understanding
    rewrite: RewriteResult

    # Stage 2-3 - retrieval, per lane then fused
    lane_results: dict[str, list[Candidate]]
    lane_sizes: dict[str, int]
    fused: list[Candidate]

    # Stage 4 - reranking
    reranked: list[Candidate]
    dropped_by_floor: int

    # Stage 5 - context assembly
    evidence: list[EvidenceBlock]

    # Stage 6 - generation
    answer_text: str
    citations: list[Citation]
    refused: bool

    # Stage 7 - validation
    verification: VerificationResult

    # Bookkeeping
    stage_latency_ms: Annotated[dict[str, float], _merge_latency]
    errors: Annotated[list[str], _merge_errors]
    usage: Usage


@dataclass
class PipelineResult:
    """Flattened view the API layer serializes. Keeps the HTTP schema from
    leaking into graph internals."""

    question: str
    answer_text: str
    citations: list[Citation] = field(default_factory=list)
    evidence: list[EvidenceBlock] = field(default_factory=list)
    reranked: list[Candidate] = field(default_factory=list)
    fused: list[Candidate] = field(default_factory=list)
    lane_sizes: dict[str, int] = field(default_factory=dict)
    rewrite: RewriteResult | None = None
    verification: VerificationResult | None = None
    refused: bool = False
    stage_latency_ms: dict[str, float] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)

    @classmethod
    def from_state(cls, state: PipelineState, question: str) -> PipelineResult:
        return cls(
            question=question,
            answer_text=state.get("answer_text", ""),
            citations=list(state.get("citations", [])),
            evidence=list(state.get("evidence", [])),
            reranked=list(state.get("reranked", [])),
            fused=list(state.get("fused", [])),
            lane_sizes=dict(state.get("lane_sizes", {})),
            rewrite=state.get("rewrite"),
            verification=state.get("verification"),
            refused=bool(state.get("refused", False)),
            stage_latency_ms=dict(state.get("stage_latency_ms", {})),
            errors=list(state.get("errors", [])),
            usage=state.get("usage", Usage()),
        )
