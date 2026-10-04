-- Runs once, on first container start, before any application migration.
--
-- Schema ownership is split so two migration tools can share one database:
--   auth  -> Next.js / Drizzle Kit
--   rag   -> FastAPI / Alembic
-- Neither tool ever touches the other's schema.

CREATE EXTENSION IF NOT EXISTS vector;    -- pgvector: halfvec + hnsw.iterative_scan (needs >= 0.8)
CREATE EXTENSION IF NOT EXISTS citext;    -- case-insensitive email
CREATE EXTENSION IF NOT EXISTS pg_trgm;   -- fuzzy title search in the document library

CREATE SCHEMA IF NOT EXISTS auth;
CREATE SCHEMA IF NOT EXISTS rag;

-- Fail loudly and immediately if the image shipped a pgvector too old for the
-- features the retrieval design depends on, rather than discovering it later
-- as a confusing runtime error.
DO $$
DECLARE v text;
BEGIN
  SELECT extversion INTO v FROM pg_extension WHERE extname = 'vector';
  IF string_to_array(v, '.')::int[] < ARRAY[0,8,0] THEN
    RAISE EXCEPTION 'pgvector % is too old; >= 0.8.0 required (halfvec, iterative_scan)', v;
  END IF;
  RAISE NOTICE 'pgvector % OK', v;
END $$;
