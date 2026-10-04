"""Liveness and readiness. Unauthenticated by design."""

from __future__ import annotations

from fastapi import APIRouter, Request

from app.config import settings

router = APIRouter(tags=["health"])


@router.get("/healthz")
async def healthz() -> dict:
    return {"status": "ok"}


@router.get("/readyz")
async def readyz(request: Request) -> dict:
    """Reports each dependency separately so a degraded component is visible
    rather than collapsing into one boolean."""
    out: dict = {"postgres": "down", "pgvector": "unknown", "llm_mode": settings.llm_mode}
    try:
        pool = request.app.state.pool
        async with pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
            out["postgres"] = "ok"
            ver = await conn.fetchval(
                "SELECT extversion FROM pg_extension WHERE extname = 'vector'"
            )
            out["pgvector"] = ver or "missing"
            out["chunks"] = await conn.fetchval("SELECT count(*) FROM rag.chunks")
    except Exception as exc:  # noqa: BLE001
        out["error"] = str(exc)[:200]
    out["status"] = "ok" if out["postgres"] == "ok" else "degraded"
    return out
