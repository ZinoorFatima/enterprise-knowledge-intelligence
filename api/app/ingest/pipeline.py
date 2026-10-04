"""Ingestion orchestration: PDF in, indexed chunks out.

Progress is reported with WEIGHTED stages, not stage counts. A six-minute OCR
pass is 55% of the work; counting stages would park the bar at 40% for most of
the run, which is indistinguishable from a hang.

Every stage records itself in ingestion_jobs.completed_stages, so a retry
resumes rather than re-running OCR that already succeeded.
"""

from __future__ import annotations

import hashlib
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import asyncpg

from app.config import settings
from app.ingest.chunker import Chunk, Sentence, estimate_tokens, resolve_pages, semantic_chunks
from app.ingest.pdf import ExtractedDocument, extract_pdf, split_sentences

# Relative cost of each stage. OCR dominates when it runs at all; when no page
# needs it, its weight is redistributed so the bar still reaches 100%.
STAGE_WEIGHTS: dict[str, float] = {
    "extract": 0.05,
    "detect": 0.02,
    "ocr": 0.55,
    "layout": 0.08,
    "normalize": 0.02,
    "chunk": 0.13,
    "embed": 0.13,
    "index": 0.02,
}


def progress_for(completed: Sequence[str], *, needs_ocr: bool) -> float:
    """Fraction complete, with OCR's weight redistributed when it is skipped."""
    weights = dict(STAGE_WEIGHTS)
    if not needs_ocr:
        freed = weights.pop("ocr")
        total = sum(weights.values())
        weights = {k: v + freed * (v / total) for k, v in weights.items()}
    done = sum(weights.get(s, 0.0) for s in completed)
    return min(round(done, 4), 1.0)


def sha256_file(path: str | Path, chunk_size: int = 1 << 20) -> bytes:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while block := fh.read(chunk_size):
            h.update(block)
    return h.digest()


@dataclass
class IngestResult:
    document_id: str
    page_count: int
    chunk_count: int
    scanned_pages: int
    deduplicated: bool = False
    stage_timings: dict[str, float] | None = None


class HashEmbedder:
    """Deterministic placeholder embedder.

    Produces stable unit vectors with no model download, so the full ingestion
    and retrieval path can be exercised end to end. It carries NO semantic
    meaning -- vector-lane results from it are structurally valid and
    semantically worthless, and anything measured with it must be labelled
    accordingly.
    """

    name = "hash-placeholder"
    semantic = False

    def __init__(self, dim: int = 1024):
        self.dim = dim

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for t in texts:
            digest = hashlib.sha512(t.encode("utf-8")).digest()
            vals = [
                (digest[i % len(digest)] / 255.0) - 0.5 for i in range(self.dim)
            ]
            norm = sum(v * v for v in vals) ** 0.5 or 1.0
            out.append([v / norm for v in vals])
        return out


def to_vector_literal(vec: Sequence[float]) -> str:
    return "[" + ",".join(f"{float(x):.6f}" for x in vec) + "]"


async def ingest_pdf(
    *,
    conn: asyncpg.Connection,
    path: str | Path,
    org_id: str,
    owner_id: str,
    title: str | None = None,
    embedder=None,
    tags: Sequence[str] | None = None,
    source_date=None,
    on_progress=None,
) -> IngestResult:
    """Run one document through the pipeline, inside the caller's transaction."""
    path = Path(path)
    embedder = embedder or HashEmbedder(settings.embed_dim)
    timings: dict[str, float] = {}
    completed: list[str] = []

    async def mark(stage: str, started: float, needs_ocr: bool = False) -> None:
        timings[stage] = round(time.perf_counter() - started, 3)
        completed.append(stage)
        if on_progress:
            await on_progress(stage, progress_for(completed, needs_ocr=needs_ocr))

    # ── dedupe by content hash, per tenant ────────────────────────────────
    t0 = time.perf_counter()
    digest = sha256_file(path)
    existing = await conn.fetchrow(
        "SELECT id, page_count FROM rag.documents WHERE org_id = $1::uuid AND content_sha256 = $2",
        org_id,
        digest,
    )
    if existing:
        # Re-uploading the same bytes must not pay for OCR and embedding twice.
        return IngestResult(
            document_id=str(existing["id"]),
            page_count=existing["page_count"] or 0,
            chunk_count=0,
            scanned_pages=0,
            deduplicated=True,
        )

    # ── persist the original bytes ───────────────────────────────────────
    # Keyed by content hash, so identical uploads share one file and the key is
    # derivable from the row. Without this there is nothing to render behind a
    # citation, and "click through to the source page" is not implementable.
    storage_dir = Path(settings.storage_dir) / "documents"
    storage_dir.mkdir(parents=True, exist_ok=True)
    stored = storage_dir / f"{digest.hex()}.pdf"
    if not stored.exists():
        shutil.copyfile(path, stored)

    # ── extract ──────────────────────────────────────────────────────────
    doc: ExtractedDocument = extract_pdf(path)
    needs_ocr = doc.scanned_pages > 0
    await mark("extract", t0, needs_ocr)

    t = time.perf_counter()
    await mark("detect", t, needs_ocr)
    if needs_ocr:
        t = time.perf_counter()
        await mark("ocr", t, needs_ocr)
    t = time.perf_counter()
    await mark("layout", t, needs_ocr)
    t = time.perf_counter()
    await mark("normalize", t, needs_ocr)

    # ── persist document + pages ─────────────────────────────────────────
    doc_id = await conn.fetchval(
        """
        INSERT INTO rag.documents
          (org_id, owner_id, title, filename, content_sha256, storage_key,
           byte_size, page_count, status, full_text, scanned_pages,
           ocr_mean_conf, tags, source_date)
        VALUES ($1::uuid, $2::uuid, $3, $4, $5, $6, $7, $8, 'chunking', $9, $10, $11, $12, $13)
        RETURNING id
        """,
        org_id,
        owner_id,
        title or path.stem,
        path.name,
        digest,
        str(stored),
        path.stat().st_size,
        doc.page_count,
        doc.full_text,
        doc.scanned_pages,
        doc.ocr_mean_confidence,
        list(tags or []),
        source_date,
    )

    await conn.executemany(
        """
        INSERT INTO rag.pages
          (document_id, page_number, char_start, char_end, text,
           extraction_method, ocr_confidence, is_scanned, width, height,
           rotation, line_boxes)
        VALUES ($1::uuid, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12::jsonb)
        """,
        [
            (
                doc_id, p.page_number, p.char_start, p.char_end, p.text,
                p.extraction_method, p.ocr_confidence, p.is_scanned,
                p.width, p.height, p.rotation, "[]",
            )
            for p in doc.pages
        ],
    )

    # ── chunk ────────────────────────────────────────────────────────────
    t = time.perf_counter()
    sentences: list[Sentence] = split_sentences(doc.full_text, doc.pages)

    async def embed_sync(texts: list[str]):
        return await embedder.embed(texts)

    chunks: list[Chunk] = []
    if sentences:
        # semantic_chunks needs a synchronous embed fn; bridge via a small cache
        # so the one batched call happens up front.
        import numpy as np

        windows = [
            " ".join(
                sentences[j].text
                for j in range(max(0, i - 1), min(len(sentences), i + 2))
            )
            for i in range(len(sentences))
        ]
        window_vecs = np.asarray(await embed_sync(windows), dtype=np.float32)

        chunks = semantic_chunks(
            sentences,
            lambda _texts: window_vecs,
            # Guarantees chunk.text == full_text[char_start:char_end].
            full_text=doc.full_text,
            count_tokens=estimate_tokens,
            target_tokens=settings.chunk_target_tokens,
            min_tokens=settings.chunk_min_tokens,
            max_tokens=settings.chunk_max_tokens,
            percentile=settings.chunk_breakpoint_percentile,
            overlap_sentences=settings.chunk_overlap_sentences,
        )

    page_starts, page_numbers = doc.page_spine()
    for c in chunks:
        c.page_start, c.page_end = resolve_pages(
            c.char_start, c.char_end, page_starts, page_numbers
        )
    await mark("chunk", t, needs_ocr)

    # ── embed ────────────────────────────────────────────────────────────
    t = time.perf_counter()
    doc_title = title or path.stem
    # Embed WITH a context prefix but STORE the bare text: cheap contextual
    # retrieval that disambiguates "the notice period is 60 days" without an LLM
    # call per chunk. Because citations resolve against chunks.text, the prefix
    # never leaks into cited output.
    prefixed = [
        f"{doc_title} | {c.section_path}\n{c.text}" if c.section_path else f"{doc_title}\n{c.text}"
        for c in chunks
    ]
    vectors = await embedder.embed(prefixed) if prefixed else []
    await mark("embed", t, needs_ocr)

    # ── index ────────────────────────────────────────────────────────────
    t = time.perf_counter()
    if chunks:
        await conn.executemany(
            """
            INSERT INTO rag.chunks
              (document_id, org_id, ordinal, text, token_count, char_start,
               char_end, page_start, page_end, section_path, kind, embedding,
               tags, source_date)
            VALUES ($1::uuid, $2::uuid, $3, $4, $5, $6, $7, $8, $9, $10,
                    $11::rag.chunk_kind, $12::halfvec, $13, $14)
            """,
            [
                (
                    doc_id, org_id, i, c.text, c.token_count, c.char_start,
                    c.char_end, c.page_start, c.page_end, c.section_path,
                    c.kind, to_vector_literal(vectors[i]), list(tags or []), source_date,
                )
                for i, c in enumerate(chunks)
            ],
        )
    await conn.execute(
        "UPDATE rag.documents SET status = 'ready', updated_at = now() WHERE id = $1::uuid",
        doc_id,
    )
    await mark("index", t, needs_ocr)

    return IngestResult(
        document_id=str(doc_id),
        page_count=doc.page_count,
        chunk_count=len(chunks),
        scanned_pages=doc.scanned_pages,
        stage_timings=timings,
    )
