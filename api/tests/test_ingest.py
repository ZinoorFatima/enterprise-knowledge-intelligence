"""Ingestion against a real PDF and a real database.

The load-bearing test is test_known_phrase_resolves_to_its_printed_page: it
builds a PDF where a unique phrase is printed on a known page, ingests it, and
asserts the stored chunk reports that page number. That is the whole provenance
chain -- extraction offsets, page spine, chunk spans, bisect -- verified against
ground truth a human could check by eye.
"""

from __future__ import annotations

import uuid

import pytest

pymupdf = pytest.importorskip("pymupdf")
asyncpg = pytest.importorskip("asyncpg")

from app.config import settings  # noqa: E402
from app.ingest.pdf import extract_pdf, page_needs_ocr, strip_running_headers  # noqa: E402
from app.ingest.pipeline import HashEmbedder, ingest_pdf, progress_for  # noqa: E402

MARKER = "Zanzibar reconciliation threshold is forty-two percent"
MARKER_PAGE = 4


def build_pdf(path, pages: int = 6, marker_page: int = MARKER_PAGE) -> str:
    """A text PDF with a unique phrase on one known page."""
    doc = pymupdf.open()
    for i in range(1, pages + 1):
        page = doc.new_page()
        page.insert_text((72, 72), "CONFIDENTIAL - DO NOT DISTRIBUTE", fontsize=9)
        body = (
            f"Section {i}. This clause sets out the obligations of each party "
            f"under schedule {i}. The parties agree to the terms described "
            f"herein and in any applicable annex. "
        ) * 4
        if i == marker_page:
            body += f" {MARKER}. "
        page.insert_textbox(pymupdf.Rect(72, 100, 520, 700), body, fontsize=11)
        page.insert_text((72, 760), f"Page {i} of {pages}", fontsize=9)
    doc.save(str(path))
    doc.close()
    return str(path)


@pytest.fixture
def sample_pdf(tmp_path):
    return build_pdf(tmp_path / "sample.pdf")


async def _conn():
    try:
        return await asyncpg.connect(settings.asyncpg_dsn, timeout=5)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"no database reachable: {exc}")


@pytest.fixture
async def db():
    c = await _conn()
    org = await c.fetchval("SELECT id FROM auth.organizations LIMIT 1")
    owner = await c.fetchval("SELECT id FROM auth.users LIMIT 1")
    if not org or not owner:
        await c.close()
        pytest.skip("needs a registered user; run the web sign-up flow first")
    tx = c.transaction()
    await tx.start()
    try:
        yield c, str(org), str(owner)
    finally:
        # Roll back so tests never leave rows behind.
        await tx.rollback()
        await c.close()


class TestExtraction:
    def test_page_spine_offsets_are_exact(self, sample_pdf):
        """Each page's recorded span must slice full_text back to that page."""
        doc = extract_pdf(sample_pdf)
        assert doc.page_count == 6
        for p in doc.pages:
            assert doc.full_text[p.char_start : p.char_end] == p.text

    def test_pages_are_one_indexed_and_ascending(self, sample_pdf):
        doc = extract_pdf(sample_pdf)
        assert [p.page_number for p in doc.pages] == list(range(1, 7))
        starts = [p.char_start for p in doc.pages]
        assert starts == sorted(starts)

    def test_born_digital_text_is_not_sent_to_ocr(self, sample_pdf):
        doc = pymupdf.open(sample_pdf)
        try:
            for page in doc:
                needs, reason = page_needs_ocr(page)
                assert needs is False, f"page flagged for OCR: {reason}"
        finally:
            doc.close()

    def test_running_headers_are_stripped(self, sample_pdf):
        """Otherwise 'CONFIDENTIAL' appears in every chunk and dominates the
        keyword lane for any query containing it."""
        doc = extract_pdf(sample_pdf, strip_headers=True)
        assert doc.full_text.count("CONFIDENTIAL") <= 1

    def test_header_stripping_masks_digits(self):
        pages = [f"Page {i} of 9\nbody text here\nFOOTER" for i in range(1, 10)]
        out = strip_running_headers(pages)
        assert all("Page" not in p.splitlines()[0] for p in out)

    def test_empty_pdf_is_handled(self, tmp_path):
        doc = pymupdf.open()
        doc.new_page()
        p = tmp_path / "blank.pdf"
        doc.save(str(p))
        doc.close()
        extracted = extract_pdf(p)
        assert extracted.page_count == 1


class TestProgressWeighting:
    def test_ocr_dominates_when_present(self):
        assert progress_for(["extract", "detect"], needs_ocr=True) < 0.10

    def test_weight_is_redistributed_when_ocr_is_skipped(self):
        """A text-only PDF must still reach 100%, not stall at 45%."""
        all_stages = ["extract", "detect", "layout", "normalize", "chunk", "embed", "index"]
        assert progress_for(all_stages, needs_ocr=False) == pytest.approx(1.0, abs=1e-3)

    def test_full_run_with_ocr_reaches_one(self):
        every = ["extract", "detect", "ocr", "layout", "normalize", "chunk", "embed", "index"]
        assert progress_for(every, needs_ocr=True) == pytest.approx(1.0, abs=1e-6)

    def test_monotonic(self):
        seen, prev = [], -1.0
        for s in ["extract", "detect", "layout", "normalize", "chunk", "embed", "index"]:
            seen.append(s)
            cur = progress_for(seen, needs_ocr=False)
            assert cur > prev
            prev = cur


class TestIngestEndToEnd:
    async def test_known_phrase_resolves_to_its_printed_page(self, db, sample_pdf):
        """Ground-truth provenance check: the marker is printed on page 4, so
        the chunk containing it must report page 4."""
        conn, org, owner = db
        res = await ingest_pdf(
            conn=conn, path=sample_pdf, org_id=org, owner_id=owner,
            title="Provenance Fixture", embedder=HashEmbedder(settings.embed_dim),
        )
        assert res.chunk_count > 0

        row = await conn.fetchrow(
            """
            SELECT page_start, page_end, char_start, char_end, text
            FROM rag.chunks
            WHERE document_id = $1::uuid AND text LIKE '%Zanzibar%'
            LIMIT 1
            """,
            res.document_id,
        )
        assert row is not None, "marker phrase was not found in any stored chunk"
        assert row["page_start"] <= MARKER_PAGE <= row["page_end"], (
            f"marker printed on page {MARKER_PAGE} but chunk reports "
            f"pages {row['page_start']}-{row['page_end']}"
        )

    async def test_chunk_spans_slice_back_out_of_full_text(self, db, sample_pdf):
        """The invariant everything downstream depends on."""
        conn, org, owner = db
        res = await ingest_pdf(
            conn=conn, path=sample_pdf, org_id=org, owner_id=owner,
            embedder=HashEmbedder(settings.embed_dim),
        )
        full_text = await conn.fetchval(
            "SELECT full_text FROM rag.documents WHERE id = $1::uuid", res.document_id
        )
        rows = await conn.fetch(
            "SELECT text, char_start, char_end FROM rag.chunks WHERE document_id = $1::uuid ORDER BY ordinal",
            res.document_id,
        )
        assert rows
        for r in rows:
            assert full_text[r["char_start"] : r["char_end"]] == r["text"]

    async def test_embeddings_and_tsvector_are_both_populated(self, db, sample_pdf):
        """Both retrieval lanes need their column filled, or hybrid search
        silently degrades to single-lane."""
        conn, org, owner = db
        res = await ingest_pdf(
            conn=conn, path=sample_pdf, org_id=org, owner_id=owner,
            embedder=HashEmbedder(settings.embed_dim),
        )
        missing = await conn.fetchval(
            """
            SELECT count(*) FROM rag.chunks
            WHERE document_id = $1::uuid
              AND (embedding IS NULL OR content_tsv IS NULL)
            """,
            res.document_id,
        )
        assert missing == 0

    async def test_content_hash_deduplicates(self, db, sample_pdf):
        """Re-uploading the same bytes must not pay for OCR and embedding again."""
        conn, org, owner = db
        first = await ingest_pdf(
            conn=conn, path=sample_pdf, org_id=org, owner_id=owner,
            embedder=HashEmbedder(settings.embed_dim),
        )
        second = await ingest_pdf(
            conn=conn, path=sample_pdf, org_id=org, owner_id=owner,
            embedder=HashEmbedder(settings.embed_dim),
        )
        assert second.deduplicated is True
        assert second.document_id == first.document_id
        assert second.chunk_count == 0

    async def test_document_reaches_ready_with_page_count(self, db, sample_pdf):
        conn, org, owner = db
        res = await ingest_pdf(
            conn=conn, path=sample_pdf, org_id=org, owner_id=owner,
            embedder=HashEmbedder(settings.embed_dim),
        )
        row = await conn.fetchrow(
            "SELECT status::text, page_count FROM rag.documents WHERE id = $1::uuid",
            res.document_id,
        )
        assert row["status"] == "ready"
        assert row["page_count"] == 6

    async def test_pages_table_mirrors_the_spine(self, db, sample_pdf):
        conn, org, owner = db
        res = await ingest_pdf(
            conn=conn, path=sample_pdf, org_id=org, owner_id=owner,
            embedder=HashEmbedder(settings.embed_dim),
        )
        rows = await conn.fetch(
            "SELECT page_number, char_start, char_end FROM rag.pages WHERE document_id = $1::uuid ORDER BY page_number",
            res.document_id,
        )
        assert [r["page_number"] for r in rows] == list(range(1, 7))
        assert all(r["char_start"] < r["char_end"] for r in rows)

    async def test_chunks_respect_the_token_ceiling(self, db, sample_pdf):
        conn, org, owner = db
        res = await ingest_pdf(
            conn=conn, path=sample_pdf, org_id=org, owner_id=owner,
            embedder=HashEmbedder(settings.embed_dim),
        )
        too_big = await conn.fetchval(
            "SELECT count(*) FROM rag.chunks WHERE document_id = $1::uuid AND token_count > $2",
            res.document_id,
            settings.chunk_max_tokens * 2,
        )
        assert too_big == 0

    async def test_org_scoping_is_recorded_on_every_chunk(self, db, sample_pdf):
        """Denormalized org_id is what lets both retrieval lanes filter without
        joining to documents."""
        conn, org, owner = db
        res = await ingest_pdf(
            conn=conn, path=sample_pdf, org_id=org, owner_id=owner,
            embedder=HashEmbedder(settings.embed_dim),
        )
        wrong = await conn.fetchval(
            "SELECT count(*) FROM rag.chunks WHERE document_id = $1::uuid AND org_id <> $2::uuid",
            res.document_id,
            org,
        )
        assert wrong == 0
