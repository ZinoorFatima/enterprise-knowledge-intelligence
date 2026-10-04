-- ═══════════════════════════════════════════════════════════════════════════
-- rag schema — owned by FastAPI/Alembic.
--
-- org_id / owner_id are plain uuid columns here, NOT foreign keys. The cross-
-- schema FKs to auth.organizations / auth.users are added in migration 0002,
-- which runs after the Drizzle auth migration has created those tables. This
-- decoupling lets the ingestion pipeline be built and tested before the web
-- app exists.
-- ═══════════════════════════════════════════════════════════════════════════

CREATE TYPE rag.doc_status AS ENUM
  ('uploaded','parsing','ocr','chunking','embedding','ready','failed');
CREATE TYPE rag.job_status AS ENUM
  ('queued','running','succeeded','failed','canceled');
CREATE TYPE rag.chunk_kind AS ENUM
  ('prose','table','caption','list','heading');

-- ─────────────────────────────────────────────────────── documents
CREATE TABLE rag.documents (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id         uuid NOT NULL,
  owner_id       uuid NOT NULL,
  title          text NOT NULL,
  filename       text NOT NULL,
  content_sha256 bytea NOT NULL,
  storage_key    text NOT NULL,
  mime_type      text NOT NULL DEFAULT 'application/pdf',
  byte_size      bigint NOT NULL,
  page_count     int,
  status         rag.doc_status NOT NULL DEFAULT 'uploaded',
  language       text,
  source_date    date,                       -- effective/published date; the date-range filter
  tags           text[] NOT NULL DEFAULT '{}',
  meta           jsonb NOT NULL DEFAULT '{}',
  full_text      text,                       -- normalized; the coordinate space for ALL char offsets
  scanned_pages  int NOT NULL DEFAULT 0,
  ocr_mean_conf  real,
  error          text,
  created_at     timestamptz NOT NULL DEFAULT now(),
  updated_at     timestamptz NOT NULL DEFAULT now(),
  -- Content-hash dedupe, per tenant: re-uploading the same bytes returns the
  -- existing document instead of paying for OCR and embedding twice.
  CONSTRAINT documents_org_sha_uniq UNIQUE (org_id, content_sha256)
);
CREATE INDEX documents_org_created_idx ON rag.documents (org_id, created_at DESC);
CREATE INDEX documents_org_status_idx  ON rag.documents (org_id, status);
CREATE INDEX documents_source_date_idx ON rag.documents (org_id, source_date);
CREATE INDEX documents_tags_gin        ON rag.documents USING gin (tags);
CREATE INDEX documents_meta_gin        ON rag.documents USING gin (meta jsonb_path_ops);
CREATE INDEX documents_title_trgm      ON rag.documents USING gin (title gin_trgm_ops);

-- ─────────────────────────────────────────────────────── pages (provenance spine)
-- char_start/char_end index into documents.full_text. Chunks live in the SAME
-- coordinate space, so chunk -> page is an exact bisect, never a heuristic.
-- This table is what makes clickable citations possible.
CREATE TABLE rag.pages (
  id                bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  document_id       uuid NOT NULL REFERENCES rag.documents(id) ON DELETE CASCADE,
  page_number       int  NOT NULL CHECK (page_number >= 1),   -- 1-indexed
  char_start        int  NOT NULL,
  char_end          int  NOT NULL,
  text              text NOT NULL,
  extraction_method text NOT NULL
    CHECK (extraction_method IN ('pymupdf','ocr_paddle','ocr_tesseract','hybrid')),
  ocr_confidence    real,                    -- NULL when not OCR'd
  is_scanned        boolean NOT NULL DEFAULT false,
  width  real, height real, rotation int NOT NULL DEFAULT 0,
  -- [{t,x0,y0,x1,y1}] normalized 0..1 of the UNROTATED page, for the PDF
  -- viewer's highlight overlay.
  line_boxes        jsonb NOT NULL DEFAULT '[]',
  CONSTRAINT pages_doc_num_uniq UNIQUE (document_id, page_number)
);
CREATE INDEX pages_doc_span_idx ON rag.pages (document_id, char_start, char_end);

-- ─────────────────────────────────────────────────────── chunks (retrieval unit)
CREATE TABLE rag.chunks (
  id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  document_id  uuid NOT NULL REFERENCES rag.documents(id) ON DELETE CASCADE,
  -- DENORMALIZED from documents. Every metadata filter must be evaluable
  -- INSIDE each retrieval lane's WHERE, on the same table as the index. A join
  -- to documents inside the vector lane forces the planner to either post-filter
  -- the HNSW output (returning fewer than k rows) or abandon the index entirely.
  org_id       uuid NOT NULL,
  source_date  date,
  tags         text[] NOT NULL DEFAULT '{}',

  ordinal      int  NOT NULL,
  text         text NOT NULL,
  token_count  int  NOT NULL,
  char_start   int  NOT NULL,                -- into documents.full_text
  char_end     int  NOT NULL,
  page_start   int  NOT NULL,                -- 1-indexed; != page_end when it straddles a break
  page_end     int  NOT NULL,
  section_path text,                         -- "3 Risk Factors > 3.2 Credit Risk"
  kind         rag.chunk_kind NOT NULL DEFAULT 'prose',

  embedding    halfvec(1024),                -- bge-m3, L2-normalized; halfvec halves storage
  content_tsv  tsvector GENERATED ALWAYS AS (
                 setweight(to_tsvector('english', coalesce(section_path,'')), 'A') ||
                 setweight(to_tsvector('english', text), 'B')
               ) STORED,
  created_at   timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT chunks_doc_ordinal_uniq UNIQUE (document_id, ordinal)
);

-- Lane B: vector. HNSW not IVFFlat — this corpus ingests continuously, and
-- IVFFlat centroids computed at build time go stale on every later insert,
-- degrading recall silently. m/ef_construction are above default because
-- ingest is a background job: build time is free, query recall is not.
CREATE INDEX chunks_embedding_hnsw ON rag.chunks
  USING hnsw (embedding halfvec_cosine_ops) WITH (m = 32, ef_construction = 200);
-- Lane A: full text.
CREATE INDEX chunks_tsv_gin      ON rag.chunks USING gin (content_tsv);
-- Filters, on this table so both lanes can use them.
CREATE INDEX chunks_org_idx      ON rag.chunks (org_id);
CREATE INDEX chunks_doc_ord_idx  ON rag.chunks (document_id, ordinal);
CREATE INDEX chunks_org_date_idx ON rag.chunks (org_id, source_date);
CREATE INDEX chunks_tags_gin     ON rag.chunks USING gin (tags);

-- Keep the denormalized columns honest. Cheap: fires only on a rare operation.
CREATE FUNCTION rag.sync_chunk_meta() RETURNS trigger AS $$
BEGIN
  UPDATE rag.chunks
     SET tags = NEW.tags, source_date = NEW.source_date, org_id = NEW.org_id
   WHERE document_id = NEW.id;
  RETURN NEW;
END $$ LANGUAGE plpgsql;

CREATE TRIGGER documents_meta_sync
  AFTER UPDATE OF tags, source_date, org_id ON rag.documents
  FOR EACH ROW EXECUTE FUNCTION rag.sync_chunk_meta();

-- ─────────────────────────────────────────────────────── ingestion jobs
-- Postgres IS the queue (SELECT ... FOR UPDATE SKIP LOCKED) and the source of
-- truth. No Redis in v1. LISTEN/NOTIFY carries progress to the SSE endpoint.
CREATE TABLE rag.ingestion_jobs (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id         uuid NOT NULL,
  document_id    uuid REFERENCES rag.documents(id) ON DELETE CASCADE,
  created_by     uuid,
  status         rag.job_status NOT NULL DEFAULT 'queued',
  stage          text NOT NULL DEFAULT 'queued',
  progress       real NOT NULL DEFAULT 0 CHECK (progress BETWEEN 0 AND 1),
  pages_done     int NOT NULL DEFAULT 0,
  pages_total    int,
  chunks_written int NOT NULL DEFAULT 0,
  attempt        int NOT NULL DEFAULT 0,
  error          text,
  error_class    text,
  stage_timings  jsonb NOT NULL DEFAULT '{}',   -- {"extract": 4.1, "ocr": 61.3} seconds
  -- Stage checkpoint: a retry resumes from the last completed stage, so an
  -- embedding failure does not re-run 90 seconds of OCR.
  completed_stages text[] NOT NULL DEFAULT '{}',
  queued_at      timestamptz NOT NULL DEFAULT now(),
  started_at     timestamptz,
  finished_at    timestamptz,
  heartbeat_at   timestamptz
);
CREATE INDEX jobs_org_status_idx ON rag.ingestion_jobs (org_id, status, queued_at DESC);
-- The claim query's index: oldest queued job first.
CREATE INDEX jobs_claim_idx      ON rag.ingestion_jobs (status, queued_at)
  WHERE status = 'queued';
-- The stale-job reaper's index. Without a reaper, a killed worker leaves a job
-- spinning in the UI forever.
CREATE INDEX jobs_stale_idx      ON rag.ingestion_jobs (heartbeat_at)
  WHERE status = 'running';

-- ─────────────────────────────────────────────────────── conversations
CREATE TABLE rag.threads (
  id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id     uuid NOT NULL,
  user_id    uuid NOT NULL,
  title      text NOT NULL DEFAULT 'New conversation',
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX threads_org_user_idx ON rag.threads (org_id, user_id, updated_at DESC);

CREATE TABLE rag.queries (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id           uuid NOT NULL,
  user_id          uuid NOT NULL,
  thread_id        uuid REFERENCES rag.threads(id) ON DELETE CASCADE,
  text             text NOT NULL,
  rewrite          jsonb NOT NULL DEFAULT '{}',  -- {standalone, subqueries[], hyde, keywords[], intent}
  filters          jsonb NOT NULL DEFAULT '{}',
  stage_latency_ms jsonb NOT NULL DEFAULT '{}',
  created_at       timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX queries_org_created_idx ON rag.queries (org_id, created_at DESC);
CREATE INDEX queries_thread_idx      ON rag.queries (thread_id, created_at);

-- The inspector renders from STORED truth rather than re-running retrieval,
-- so what the user inspects is exactly what produced the answer.
CREATE TABLE rag.retrieval_traces (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  query_id    uuid NOT NULL REFERENCES rag.queries(id) ON DELETE CASCADE,
  lanes       jsonb NOT NULL DEFAULT '{}',   -- per-lane raw results + timings
  fused       jsonb NOT NULL DEFAULT '[]',   -- [{chunk_id, rrf, bm25_rank, vec_rank}]
  reranked    jsonb NOT NULL DEFAULT '[]',   -- [{chunk_id, score, delta, in_context}]
  corpus_size int,                           -- docs before filtering
  filtered_size int,                         -- docs after — the "1,284 -> 214" line
  created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX traces_query_idx ON rag.retrieval_traces (query_id);

CREATE TABLE rag.answers (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  query_id      uuid NOT NULL REFERENCES rag.queries(id) ON DELETE CASCADE,
  model         text NOT NULL,
  effort        text,
  llm_mode      text NOT NULL DEFAULT 'live',  -- 'offline' answers are never counted as measured
  text          text NOT NULL,
  blocks        jsonb NOT NULL DEFAULT '[]',   -- raw content blocks incl. per-block citations[]
  stop_reason   text,
  request_id    text,
  input_tokens  int, output_tokens int,
  cache_read_input_tokens int, cache_creation_input_tokens int,
  cost_usd      numeric(12,6),
  faithfulness  numeric(5,4),
  verification  jsonb,                         -- {claims:[...], verifier_model, checked_at}
  created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX answers_query_idx   ON rag.answers (query_id);
CREATE INDEX answers_created_idx ON rag.answers (created_at DESC);

-- Resolved citations. page_number is computed from char offsets, so it is
-- exact. citation_page_accuracy in the eval harness asserts this stays 1.0.
CREATE TABLE rag.citations (
  id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  answer_id      uuid NOT NULL REFERENCES rag.answers(id) ON DELETE CASCADE,
  marker         int  NOT NULL,               -- the [[c1]] number shown inline
  block_index    int  NOT NULL,
  citation_index int  NOT NULL,
  chunk_id       bigint REFERENCES rag.chunks(id) ON DELETE SET NULL,
  document_id    uuid   REFERENCES rag.documents(id) ON DELETE SET NULL,
  page_number    int,
  cited_text     text NOT NULL,
  char_start     int, char_end int,           -- into documents.full_text, for exact highlight
  created_at     timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT citations_answer_block_uniq UNIQUE (answer_id, block_index, citation_index)
);
CREATE INDEX citations_answer_idx   ON rag.citations (answer_id);
CREATE INDEX citations_doc_page_idx ON rag.citations (document_id, page_number);

-- ─────────────────────────────────────────────────────── evaluation
CREATE TABLE rag.eval_datasets (
  id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id     uuid NOT NULL,
  name       text NOT NULL,
  version    int  NOT NULL DEFAULT 1,
  item_count int  NOT NULL DEFAULT 0,
  -- Any unreviewed item makes every run against this dataset 'provisional'.
  -- This is the mechanism that stops synthetic numbers becoming the headline.
  reviewed   boolean NOT NULL DEFAULT false,
  created_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT eval_datasets_name_version_uniq UNIQUE (org_id, name, version)
);

CREATE TABLE rag.eval_items (
  id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  dataset_id         uuid NOT NULL REFERENCES rag.eval_datasets(id) ON DELETE CASCADE,
  question           text NOT NULL,
  ground_truth       text,
  relevant_doc_ids   uuid[]   NOT NULL DEFAULT '{}',
  -- relevant_pages is the DURABLE label; relevant_chunk_ids is only a fast path.
  -- Chunk ids die the moment you change the chunker — which is precisely the
  -- experiment the eval exists to measure. Page labels survive re-chunking.
  relevant_pages     jsonb    NOT NULL DEFAULT '[]',   -- [{document_id, page}]
  relevant_chunk_ids bigint[] NOT NULL DEFAULT '{}',
  filters            jsonb    NOT NULL DEFAULT '{}',
  difficulty         text CHECK (difficulty IN ('single_hop','multi_hop','aggregation','no_answer')),
  -- 10-15% of a healthy dataset is 'refuse'. Without them, a system that answers
  -- everything confidently scores well and you will ship it.
  expected_behavior  text NOT NULL DEFAULT 'answer'
                     CHECK (expected_behavior IN ('answer','refuse')),
  notes              text,
  reviewed           boolean NOT NULL DEFAULT false
);
CREATE INDEX eval_items_dataset_idx ON rag.eval_items (dataset_id);

CREATE TABLE rag.eval_runs (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id      uuid NOT NULL,
  dataset_id  uuid NOT NULL REFERENCES rag.eval_datasets(id),
  git_sha     text,
  config      jsonb NOT NULL DEFAULT '{}',    -- full pipeline config snapshot
  status      rag.job_status NOT NULL DEFAULT 'queued',
  provisional boolean NOT NULL DEFAULT false,
  llm_mode    text NOT NULL DEFAULT 'live',
  n_items     int,
  -- The four headline metrics.
  context_precision numeric(5,4),
  context_recall    numeric(5,4),
  faithfulness      numeric(5,4),
  answer_relevancy  numeric(5,4),
  -- Free deterministic metrics — no judge required.
  ndcg_at_10  numeric(5,4),
  mrr         numeric(5,4),
  recall_at_20 numeric(5,4),
  -- Should be 1.0. If it isn't, citation resolution is broken.
  citation_page_accuracy numeric(5,4),
  -- Reported separately rather than folded into relevancy: on a 'refuse' item
  -- the correct behaviour is refusal, and zeroing relevancy would penalize it.
  refusal_accuracy  numeric(5,4),
  -- How many items had context_precision undefined (nothing relevant retrieved).
  -- Reported so a system that retrieves nothing cannot score flatteringly.
  precision_undefined_count int NOT NULL DEFAULT 0,
  p50_latency_ms int, p95_latency_ms int,
  total_cost_usd numeric(12,6),
  judge_models   jsonb NOT NULL DEFAULT '{}',
  error          text,
  started_at  timestamptz,
  finished_at timestamptz,
  created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX eval_runs_org_started_idx ON rag.eval_runs (org_id, started_at DESC);

CREATE TABLE rag.eval_results (
  id      bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  run_id  uuid NOT NULL REFERENCES rag.eval_runs(id) ON DELETE CASCADE,
  item_id uuid NOT NULL REFERENCES rag.eval_items(id) ON DELETE CASCADE,
  answer    text,
  retrieved jsonb NOT NULL DEFAULT '[]',
  context_precision numeric(5,4),   -- NULL = undefined, excluded from the mean
  context_recall    numeric(5,4),
  faithfulness      numeric(5,4),
  answer_relevancy  numeric(5,4),
  ndcg_at_10        numeric(5,4),
  reciprocal_rank   numeric(5,4),
  refused    boolean,
  latency_ms int,
  cost_usd   numeric(12,6),
  judge_raw  jsonb,
  error      text,
  CONSTRAINT eval_results_run_item_uniq UNIQUE (run_id, item_id)
);
CREATE INDEX eval_results_run_idx ON rag.eval_results (run_id);

-- Verifier calibration: human labels for a sample of claims, so the UI can show
-- measured verifier precision/recall next to the faithfulness score. A
-- faithfulness number with no measured verifier accuracy is decoration.
CREATE TABLE rag.verifier_labels (
  id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  answer_id    uuid NOT NULL REFERENCES rag.answers(id) ON DELETE CASCADE,
  claim_index  int  NOT NULL,
  human_label  text NOT NULL CHECK (human_label IN ('supported','partially_supported','unsupported')),
  model_label  text NOT NULL CHECK (model_label IN ('supported','partially_supported','unsupported')),
  labeled_by   uuid,
  labeled_at   timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT verifier_labels_uniq UNIQUE (answer_id, claim_index)
);
