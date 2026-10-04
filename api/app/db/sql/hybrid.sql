-- ═══════════════════════════════════════════════════════════════════════════
-- Hybrid retrieval: two lanes, one statement, fused with Reciprocal Rank Fusion.
--
-- CRITICAL: $3 (the query vector) is bound DIRECTLY as a parameter and is never
-- routed through a `params` CTE. A `<=>` operand emerging from a CTE row can
-- stop pgvector using the HNSW index. The plan then degrades to a full scan
-- with no error and no warning — just a ~200x slowdown. tests/test_hybrid_plan.py
-- asserts `Index Scan using chunks_embedding_hnsw` appears in EXPLAIN.
--
-- Filters are pushed into BOTH lanes. Filtering after fusion would bias the
-- result toward whichever lane happened to surface in-filter documents.
--
-- $1  org_id           uuid
-- $2  query_text       text     -> websearch_to_tsquery ('' disables the lexical lane)
-- $3  query_embedding  halfvec(1024)  (NULL disables the vector lane)
-- $4  doc_ids          uuid[]   (NULL = no filter)
-- $5  date_from        date     (NULL = no filter)
-- $6  date_to          date     (NULL = no filter)
-- $7  tags             text[]   (NULL = no filter; overlap semantics)
-- $8  lane_k           int      per-lane depth (default 100)
-- $9  rrf_k            int      RRF constant (default 60)
-- $10 out_n            int      rows returned -> feeds the reranker (default 60)
-- $11 w_bm25           float8   lane weight (0 disables; HyDE lane passes 0)
-- $12 w_vec            float8   lane weight (0 disables; keyword lane passes 0)
-- ═══════════════════════════════════════════════════════════════════════════

-- Each lane is TWO CTEs: an inner one that is a plain ORDER BY ... LIMIT the
-- index can satisfy, and an outer one that numbers the (already tiny) result.
--
-- Do NOT collapse these into one CTE with ROW_NUMBER() OVER (ORDER BY <=>).
-- A window function's ORDER BY must be computed over the whole filtered set
-- before the outer LIMIT applies, so the planner sorts every candidate row and
-- the HNSW index goes unused -- a silent seq scan, verified by
-- tests/test_hybrid_plan.py. The rank is identical either way; only the plan
-- differs, and it differs by orders of magnitude.
WITH bm25_raw AS (
    SELECT c.id,
           ts_rank_cd(c.content_tsv, websearch_to_tsquery('english', $2), 1) AS score
    FROM rag.chunks c
    WHERE $11 > 0
      AND $2 <> ''
      AND c.org_id = $1
      AND c.content_tsv @@ websearch_to_tsquery('english', $2)
      AND ($4::uuid[] IS NULL OR c.document_id = ANY($4))
      AND ($5::date   IS NULL OR c.source_date >= $5)
      AND ($6::date   IS NULL OR c.source_date <= $6)
      AND ($7::text[] IS NULL OR c.tags && $7)
    ORDER BY score DESC, c.id
    LIMIT $8
),
bm25 AS (
    SELECT id, score, ROW_NUMBER() OVER (ORDER BY score DESC, id) AS rank
    FROM bm25_raw
),
vec_raw AS (
    -- Plain ORDER BY <distance> LIMIT k: the shape pgvector's HNSW index can
    -- answer directly, walking the graph instead of scanning the table.
    SELECT c.id,
           c.embedding <=> $3::halfvec(1024) AS dist
    FROM rag.chunks c
    WHERE $12 > 0
      AND $3::halfvec(1024) IS NOT NULL
      AND c.org_id = $1
      AND c.embedding IS NOT NULL
      AND ($4::uuid[] IS NULL OR c.document_id = ANY($4))
      AND ($5::date   IS NULL OR c.source_date >= $5)
      AND ($6::date   IS NULL OR c.source_date <= $6)
      AND ($7::text[] IS NULL OR c.tags && $7)
    ORDER BY c.embedding <=> $3::halfvec(1024)
    LIMIT $8
),
vec AS (
    SELECT id,
           1 - dist AS cos_sim,                      -- cosine distance -> similarity
           ROW_NUMBER() OVER (ORDER BY dist, id) AS rank
    FROM vec_raw
),
fused AS (
    -- FULL OUTER JOIN, not INNER: a chunk found by only one lane must survive.
    -- "This lane never retrieved it" is the most informative state in the
    -- inspector, and RRF is specifically designed to handle partial overlap.
    SELECT COALESCE(b.id, v.id)                        AS chunk_id,
           COALESCE($11 / ($9 + b.rank), 0.0)
         + COALESCE($12 / ($9 + v.rank), 0.0)          AS rrf_score,
           b.rank  AS bm25_rank,
           v.rank  AS vec_rank,
           b.score AS bm25_score,
           v.cos_sim
    FROM bm25 b
    FULL OUTER JOIN vec v ON b.id = v.id
)
SELECT f.chunk_id,
       f.rrf_score,
       f.bm25_rank,
       f.vec_rank,
       f.bm25_score,
       f.cos_sim,
       c.text,
       c.token_count,
       c.ordinal,
       c.page_start,
       c.page_end,
       c.section_path,
       c.kind,
       c.char_start,
       c.char_end,
       d.id          AS document_id,
       d.title,
       d.filename,
       d.source_date,
       d.tags
FROM fused f
JOIN rag.chunks    c ON c.id = f.chunk_id
JOIN rag.documents d ON d.id = c.document_id
ORDER BY f.rrf_score DESC, f.chunk_id
LIMIT $10;
