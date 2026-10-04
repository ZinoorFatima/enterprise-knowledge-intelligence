"""Evaluation runs."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, status

from app.core.security import Principal, PrincipalDep
from app.db.session import PgRetriever
from app.eval.runner import load_dataset, run_eval
from app.generate.provider import get_provider
from app.graph.state import PipelineDeps

router = APIRouter(tags=["evaluation"])

GOLDEN_DIR = Path(__file__).resolve().parents[3] / "eval" / "golden"


@router.get("/eval/datasets")
async def list_datasets(principal: Principal = PrincipalDep) -> dict:
    if not GOLDEN_DIR.exists():
        return {"items": []}
    return {"items": [{"name": p.stem, "file": p.name} for p in sorted(GOLDEN_DIR.glob("*.jsonl"))]}


@router.post("/eval/runs")
async def create_run(
    request: Request, dataset: str = "smoke", principal: Principal = PrincipalDep
) -> dict:
    principal.require_role("owner", "admin")

    path = GOLDEN_DIR / f"{dataset}.jsonl"
    if not path.exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"dataset not found: {dataset}")

    from app.retrieve.embedder import get_embedder, get_reranker

    embedder = request.app.state.embedder or get_embedder()
    request.app.state.embedder = embedder

    deps = PipelineDeps(
        retriever=PgRetriever(request.app.state.pool),
        embedder=embedder,
        reranker=get_reranker(),
        provider=get_provider(),
        org_id=principal.org_id,
    )
    summary = await run_eval(load_dataset(path), deps, embedder=embedder)
    # to_dict() carries provisional + provisional_reasons, so a caller can never
    # render these numbers as measured without also seeing why they might not be.
    return summary.to_dict()
