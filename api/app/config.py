"""Central configuration. Every tunable in the retrieval and generation pipeline
lives here so an eval run can snapshot the exact config that produced it."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


_REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    # Look in the repo root as well as cwd, so the same config loads whether a
    # command is run from the repo root, from api/, or from a container.
    # Later files win, so a cwd-local .env can still override.
    model_config = SettingsConfigDict(
        env_file=(_REPO_ROOT / ".env", ".env"),
        extra="ignore",
    )

    # ─── Database ────────────────────────────────────────────────────────────
    database_url: str = "postgresql+asyncpg://ekis:ekis_dev_password@localhost:5433/ekis"
    db_pool_min: int = 2
    db_pool_max: int = 20

    # ─── Services ────────────────────────────────────────────────────────────
    embed_service_url: str = "http://localhost:8001"
    # Resolved absolutely below. A relative storage path silently depends on the
    # process working directory, so the API started from api/ and a worker
    # started from the repo root would write to and read from different trees.
    storage_dir: Path = _REPO_ROOT / "storage"

    # ─── LLM provider ────────────────────────────────────────────────────────
    # offline: retrieval is fully real, generation is deterministic and stubbed.
    # This is a first-class mode, not a test fixture — the whole UI must be
    # exercisable without an API key.
    llm_mode: Literal["offline", "live"] = "offline"

    # Which generation backend to use. "offline" needs no key at all.
    # llm_mode is kept for backwards compatibility: LLM_MODE=live without an
    # explicit LLM_PROVIDER resolves to anthropic.
    llm_provider: Literal["offline", "anthropic", "gemini"] = "offline"

    anthropic_api_key: str = ""
    gemini_api_key: str = ""
    # Pinned, deliberately not a "-latest" alias: an eval whose model can change
    # underneath it is not reproducible, and the config snapshot would record an
    # alias rather than what actually ran.
    gemini_model: str = "gemini-3.8-flash"
    # Must differ from gemini_model; a model may not grade its own output.
    gemini_judge_model: str = "gemini-3.5-flash"

    model_answer: str = "claude-opus-5"
    model_rewrite: str = "claude-haiku-4-5"
    model_decompose: str = "claude-haiku-4-5"
    # NEVER the same model that generated the answer: self-preference bias
    # measurably inflates faithfulness scores.
    model_verify: str = "claude-sonnet-5"
    answer_effort: Literal["low", "medium", "high", "xhigh", "max"] = "high"
    answer_max_tokens: int = 8000

    # ─── Embeddings ──────────────────────────────────────────────────────────
    embed_model: str = "BAAI/bge-m3"
    rerank_model: str = "BAAI/bge-reranker-v2-m3"
    embed_dim: int = 1024

    # ─── Retrieval ───────────────────────────────────────────────────────────
    # k=60 (Cormack/Clarke/Buttcher). Small k makes the RRF curve steep, which
    # suits a system with no reranker. We have a cross-encoder downstream, so
    # recall INTO the reranker is the objective and precision@1 out of SQL is
    # nearly worthless. Weights are parameters, not constants — tune on the
    # eval set, do not guess. Technical corpora often want w_bm25 > 1.0.
    rrf_k: int = 60
    lane_k: int = 100
    fuse_out_n: int = 60
    w_bm25: float = 1.0
    w_vec: float = 1.0

    # Lane weights for cross-lane fusion of multi-query results.
    lane_weight_original: float = 1.5
    lane_weight_subquery: float = 1.0
    lane_weight_hyde: float = 0.8
    lane_weight_keyword: float = 1.0

    # Reranker backend.
    #   cross-encoder : bge-reranker-v2-m3. Best quality. Needs a GPU to be
    #                   practical -- measured ~0.1 pairs/s on this CPU, which
    #                   makes a 60-candidate rerank take minutes per query.
    #   lexical       : query-term overlap. No model, sub-millisecond, clearly
    #                   worse than a cross-encoder but far better than nothing.
    #   none          : keep RRF order.
    # Default is lexical because a reranker that makes /ask hang is not a
    # reranker, and an unbounded cold-start download on first request is worse
    # than a known-weaker score.
    rerank_backend: Literal["cross-encoder", "lexical", "none"] = "lexical"
    rerank_top_n: int = 12
    # Two floors. This is the main structural defence against
    # hallucination-by-context-stuffing: a question with no answer in the corpus
    # must reach the model with little evidence so it can correctly refuse.
    rerank_score_floor: float = 0.25
    rerank_relative_floor: float = 0.50
    rerank_batch_tokens: int = 8192

    hnsw_ef_search: int = 100
    hnsw_max_scan_tuples: int = 20000

    # Hard cap despite Opus 5's 1M window: cost is linear in input tokens, and
    # answer quality degrades past ~20 well-ranked chunks as distractors dilute
    # attention. A large context window is a capability, not a strategy.
    max_context_tokens: int = 60_000
    neighbor_expand_top_k: int = 4

    # ─── Ingestion ───────────────────────────────────────────────────────────
    ocr_engine: Literal["paddle", "tesseract"] = "paddle"
    ocr_dpi: int = 300
    ocr_low_confidence: float = 0.80
    max_upload_bytes: int = 52_428_800  # 50 MB
    chunk_target_tokens: int = 400
    chunk_min_tokens: int = 128
    chunk_max_tokens: int = 512
    chunk_breakpoint_percentile: float = 95.0
    chunk_overlap_sentences: int = 1
    chunk_window_sentences: int = 1

    # ─── Jobs ────────────────────────────────────────────────────────────────
    worker_concurrency: int = 3
    job_max_attempts: int = 3
    job_heartbeat_seconds: int = 15
    job_stale_after_seconds: int = 300

    # ─── Verification ────────────────────────────────────────────────────────
    verify_enabled: bool = True
    # Always verify when Stage A finds uncited spans or the answer is long;
    # otherwise sample. Drops ~$0.025/query to ~$0.006 while keeping full
    # coverage on the answers most likely to be wrong.
    verify_always_over_words: int = 150
    verify_sample_rate: float = 0.20

    # ─── Auth ────────────────────────────────────────────────────────────────
    rag_service_secret: str = "change-me"
    service_token_audience: str = "ekis-api"
    service_token_issuer: str = "ekis-web"

    @field_validator("storage_dir")
    @classmethod
    def _absolute_storage(cls, v: Path) -> Path:
        """A relative STORAGE_DIR from the environment is resolved against the
        repo root, never the current working directory."""
        return v if v.is_absolute() else (_REPO_ROOT / v).resolve()

    @property
    def asyncpg_dsn(self) -> str:
        """asyncpg wants a bare postgresql:// DSN, not SQLAlchemy's +asyncpg form."""
        return self.database_url.replace("postgresql+asyncpg://", "postgresql://")

    @property
    def provider(self) -> str:
        """Resolved generation backend.

        An explicit LLM_PROVIDER wins. Otherwise LLM_MODE=live means anthropic,
        so existing configs keep working.
        """
        if self.llm_provider != "offline":
            return self.llm_provider
        return "anthropic" if self.llm_mode == "live" else "offline"

    @property
    def is_offline(self) -> bool:
        return self.provider == "offline"

    def snapshot(self) -> dict:
        """Config snapshot stored on every eval run, so a number is always
        traceable to the settings that produced it."""
        keys = (
            "model_answer", "model_rewrite", "model_verify", "answer_effort",
            "embed_model", "rerank_model", "rrf_k", "lane_k", "fuse_out_n",
            "w_bm25", "w_vec", "rerank_top_n", "rerank_score_floor",
            "rerank_relative_floor", "rerank_backend", "hnsw_ef_search", "max_context_tokens",
            "chunk_target_tokens", "chunk_min_tokens", "chunk_max_tokens",
            "chunk_breakpoint_percentile", "ocr_engine", "llm_mode", "gemini_model", "gemini_judge_model",
        )
        return {k: getattr(self, k) for k in keys}


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
