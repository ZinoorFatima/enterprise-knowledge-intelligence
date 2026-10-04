# Enterprise Knowledge Intelligence System

A retrieval-augmented generation platform built around a single question: **can you check the answer?**

Most RAG systems are a black box — a question goes in, a confident paragraph comes out, and nothing in between is inspectable. That is fine for a demo and unacceptable anywhere a wrong answer has consequences. This system surfaces the rewritten query, both retrieval lanes, the fusion, every rerank movement, the exact cited spans, and a per-claim audit of the answer — not as a debug mode, but as the primary interface.

---

## What it does

Upload PDFs. Ask questions across the whole corpus. Get an answer where:

- **Every citation resolves to an exact page and character span**, not a document-level pointer.
- **Every factual claim is checked** against the retrieved evidence by a *different* model than the one that wrote it.
- **A question the corpus cannot answer produces a refusal**, not a plausible fabrication.
- **Every number the system reports is either measured or labelled as not measured.**

### Features

| | |
|---|---|
| **Ingestion** | PDF parsing (PyMuPDF), scanned-page and garbage-text-layer detection, OCR fallback, content-hash dedupe |
| **Chunking** | Semantic breakpoint detection over sentence embeddings, with exact character provenance |
| **Retrieval** | Hybrid BM25-style + dense vector in one SQL statement, fused with Reciprocal Rank Fusion |
| **Ranking** | Cross-encoder or lexical reranking, with absolute and relative score floors |
| **Generation** | Grounded synthesis with inline citations resolved by index arithmetic |
| **Validation** | Per-claim entailment verification, independent verifier model enforced in code |
| **Evaluation** | Context Precision / Recall, Faithfulness, Answer Relevancy, nDCG, MRR, refusal accuracy, bootstrap CIs |

---

## Architecture

```
Documents
    │
    ▼
Parser / OCR ──── scanned + broken-text-layer detection
    │
    ▼
Semantic chunking ──── exact char offsets preserved
    │
    ▼
Embedding (bge-m3, local)
    │
    ▼
Postgres + pgvector ──── vectors AND full text in one table
    │
    ├──────────────┬──────────────┐
    ▼              ▼              │
BM25 lane     Vector lane         │  filters pushed into BOTH lanes
    └──────┬───────┘              │
           ▼                      │
   Reciprocal Rank Fusion ────────┘
           │
           ▼
      Reranker ──── score floors: no evidence ⇒ refuse
           │
           ▼
         Claude ──── grounded, with citations
           │
           ▼
       Verifier ──── per-claim entailment, different model
           │
           ▼
  Answer + citations + faithfulness
```

The query pipeline is a **LangGraph `StateGraph`**. The conditional edges carry the product's guarantees rather than burying them in branches:

```
START ─▶ route_rewrite ─┬▶ rewrite ──┐
                        └▶ skip ─────┤
                                     ▼
              retrieve ▶ fuse ▶ rerank ▶ assemble
                                     │
                        route_after_assemble
                           ┌─────────┴─────────┐
                           ▼                   ▼
                       generate              refuse
                           │                   │
                     route_verify              │
                      ┌────┴────┐              │
                      ▼         ▼              │
                   verify   skip_verify ◀──────┘
```

"Nothing cleared the score floors, so refuse" is a **routing decision**. As an edge it is reviewable; as an `if` halfway down a function it is not. The graph state doubles as the audit trail the retrieval inspector renders, so what you inspect is what produced the answer.

### Trust boundary

The browser never reaches the retrieval service. Every call goes:

```
browser → Next.js route handler (verifies session)
        → mints 120s HS256 service token
        → FastAPI (trusts only the verified `sub` claim)
        → re-validates org membership per request
```

A client-supplied user or org id is never trusted. Membership is re-checked against the database on every request rather than taken from the JWT, so removing someone takes effect immediately instead of when their session expires.

---

## Stack

| Layer | Choice | Why |
|---|---|---|
| Web | Next.js 15 (App Router), React 19, Tailwind v4 | Server components by default; the landing page ships two hydrated islands |
| Auth | Auth.js v5, argon2id + Google OAuth | Edge/Node config split — argon2 and the PG driver cannot run on Edge |
| API | FastAPI, Python 3.11 | OCR, parsing and embeddings are Python-native |
| Orchestration | LangGraph | Conditional edges express the pipeline's guarantees |
| Database | Postgres 16 + pgvector 0.8 | Vectors *and* full text in one table ⇒ hybrid search is one statement |
| Embeddings | `BAAI/bge-m3` (1024-dim, local) | No document text leaves the deployment |
| Reranking | `BAAI/bge-reranker-v2-m3`, or lexical fallback | Cross-encoder needs a GPU to be practical |
| Generation | Claude (`claude-opus-5`) | Behind a provider shim with a fully offline mode |

Schema ownership is split by namespace so two migration tools share one database: **`auth`** is owned by Drizzle, **`rag`** by Alembic. Neither touches the other.

---

## Results

> **Read this section carefully.** The numbers below are from a **5-chunk development corpus in offline mode**. They demonstrate that the harness works; they do **not** demonstrate that retrieval is good. Meaningful figures need a real corpus and a GPU.

### Test suite

**171 tests passing**, including query-plan tests that run `EXPLAIN` against a live Postgres.

```
api/tests/test_chunker.py          chunking + exact char provenance
api/tests/test_fuse.py             RRF, score floors, rerank deltas
api/tests/test_provider_offline.py offline honesty guarantees
api/tests/test_pipeline_graph.py   LangGraph routing + refusal behaviour
api/tests/test_verify.py           verifier fail-closed behaviour
api/tests/test_eval_metrics.py     metric definitions + bounds
api/tests/test_eval_runner.py      end-to-end harness
api/tests/test_hybrid_plan.py      index usage, asserted via EXPLAIN
api/tests/test_ingest.py           real PDF → page spine → chunks → DB
```

### Evaluation run (dev corpus, offline mode)

```
items: 8   provisional: true
  context_precision           1.000
  context_recall              1.000
  faithfulness         not measured
  answer_relevancy            0.486
  ndcg_at_10                  1.000
  mrr                         1.000
  recall_at_20                1.000
  refusal accuracy            0.000  over 2 unanswerable items
```

**What these actually mean:**

- **The 1.000s are not a good sign.** With 5 coarse chunks and one labelled page per question, almost anything retrieved covers the target. The corpus is far too small to discriminate.
- **`faithfulness: not measured`** is the system working correctly. Offline mode cannot judge entailment, so it reports nothing rather than inventing a score.
- **`refusal accuracy: 0.000`** is a real, useful finding — the system answered both deliberately unanswerable questions. With the lexical reranker's 0.25 baseline, weak matches still clear the score floors. This needs tuning, and the metric exists precisely to catch it.
- **`provisional: true`** is attached automatically, with reasons, whenever a run uses unreviewed data or stubbed generation.

### Honesty properties, enforced in code

These are not conventions — they fail the build or raise at runtime:

| Property | Mechanism |
|---|---|
| A metric nothing computed shows "not measured" | `MetricSummary.mean is None` renders as text, never `0` |
| Undefined ≠ zero | Context Precision returns `None` when nothing relevant was retrieved; exclusion count is reported |
| The verifier is never the generator | `assert_independent_verifier()` **raises** — self-grading inflates faithfulness |
| A verdict without a quote is not support | Claims lacking a decisive quote are downgraded to `unsupported` |
| Fabricated citations are rejected | Verdicts citing chunk ids never sent are dropped |
| Unreviewed data cannot look measured | Runs stamped `provisional` with reasons attached |
| Refusal is scored, not hidden | Unanswerable items scored separately as refusal accuracy |

### Bugs found and fixed during development

Each has a regression test:

1. **The HNSW index was silently unused.** `ROW_NUMBER() OVER (ORDER BY embedding <=> $3)` forces a sort over every filtered row before `LIMIT` applies. No error — just latency scaling with corpus size. Restructured: **16.2 ms → 4.96 ms** on 4k rows, reading 100 rows instead of 4000.
2. **Chunk text drifted from its own offsets.** The chunker re-joined stripped sentences with `" "`, but real PDFs separate them with newlines and tabs — so every derived citation page was approximate. Chunks now slice the source directly, verified against a phrase printed on a known page.
3. **nDCG exceeded 1.0** (reported `1.105`). Several chunks covering one labelled page were each credited, so DCG outran IDCG. Pages are now credited once.

---

## Setup

### Requirements

- **PostgreSQL 16+ with pgvector ≥ 0.8** — required for `halfvec` and iterative index scans. The init script fails loudly on older versions rather than degrading silently.
- **Python 3.11+**
- **Node 20+**
- An Anthropic API key — *optional*: `LLM_MODE=offline` runs the entire retrieval half with no key at all.

### 1. Configuration

```bash
cp .env.example .env
```

Generate real secrets (never commit `.env`):

```bash
python -c "import secrets,base64;print(base64.b64encode(secrets.token_bytes(32)).decode())"
```

Set `AUTH_SECRET` and `RAG_SERVICE_SECRET` to separate generated values.

### 2. Database

With Docker:

```bash
docker compose up -d db
```

Or point `DATABASE_URL` at any Postgres 16+ with pgvector 0.8+.

Apply the schema — order matters, because `rag` foreign-keys into `auth`:

```bash
psql "$DATABASE_URL" -f db/init/00-init.sql
npm --prefix web install && npx --prefix web drizzle-kit push
psql "$DATABASE_URL" -f api/alembic/sql/0001_rag_schema.sql
psql "$DATABASE_URL" -f api/alembic/sql/0002_auth_fks.sql
```

### 3. Services

```bash
# Python API
cd api && pip install -e . && uvicorn app.main:app --port 8010
```

```bash
# Web app
cd web && npm install && npm run dev
```

Check readiness before anything else — it reports each dependency separately:

```bash
curl localhost:8010/readyz
# {"postgres":"ok","pgvector":"0.8.7","llm_mode":"offline","chunks":667,"status":"ok"}
```

### 4. Seed a development corpus

```bash
cd api
python scripts/seed_dev.py --bulk 90    # ingests through the REAL pipeline
python scripts/build_golden.py          # golden set, labelled against real doc ids
python scripts/reembed.py --batch 32    # after any embedding model change
```

Then sign up at `http://localhost:3000/sign-up` and upload a PDF.

### 5. Run the evaluation

```bash
python api/scripts/run_eval.py eval/golden/smoke.jsonl
```

---

## Project layout

```
api/                     FastAPI retrieval tier
  app/graph/             LangGraph pipeline: state, nodes, edges
  app/ingest/            parsing, OCR detection, semantic chunking
  app/retrieve/          fusion, score floors, embedders
  app/generate/          provider shim, verifier
  app/eval/              metrics + harness
  app/db/sql/hybrid.sql  the two-lane fused retrieval query
  scripts/               seeding, re-embedding, eval, diagnostics
web/                     Next.js web tier
  app/(marketing)/       public site
  app/(app)/             authenticated app
  app/api/               proxy routes — the only path to FastAPI
  components/app/        ask workspace, inspector, verification, PDF viewer
db/init/                 extensions + schema bootstrap
eval/golden/             version-controlled golden dataset
```

---

## Known limitations

Stated plainly, because a system with no listed limitations has not been evaluated honestly.

- **`bge-m3` runs at ~0.1 chunks/sec on CPU.** Re-embedding a few hundred chunks takes hours. Only a small subset of the dev corpus carries real embeddings; the rest are excluded from the vector lane rather than mixed across incompatible embedding spaces.
- **Postgres full-text is cover density, not BM25.** No IDF, no term saturation, no tunable length normalisation — and `websearch_to_tsquery` uses AND semantics, so one absent word kills the match. The lane sits behind a protocol so ParadeDB `pg_search` can replace it once measurement shows it is the binding constraint.
- **The cross-encoder reranker is impractical on CPU**, so the default is a lexical reranker. It cannot score paraphrase. Set `RERANK_BACKEND=cross-encoder` on a GPU host.
- **The PDF viewer's data path is verified** (range requests, citation resolution) **but pdf.js does not complete a render in the embedded test browser.** Needs confirming in a standard browser. It fails gracefully with an escape-hatch link rather than hanging.
- **Published evaluation figures on the marketing site are targets**, labelled as such, until a run against a reviewed dataset is frozen.

---

## License

MIT
