"""FastAPI application.

Not publicly exposed. The browser talks to Next.js, which verifies the session
and forwards with a short-lived service token; see app/core/security.py.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

import asyncpg
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from app.config import settings
from app.routers import ask, documents, evaluation, health, search

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("ekis")


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.pool = await asyncpg.create_pool(
        settings.asyncpg_dsn,
        min_size=settings.db_pool_min,
        max_size=settings.db_pool_max,
        command_timeout=60,
    )
    # Load the embedding model eagerly only when it is already cached; a cold
    # download must not block startup.
    app.state.embedder = None
    log.info("started: llm_mode=%s embed_model=%s", settings.llm_mode, settings.embed_model)
    try:
        yield
    finally:
        await app.state.pool.close()


app = FastAPI(
    title="Enterprise Knowledge Intelligence API",
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/docs",
)

app.include_router(health.router)
app.include_router(documents.router, prefix="/v1")
app.include_router(search.router, prefix="/v1")
app.include_router(ask.router, prefix="/v1")
app.include_router(evaluation.router, prefix="/v1")


@app.exception_handler(Exception)
async def unhandled(request, exc):  # noqa: ANN001
    log.exception("unhandled error on %s", request.url.path)
    # Never leak internals to a caller; the detail is in the server log.
    return JSONResponse(status_code=500, content={"error": "INTERNAL_ERROR"})
