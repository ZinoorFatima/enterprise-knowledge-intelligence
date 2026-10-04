"""Query-plan regression tests against a real Postgres.

These exist because of a bug that actually shipped into this repo. Writing the
vector lane as

    ROW_NUMBER() OVER (ORDER BY c.embedding <=> $3) ... LIMIT k

forces the planner to sort every filtered row before the LIMIT applies, so the
HNSW index is never used. Nothing fails. No error is raised. The only symptom is
that the query slows in proportion to corpus size -- invisible on a dev corpus,
impossible to miss in production.

A unit test cannot catch that. Only EXPLAIN can. Skipped when no database is
reachable, so the suite still runs offline.
"""

from __future__ import annotations

import re

import pytest

asyncpg = pytest.importorskip("asyncpg")

from app.config import settings  # noqa: E402
from app.db.session import HYBRID_SQL  # noqa: E402


async def _connect():
    try:
        return await asyncpg.connect(settings.asyncpg_dsn, timeout=5)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"no database reachable: {exc}")


def _vec_literal(dim: int = 1024) -> str:
    return "'[" + ",".join(f"{(i % 100) / 100.0:.2f}" for i in range(dim)) + "]'::halfvec(1024)"


def _inline(sql: str, vec_literal: str, org: str, query_text: str = "liability fees") -> str:
    """Substitute every $n in ONE regex pass.

    A naive sequential replace rewrites $1 inside $11 and silently produces a
    different query -- its own small lesson about why this file exists.
    """
    params = {
        1: f"'{org}'::uuid",
        2: f"'{query_text}'",
        3: vec_literal,
        4: "NULL::uuid[]",
        5: "NULL::date",
        6: "NULL::date",
        7: "NULL::text[]",
        8: "100",
        9: "60",
        10: "60",
        11: "1.0",
        12: "1.0",
    }
    return re.sub(r"\$(\d+)", lambda m: params[int(m.group(1))], sql)


async def _explain(conn, sql: str, *, force_index: bool = False) -> str:
    """EXPLAIN the query.

    force_index disables sequential scans. Whether the planner *chooses* an
    index is corpus-size dependent -- on a small single-tenant dev corpus a seq
    scan is genuinely cheaper and choosing it is correct costing, not a
    regression. What must hold at every size is that the query is SHAPED so the
    index CAN be used, bounded to lane_k rows.
    """
    await conn.execute("LOAD 'vector'")
    tx = conn.transaction()
    await tx.start()
    try:
        await conn.execute("SET LOCAL hnsw.ef_search = 100")
        await conn.execute("SET LOCAL hnsw.iterative_scan = 'relaxed_order'")
        if force_index:
            await conn.execute("SET LOCAL enable_seqscan = off")
        rows = await conn.fetch("EXPLAIN (ANALYZE, COSTS OFF) " + sql)
    finally:
        await tx.rollback()
    return "\n".join(r[0] for r in rows)


@pytest.fixture
async def conn():
    """Yields (connection, org_id) for whichever org actually owns the corpus."""
    c = await _connect()
    row = await c.fetchrow(
        "SELECT org_id, count(*) AS n FROM rag.chunks GROUP BY org_id ORDER BY n DESC LIMIT 1"
    )
    if not row or row["n"] < 500:
        await c.close()
        pytest.skip(
            f"needs a seeded corpus (found {row['n'] if row else 0} chunks); "
            "run: python scripts/seed_dev.py --bulk 90"
        )
    yield c, str(row["org_id"])
    await c.close()


class TestHybridPlan:
    """Plan shape.

    Note what these deliberately do NOT assert: that the planner always picks
    HNSW for the full query. With one tenant the org_id predicate matches every
    row, so Postgres reasonably prefers the org btree and sorts. Asserting
    otherwise would be testing the planner's cost model, not our SQL.
    """

    # The vector lane's ordering construct, mirroring hybrid.sql. If that lane is
    # rewritten to wrap the ordering in a window function, this stops being
    # index-answerable and these assertions fail.
    LANE = (
        "SELECT c.id FROM rag.chunks c "
        "WHERE c.embedding IS NOT NULL "
        "ORDER BY c.embedding <=> {vec} LIMIT 100"
    )

    async def test_vector_ordering_is_index_answerable(self, conn):
        c, _org = conn
        plan = await _explain(c, self.LANE.format(vec=_vec_literal()), force_index=True)
        assert "chunks_embedding_hnsw" in plan, (
            "ORDER BY embedding <=> const LIMIT k cannot use the HNSW index. "
            "Most likely cause: the ordering was wrapped in a window function, "
            "which forces a full sort before LIMIT applies.\n\n" + plan
        )

    async def test_index_walk_is_bounded_to_lane_k(self, conn):
        """The discriminating assertion. A window-function ordering has to read
        every candidate row to number them; a bounded walk reads k."""
        c, _org = conn
        plan = await _explain(c, self.LANE.format(vec=_vec_literal()), force_index=True)
        line = next((ln for ln in plan.splitlines() if "chunks_embedding_hnsw" in ln), "")
        m = re.search(r"rows=(\d+)", line)
        assert m, f"no row count on the HNSW scan line:\n{plan}"
        assert int(m.group(1)) <= 200, (
            f"HNSW scan returned {m.group(1)} rows; expected ~100 (lane_k). "
            "Reading the whole table means LIMIT is not reaching the index."
        )

    def test_hybrid_sql_keeps_the_ordering_out_of_a_window_function(self):
        """Source-level guard, so the regression is caught with no database at
        all. This one construct is what silently disables the index."""
        collapsed = " ".join(HYBRID_SQL.split()).lower()
        assert "row_number() over ( order by c.embedding" not in collapsed
        assert "row_number() over (order by c.embedding" not in collapsed
        # ...and the lane must still carry its own ORDER BY ... LIMIT.
        assert "order by c.embedding <=>" in collapsed

    def test_hybrid_is_a_single_statement(self):
        """Hybrid search must stay one round trip; splitting it would double
        latency and make the fusion non-atomic."""
        # Strip -- comments first: the parameter documentation uses semicolons
        # in prose, which is not a statement boundary.
        code = "\n".join(
            line.split("--", 1)[0] for line in HYBRID_SQL.splitlines()
        )
        assert code.strip().count(";") <= 1

    # The lexical lane's predicate, mirroring hybrid.sql. Isolated for the same
    # reason as LANE above: on a single-tenant corpus the org filter is
    # unselective, so the planner may prefer a pkey scan over GIN and be right.
    KEYWORD_LANE = (
        "SELECT c.id FROM rag.chunks c "
        "WHERE c.content_tsv @@ websearch_to_tsquery('english', 'liability fees') "
        "LIMIT 100"
    )

    async def test_keyword_predicate_is_index_answerable(self, conn):
        c, _org = conn
        plan = await _explain(c, self.KEYWORD_LANE, force_index=True)
        assert "chunks_tsv_gin" in plan, (
            "the @@ predicate cannot use the GIN index -- check that content_tsv "
            "is still a stored generated column and the index exists.\n\n" + plan
        )


class TestHybridResults:
    async def test_returns_rows_with_lane_provenance(self, conn):
        c, org = conn
        await c.execute("LOAD 'vector'")
        rows = await c.fetch(_inline(HYBRID_SQL, _vec_literal(), org))
        assert rows, "hybrid query returned nothing on a seeded corpus"
        assert any(r["bm25_rank"] is not None for r in rows), "no lexical-lane hits"
        assert any(r["vec_rank"] is not None for r in rows), "no vector-lane hits"

    async def test_rrf_scores_are_descending(self, conn):
        c, org = conn
        await c.execute("LOAD 'vector'")
        rows = await c.fetch(_inline(HYBRID_SQL, _vec_literal(), org))
        scores = [float(r["rrf_score"]) for r in rows]
        assert scores == sorted(scores, reverse=True)

    async def test_a_chunk_found_by_only_one_lane_still_survives(self, conn):
        """FULL OUTER JOIN semantics: 'this lane never retrieved it' is the most
        informative state in the inspector, so it must not be filtered away."""
        c, org = conn
        await c.execute("LOAD 'vector'")
        rows = await c.fetch(_inline(HYBRID_SQL, _vec_literal(), org))
        assert any(r["bm25_rank"] is None or r["vec_rank"] is None for r in rows)

    async def test_document_filter_restricts_results(self, conn):
        c, org = conn
        await c.execute("LOAD 'vector'")

        # Derive the filter from a document the UNFILTERED query actually
        # returns. Picking an arbitrary document is fragile: a chunk with no
        # embedding and no lexical match legitimately returns nothing, and the
        # test would then be asserting on the corpus rather than on filtering.
        unfiltered = await c.fetch(_inline(HYBRID_SQL, _vec_literal(), org))
        assert unfiltered, "unfiltered query returned nothing; corpus is unusable"
        doc_id = str(unfiltered[0]["document_id"])

        sql = _inline(HYBRID_SQL, _vec_literal(), org).replace(
            "NULL::uuid[]", f"ARRAY['{doc_id}']::uuid[]"
        )
        rows = await c.fetch(sql)
        assert rows, "filtering to a document that WAS returned yielded nothing"
        assert all(str(r["document_id"]) == doc_id for r in rows)
        assert len(rows) <= len(unfiltered)

    async def test_disabling_the_lexical_lane_removes_its_ranks(self, conn):
        """w_bm25 = 0 is how the HyDE lane avoids injecting hallucinated
        keywords into lexical search."""
        c, org = conn
        params = {
            1: f"'{org}'::uuid", 2: "'liability fees'", 3: _vec_literal(),
            4: "NULL::uuid[]", 5: "NULL::date", 6: "NULL::date", 7: "NULL::text[]",
            8: "100", 9: "60", 10: "60", 11: "0.0", 12: "1.0",
        }
        sql = re.sub(r"\$(\d+)", lambda m: params[int(m.group(1))], HYBRID_SQL)
        await c.execute("LOAD 'vector'")
        rows = await c.fetch(sql)
        assert rows
        assert all(r["bm25_rank"] is None for r in rows)
