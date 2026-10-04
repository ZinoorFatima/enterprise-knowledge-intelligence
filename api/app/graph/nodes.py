"""Pipeline stages as LangGraph nodes.

Each node reads the state, does one job, and writes only its own keys. That
discipline is what makes the state a faithful audit trail: no node overwrites
another's output, so the retrieval inspector shows what actually happened rather
than a last-writer-wins summary.
"""

from __future__ import annotations

import re
import time
from typing import Any

from langchain_core.runnables import RunnableConfig

from app.config import settings
from app.generate.provider import EvidenceBlock
from app.generate.verify import find_uncited_spans, should_verify, verify_answer
from app.graph.state import (
    Filters,
    PipelineDeps,
    PipelineState,
    RewriteResult,
    VerificationResult,
)
from app.retrieve.fuse import (
    apply_score_floors,
    assign_rerank_deltas,
    batch_by_token_budget,
    fuse_lanes,
)

REWRITE_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "required": ["standalone_query", "subqueries", "hyde", "keywords", "intent"],
    "properties": {
        "standalone_query": {"type": "string"},
        "subqueries": {"type": "array", "items": {"type": "string"}, "maxItems": 4},
        "hyde": {"type": "string"},
        "keywords": {"type": "array", "items": {"type": "string"}, "maxItems": 12},
        "filters": {"type": "object"},
        "intent": {
            "enum": [
                "lookup", "comparison", "aggregation",
                "definition", "multi_hop", "no_retrieval",
            ]
        },
    },
}

REWRITE_SYSTEM = """You rewrite user questions for a hybrid (keyword + vector)
retrieval system over an enterprise document corpus.

Produce:
- standalone_query: the question resolved against the conversation. Replace every
  pronoun and elliptical reference with its explicit referent. If the question is
  already standalone, return it unchanged.
- subqueries: 2-4 decompositions. For a comparison or multi-hop question, one per
  entity or hop. For a simple lookup, one or two vocabulary variants using
  terminology the source documents would plausibly use. Never invent facts.
- hyde: a 60-120 word passage written as if excerpted from a document that
  answers the question. It will be embedded, never shown to a user, and never
  fed to keyword search.
- keywords: exact tokens that must appear in a relevant passage - identifiers,
  clause numbers, part numbers, proper nouns, statute cites. Empty if none.
- intent: lookup | comparison | aggregation | definition | multi_hop | no_retrieval
"""

_ANAPHORA = re.compile(
    r"\b(it|its|they|them|their|that|this|those|these|the above|the same|he|she)\b",
    re.IGNORECASE,
)


def _deps(config: RunnableConfig) -> PipelineDeps:
    deps = (config.get("configurable") or {}).get("deps")
    if deps is None:
        raise RuntimeError("PipelineDeps missing; pass via config={'configurable': {'deps': ...}}")
    return deps


def needs_rewrite(question: str, history: list[dict[str, Any]] | None) -> bool:
    """Skip gate. Worth 400-900ms on roughly 40% of queries.

    A rewrite earns its latency when there is conversational context to resolve,
    or when the question is too terse to embed well / too compound to embed as
    one vector. A plain mid-length standalone question does not need it.
    """
    if history:
        return True  # anaphora is near-guaranteed in a follow-up
    if _ANAPHORA.search(question):
        return True
    n = len(question.split())
    return n < 5 or n > 28


# ───────────────────────────────────────────────────────────── nodes


async def rewrite_node(state: PipelineState, config: RunnableConfig) -> dict:
    t0 = time.perf_counter()
    deps = _deps(config)
    question = state["question"]
    history = state.get("history") or []

    rendered = "\n".join(
        f"{m.get('role', 'user')}: {m.get('content', '')}" for m in history[-6:]
    )
    rendered = f"{rendered}\nuser: {question}" if rendered else question

    try:
        out, _ = await deps.provider.complete_json(
            model=settings.model_rewrite,
            system=REWRITE_SYSTEM,
            user=rendered,
            schema=REWRITE_SCHEMA,
        )
        rewrite = RewriteResult(
            standalone_query=out.get("standalone_query") or question,
            subqueries=[s for s in (out.get("subqueries") or []) if s][:4],
            hyde=out.get("hyde") or "",
            keywords=[k for k in (out.get("keywords") or []) if k][:12],
            intent=out.get("intent") or "lookup",
        )
    except Exception as exc:  # a failed rewrite must not fail the query
        return {
            "rewrite": RewriteResult(standalone_query=question, skipped=True),
            "errors": [f"rewrite failed, using original question: {exc}"],
            "stage_latency_ms": {"rewrite": (time.perf_counter() - t0) * 1000},
        }

    return {
        "rewrite": rewrite,
        "stage_latency_ms": {"rewrite": (time.perf_counter() - t0) * 1000},
    }


async def skip_rewrite_node(state: PipelineState) -> dict:
    """Records that the gate fired, so the inspector can show 'rewriting off'
    rather than leaving the user to wonder whether it silently failed."""
    return {
        "rewrite": RewriteResult(standalone_query=state["question"], skipped=True),
        "stage_latency_ms": {"rewrite": 0.0},
    }


async def retrieve_node(state: PipelineState, config: RunnableConfig) -> dict:
    """Fan out across query variants. Lanes are built so that each one plays to
    a different retrieval strength."""
    t0 = time.perf_counter()
    deps = _deps(config)
    rewrite = state.get("rewrite") or RewriteResult(standalone_query=state["question"])
    filters = state.get("filters") or Filters()

    # (lane name, text, w_bm25, w_vec)
    plans: list[tuple[str, str, float, float]] = [
        ("original", rewrite.standalone_query or state["question"], settings.w_bm25, settings.w_vec)
    ]
    for i, sq in enumerate(rewrite.subqueries[:3]):
        plans.append((f"subquery_{i}", sq, settings.w_bm25, settings.w_vec))
    if rewrite.hyde:
        # Vector-only. Feeding a synthetic passage into keyword search injects
        # hallucinated terms, and an invented clause number will dominate BM25.
        plans.append(("hyde", rewrite.hyde, 0.0, settings.w_vec))
    if rewrite.keywords:
        # Keyword-only. This is what rescues "what does clause 12.2 say" from a
        # vector lane that considers all clause numbers roughly equidistant.
        plans.append(("keyword", " OR ".join(rewrite.keywords), settings.w_bm25, 0.0))

    # One batched embedding call for every lane that needs a vector.
    needs_vec = [p for p in plans if p[3] > 0]
    vectors: dict[str, list[float]] = {}
    if needs_vec:
        try:
            embedded = await deps.embedder.embed([p[1] for p in needs_vec])
            vectors = {p[0]: v for p, v in zip(needs_vec, embedded)}
        except Exception as exc:
            return {
                "lane_results": {},
                "lane_sizes": {},
                "errors": [f"embedding failed: {exc}"],
                "stage_latency_ms": {"retrieve": (time.perf_counter() - t0) * 1000},
            }

    lane_results: dict[str, list] = {}
    errors: list[str] = []
    for name, text, w_bm25, w_vec in plans:
        try:
            lane_results[name] = await deps.retriever.search(
                org_id=deps.org_id,
                query_text=text if w_bm25 > 0 else "",
                query_embedding=vectors.get(name) if w_vec > 0 else None,
                filters=filters,
                w_bm25=w_bm25,
                w_vec=w_vec,
                lane_k=settings.lane_k,
                out_n=settings.fuse_out_n,
            )
        except Exception as exc:
            errors.append(f"lane {name} failed: {exc}")
            lane_results[name] = []

    return {
        "lane_results": lane_results,
        "lane_sizes": {k: len(v) for k, v in lane_results.items()},
        "errors": errors,
        "stage_latency_ms": {"retrieve": (time.perf_counter() - t0) * 1000},
    }


async def fuse_node(state: PipelineState) -> dict:
    t0 = time.perf_counter()
    weights = {
        "original": settings.lane_weight_original,
        "hyde": settings.lane_weight_hyde,
        "keyword": settings.lane_weight_keyword,
    }
    lane_results = state.get("lane_results") or {}
    for name in lane_results:
        weights.setdefault(name, settings.lane_weight_subquery)

    fused = fuse_lanes(
        lane_results, weights, k=settings.rrf_k, out_n=settings.fuse_out_n
    )
    return {"fused": fused, "stage_latency_ms": {"fuse": (time.perf_counter() - t0) * 1000}}


async def rerank_node(state: PipelineState, config: RunnableConfig) -> dict:
    """Cross-encoder scoring, then the two score floors.

    The floors are the main structural defence against hallucination-by-context-
    stuffing: a question the corpus cannot answer must arrive at generation with
    little or no evidence, so the model can correctly refuse.
    """
    t0 = time.perf_counter()
    deps = _deps(config)
    fused = list(state.get("fused") or [])
    if not fused:
        return {
            "reranked": [],
            "dropped_by_floor": 0,
            "stage_latency_ms": {"rerank": (time.perf_counter() - t0) * 1000},
        }

    query = (state.get("rewrite") or RewriteResult()).standalone_query or state["question"]
    passages = [c.text[:1800] for c in fused]

    try:
        # Length-bucketed batching: padding waste on mixed-length batches is
        # routinely 35-45%, and sorting by length recovers most of it.
        for batch in batch_by_token_budget(passages, budget=settings.rerank_batch_tokens):
            scores = await deps.reranker.rerank(query, [passages[i] for i in batch])
            for idx, score in zip(batch, scores):
                fused[idx].rerank_score = float(score)
    except Exception as exc:
        # Fall back to RRF order rather than failing the query outright.
        for c in fused:
            c.rerank_score = c.rrf_score
        ranked = sorted(fused, key=lambda c: -(c.rerank_score or 0))[: settings.rerank_top_n]
        for c in ranked:
            c.in_context = True
        return {
            "reranked": ranked,
            "dropped_by_floor": 0,
            "errors": [f"rerank failed, fell back to RRF order: {exc}"],
            "stage_latency_ms": {"rerank": (time.perf_counter() - t0) * 1000},
        }

    ranked = sorted(fused, key=lambda c: -(c.rerank_score or 0))[: settings.rerank_top_n]
    assign_rerank_deltas(fused, ranked)
    kept = apply_score_floors(
        ranked,
        absolute=settings.rerank_score_floor,
        relative=settings.rerank_relative_floor,
    )
    for c in kept:
        c.in_context = True

    return {
        "reranked": kept,
        "dropped_by_floor": len(ranked) - len(kept),
        "stage_latency_ms": {"rerank": (time.perf_counter() - t0) * 1000},
    }


_SENT = re.compile(r"(?<=[.!?])\s+")


async def assemble_node(state: PipelineState) -> dict:
    """Build evidence units under a token budget.

    Hard cap even though the model's window is far larger: cost is linear in
    input tokens, and answer quality degrades past ~20 well-ranked chunks as
    distractors dilute attention.
    """
    t0 = time.perf_counter()
    reranked = state.get("reranked") or []
    budget = settings.max_context_tokens
    used = 0
    evidence: list[EvidenceBlock] = []

    # Reading order within a document beats score order for multi-document
    # reasoning, and makes the citation trail legible to a human checking it.
    ordered = sorted(reranked, key=lambda c: (c.document_id, c.ordinal))
    by_doc_best = {}
    for c in reranked:
        prev = by_doc_best.get(c.document_id)
        if prev is None or (c.rerank_score or 0) > prev:
            by_doc_best[c.document_id] = c.rerank_score or 0
    ordered.sort(key=lambda c: (-by_doc_best[c.document_id], c.document_id, c.ordinal))

    for c in ordered:
        if used + c.token_count > budget:
            break
        # 2-3 sentence sub-blocks: the block is the minimal citable unit, so
        # smaller blocks give tighter cited_text and a more precise highlight.
        sentences = [s for s in _SENT.split(c.text) if s.strip()]
        blocks, spans, cursor = [], [], c.char_start
        for i in range(0, len(sentences), 2):
            grp = " ".join(sentences[i : i + 2])
            blocks.append(grp)
            spans.append((cursor, cursor + len(grp)))
            cursor += len(grp) + 1
        if not blocks:
            blocks, spans = [c.text], [(c.char_start, c.char_end)]

        evidence.append(
            EvidenceBlock(
                chunk_id=c.chunk_id,
                document_id=c.document_id,
                title=c.title,
                page_start=c.page_start,
                page_end=c.page_end,
                char_start=c.char_start,
                blocks=blocks,
                block_spans=spans,
            )
        )
        used += c.token_count

    return {
        "evidence": evidence,
        "stage_latency_ms": {"assemble": (time.perf_counter() - t0) * 1000},
    }


async def generate_node(state: PipelineState, config: RunnableConfig) -> dict:
    t0 = time.perf_counter()
    deps = _deps(config)
    evidence = state.get("evidence") or []

    text_parts: list[str] = []
    citations = []
    usage = None
    try:
        async for ev in deps.provider.stream_answer(
            state["question"], evidence, state.get("history") or []
        ):
            if ev.type == "text":
                text_parts.append(ev.text)
            elif ev.type == "citation" and ev.citation is not None:
                citations.append(ev.citation)
            elif ev.type == "usage":
                usage = ev.usage
            elif ev.type == "error":
                return {
                    "answer_text": "".join(text_parts),
                    "citations": citations,
                    "refused": False,
                    "errors": [f"generation error: {ev.error}"],
                    "stage_latency_ms": {"generate": (time.perf_counter() - t0) * 1000},
                }
    except Exception as exc:
        return {
            "answer_text": "".join(text_parts),
            "citations": citations,
            "errors": [f"generation failed: {exc}"],
            "stage_latency_ms": {"generate": (time.perf_counter() - t0) * 1000},
        }

    out: dict = {
        "answer_text": "".join(text_parts),
        "citations": citations,
        "refused": False,
        "stage_latency_ms": {"generate": (time.perf_counter() - t0) * 1000},
    }
    if usage is not None:
        out["usage"] = usage
    return out


async def refuse_node(state: PipelineState) -> dict:
    """Reached when retrieval cleared nothing above the score floors.

    This is a correct outcome, not an error path. Refusing is the behaviour the
    floors exist to produce, and it is scored as such in the eval harness.
    """
    return {
        "answer_text": (
            "I could not find support for that question in the indexed documents. "
            "Nothing retrieved scored above the relevance threshold, so there is no "
            "evidence to cite."
        ),
        "citations": [],
        "evidence": [],
        "refused": True,
    }


async def verify_node(state: PipelineState, config: RunnableConfig) -> dict:
    t0 = time.perf_counter()
    deps = _deps(config)
    try:
        result = await verify_answer(
            provider=deps.provider,
            answer_text=state.get("answer_text", ""),
            evidence=state.get("evidence") or [],
            blocks=[],
            generator_model=settings.model_answer,
        )
    except Exception as exc:
        return {
            "verification": VerificationResult(
                unavailable_reason=f"verification failed: {exc}",
                verifier_model=settings.model_verify,
            ),
            "errors": [f"verification failed: {exc}"],
            "stage_latency_ms": {"verify": (time.perf_counter() - t0) * 1000},
        }
    return {
        "verification": result,
        "stage_latency_ms": {"verify": (time.perf_counter() - t0) * 1000},
    }


async def skip_verify_node(state: PipelineState) -> dict:
    """Never leave verification silently absent: an answer that was not checked
    must say so, on a product that promises checking."""
    return {
        "verification": VerificationResult(
            unavailable_reason="not selected for verification",
            verifier_model=settings.model_verify,
        )
    }


# ───────────────────────────────────────────────────────────── edges


def route_rewrite(state: PipelineState) -> str:
    if not state.get("want_rewrite", True):
        return "skip_rewrite"
    return "rewrite" if needs_rewrite(state["question"], state.get("history")) else "skip_rewrite"


def route_after_assemble(state: PipelineState) -> str:
    """No evidence means refuse. This edge is the product's honesty guarantee
    expressed as control flow."""
    return "generate" if state.get("evidence") else "refuse"


def route_verify(state: PipelineState) -> str:
    if not state.get("want_verify", True) or state.get("refused"):
        return "skip_verify"
    uncited = find_uncited_spans([])
    return "verify" if should_verify(state.get("answer_text", ""), uncited) else "skip_verify"
