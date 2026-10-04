"""Document upload, listing, and ingestion."""

from __future__ import annotations

import tempfile
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import StreamingResponse

from app.config import settings
from app.core.security import Principal, PrincipalDep
from app.ingest.pipeline import ingest_pdf

router = APIRouter(tags=["documents"])


@router.get("/documents")
async def list_documents(
    request: Request, principal: Principal = PrincipalDep, limit: int = 50
) -> dict:
    rows = await request.app.state.pool.fetch(
        """
        SELECT d.id::text, d.title, d.filename, d.page_count, d.status::text,
               d.scanned_pages, d.tags, d.created_at,
               (SELECT count(*) FROM rag.chunks c WHERE c.document_id = d.id) AS chunk_count
        FROM rag.documents d
        WHERE d.org_id = $1::uuid
        ORDER BY d.created_at DESC
        LIMIT $2
        """,
        principal.org_id,
        min(limit, 200),
    )
    return {"items": [dict(r) for r in rows]}


@router.get("/documents/{document_id}")
async def get_document(
    document_id: str, request: Request, principal: Principal = PrincipalDep
) -> dict:
    row = await request.app.state.pool.fetchrow(
        """
        SELECT id::text, title, filename, page_count, status::text, scanned_pages,
               ocr_mean_conf, tags, created_at
        FROM rag.documents WHERE id = $1::uuid AND org_id = $2::uuid
        """,
        document_id,
        principal.org_id,
    )
    # 404 rather than 403 on a cross-org id: a 403 would confirm it exists.
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="document not found")
    return dict(row)


@router.get("/documents/{document_id}/file")
async def get_document_file(
    document_id: str, request: Request, principal: Principal = PrincipalDep
):
    """Stream the original PDF.

    Range support is not optional here: pdf.js fetches byte ranges to render a
    single page without pulling a 600-page file, so refusing ranges would turn
    "jump to page 47" into a full download.
    """
    row = await request.app.state.pool.fetchrow(
        "SELECT storage_key, filename FROM rag.documents WHERE id = $1::uuid AND org_id = $2::uuid",
        document_id,
        principal.org_id,
    )
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="document not found")

    path = Path(row["storage_key"])
    if not path.is_file():
        # Rows ingested before originals were retained. Say so precisely rather
        # than returning a generic 404 that looks like a permissions problem.
        raise HTTPException(
            status.HTTP_410_GONE,
            detail="the original file for this document was not retained at ingest time",
        )

    size = path.stat().st_size
    headers = {
        "accept-ranges": "bytes",
        "content-disposition": f'inline; filename="{row["filename"]}"',
        "cache-control": "private, max-age=300",
    }

    range_header = request.headers.get("range")
    if range_header and range_header.startswith("bytes="):
        spec = range_header.removeprefix("bytes=").split(",")[0].strip()
        start_s, _, end_s = spec.partition("-")
        try:
            start = int(start_s) if start_s else 0
            end = int(end_s) if end_s else size - 1
        except ValueError:
            raise HTTPException(status.HTTP_416_REQUESTED_RANGE_NOT_SATISFIABLE, detail="bad range")
        end = min(end, size - 1)
        if start > end or start >= size:
            raise HTTPException(
                status.HTTP_416_REQUESTED_RANGE_NOT_SATISFIABLE, detail="range out of bounds"
            )

        def chunk_range():
            remaining = end - start + 1
            with path.open("rb") as fh:
                fh.seek(start)
                while remaining > 0:
                    block = fh.read(min(1 << 18, remaining))
                    if not block:
                        break
                    remaining -= len(block)
                    yield block

        headers["content-range"] = f"bytes {start}-{end}/{size}"
        headers["content-length"] = str(end - start + 1)
        return StreamingResponse(
            chunk_range(), status_code=206, media_type="application/pdf", headers=headers
        )

    headers["content-length"] = str(size)
    return StreamingResponse(path.open("rb"), media_type="application/pdf", headers=headers)


@router.get("/documents/{document_id}/pages/{page_number}")
async def get_page(
    document_id: str,
    page_number: int,
    request: Request,
    principal: Principal = PrincipalDep,
) -> dict:
    """Extracted text for one page, with its OCR provenance.

    ocr_confidence is returned so the UI can mark a low-confidence scan rather
    than presenting a possibly-misread figure as clean text.
    """
    row = await request.app.state.pool.fetchrow(
        """
        SELECT p.page_number, p.text, p.char_start, p.char_end,
               p.extraction_method, p.ocr_confidence, p.is_scanned,
               p.width, p.height, p.rotation
        FROM rag.pages p
        JOIN rag.documents d ON d.id = p.document_id
        WHERE p.document_id = $1::uuid AND p.page_number = $2 AND d.org_id = $3::uuid
        """,
        document_id,
        page_number,
        principal.org_id,
    )
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="page not found")
    return dict(row)


@router.post("/documents", status_code=status.HTTP_201_CREATED)
async def upload_document(
    request: Request,
    file: UploadFile = File(...),
    title: str | None = Form(default=None),
    tags: str = Form(default=""),
    principal: Principal = PrincipalDep,
) -> dict:
    principal.require_role("owner", "admin", "member")

    if (file.content_type or "") not in ("application/pdf", "application/octet-stream"):
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail="only PDF is supported"
        )

    # Stream to a temp file rather than buffering. A large scan read into memory
    # would sit entirely in the worker heap and can OOM the container.
    size = 0
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
        while block := await file.read(1 << 20):
            size += len(block)
            if size > settings.max_upload_bytes:
                tmp.close()
                Path(tmp.name).unlink(missing_ok=True)
                raise HTTPException(
                    status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail=f"file exceeds {settings.max_upload_bytes // (1 << 20)} MB",
                )
            tmp.write(block)
        tmp_path = Path(tmp.name)

    from app.retrieve.embedder import get_embedder

    embedder = request.app.state.embedder or get_embedder()
    request.app.state.embedder = embedder

    try:
        async with request.app.state.pool.acquire() as conn, conn.transaction():
            result = await ingest_pdf(
                conn=conn,
                path=tmp_path,
                org_id=principal.org_id,
                owner_id=principal.user_id,
                title=title or file.filename,
                tags=[t.strip() for t in tags.split(",") if t.strip()],
                embedder=embedder,
            )
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))
    finally:
        tmp_path.unlink(missing_ok=True)

    return {
        "document_id": result.document_id,
        "page_count": result.page_count,
        "chunk_count": result.chunk_count,
        "scanned_pages": result.scanned_pages,
        "deduplicated": result.deduplicated,
        "stage_timings": result.stage_timings,
    }


@router.delete("/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    document_id: str, request: Request, principal: Principal = PrincipalDep
):
    principal.require_role("owner", "admin", "member")
    deleted = await request.app.state.pool.fetchval(
        "DELETE FROM rag.documents WHERE id = $1::uuid AND org_id = $2::uuid RETURNING id",
        document_id,
        principal.org_id,
    )
    if deleted is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="document not found")
