"""Question answering through the LangGraph pipeline.

Two shapes of the same pipeline:

  POST /v1/ask         - buffered JSON, for the eval harness and for callers
                         that want the whole trace at once.
  POST /v1/ask/stream  - SSE, for the UI.

The stream is not only about tokens. The highest-leverage perceived-latency fix
in the system is emitting the `retrieval` event as soon as ranking finishes, so
source cards appear while generation is still running. Nothing is faked: every
event carries work that has actually completed.
"""

from __future__ import annotations

import json
from typing import AsyncIterator

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from app.core.security import Principal, PrincipalDep
from app.db.session import PgRetriever
from app.generate.provider import get_provider
from app.graph.pipeline import run_pipeline
from app.graph.state import AskRequest, PipelineDeps, PipelineResult

router = APIRouter(tags=["ask"])


def _deps(request: Request, principal: Principal) -> PipelineDeps:
    from app.retrieve.embedder import get_embedder, get_reranker

    embedder = request.app.state.embedder or get_embedder()
    request.app.state.embedder = embedder
    return PipelineDeps(
        retriever=PgRetriever(request.app.state.pool),
        embedder=embedder,
        reranker=get_reranker(),
        provider=get_provider(),
        org_id=principal.org_id,
    )


def _serialize(out: PipelineResult) -> dict:
    return {
        "answer": out.answer_text,
        "refused": out.refused,
        "citations": [
            {
                "marker": i + 1,
                "evidence_index": c.evidence_index,
                "document_id": out.evidence[c.evidence_index].document_id,
                "title": out.evidence[c.evidence_index].title,
                "page": out.evidence[c.evidence_index].page_start,
                "cited_text": c.cited_text,
            }
            for i, c in enumerate(out.citations)
            if c.evidence_index < len(out.evidence)
        ],
        # The inspector renders from stored truth, never a second retrieval run,
        # so what the user inspects is what produced the answer.
        "retrieval": {
            "lanes": out.lane_sizes,
            "fused": len(out.fused),
            "reranked": [
                {
                    "chunk_id": c.chunk_id,
                    "title": c.title,
                    "page": c.page_start,
                    "bm25_rank": c.bm25_rank,
                    "vec_rank": c.vec_rank,
                    "rrf": c.rrf_score,
                    "rerank": c.rerank_score,
                    "delta": c.rerank_delta,
                    "in_context": c.in_context,
                }
                for c in out.reranked
            ],
        },
        "rewrite": out.rewrite.model_dump() if out.rewrite else None,
        "verification": out.verification.model_dump() if out.verification else None,
        "latency_ms": out.stage_latency_ms,
        "errors": out.errors,
    }


@router.post("/ask")
async def ask(req: AskRequest, request: Request, principal: Principal = PrincipalDep) -> dict:
    out = await run_pipeline(
        question=req.question,
        deps=_deps(request, principal),
        filters=req.filters,
        want_rewrite=req.rewrite,
        want_verify=req.verify,
    )
    return _serialize(out)


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


@router.post("/ask/stream")
async def ask_stream(req: AskRequest, request: Request, principal: Principal = PrincipalDep):
    deps = _deps(request, principal)

    async def events() -> AsyncIterator[str]:
        try:
            # LangGraph streams node completions, so each stage can be surfaced
            # the moment it finishes rather than after the whole graph settles.
            from app.graph.pipeline import get_pipeline
            from app.graph.state import PipelineState

            initial: PipelineState = {
                "question": req.question,
                "filters": req.filters,
                "history": [],
                "want_rewrite": req.rewrite,
                "want_verify": req.verify,
                "stage_latency_ms": {},
                "errors": [],
            }

            merged: dict = {}
            async for update in get_pipeline().astream(
                initial, config={"configurable": {"deps": deps}}, stream_mode="updates"
            ):
                for node, patch in update.items():
                    if not isinstance(patch, dict):
                        continue
                    merged.update(patch)

                    if node in ("rewrite", "skip_rewrite") and "rewrite" in patch:
                        yield _sse("rewrite", patch["rewrite"].model_dump())
                    elif node == "rerank":
                        # The perceived-latency win: source cards can render now,
                        # while the model is still generating.
                        yield _sse(
                            "retrieval",
                            {
                                "lanes": merged.get("lane_sizes", {}),
                                "fused": len(merged.get("fused", [])),
                                "reranked": [
                                    {
                                        "chunk_id": c.chunk_id,
                                        "title": c.title,
                                        "page": c.page_start,
                                        "bm25_rank": c.bm25_rank,
                                        "vec_rank": c.vec_rank,
                                        "rrf": c.rrf_score,
                                        "rerank": c.rerank_score,
                                        "delta": c.rerank_delta,
                                        "in_context": c.in_context,
                                    }
                                    for c in patch.get("reranked", [])
                                ],
                            },
                        )
                    elif node in ("generate", "refuse"):
                        yield _sse(
                            "answer",
                            {
                                "text": patch.get("answer_text", ""),
                                "refused": bool(patch.get("refused", False)),
                            },
                        )
                    elif node in ("verify", "skip_verify") and "verification" in patch:
                        yield _sse("verification", patch["verification"].model_dump())

            out = PipelineResult.from_state(merged, req.question)
            yield _sse("done", _serialize(out))
        except Exception as exc:  # noqa: BLE001
            # A stream that dies silently looks identical to one still working,
            # so failures must be delivered as an event.
            yield _sse("error", {"message": str(exc)[:300]})

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            "cache-control": "no-cache, no-store, no-transform",
            "connection": "keep-alive",
            # Without these a buffering proxy delivers the whole stream at the
            # end, which defeats the point entirely.
            "x-accel-buffering": "no",
            "content-encoding": "identity",
        },
    )
