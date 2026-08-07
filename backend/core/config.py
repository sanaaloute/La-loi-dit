"""Application settings (12-factor: everything via env vars, prefix LEGAL_AI_)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="LEGAL_AI_",
        env_file=[".env", ".env.dev"],
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- application ---
    env: str = "development"
    app_name: str = "Burkina Faso Legal AI"
    app_version: str = "0.1.0"
    secret_key: str = "change-me-in-production"
    data_dir: Path = Path("./data")
    log_level: str = "INFO"
    audit_log_cap: int = 1000

    # --- LLM via LiteLLM (OpenAI-compatible multi-provider) ---
    # provider: openai | anthropic | gemini | deepseek | qwen | kimi | ollama | mock
    llm_provider: str = "mock"
    llm_model: str = "gpt-4o-mini"
    llm_api_key: str = ""
    llm_api_base: str = ""
    llm_temperature: float = 0.1
    llm_max_tokens: int = 4096
    llm_timeout_seconds: float = 60.0
    openrouter_api_key: str = ""  # falls back to llm_api_key when empty
    tokenfree_api_key: str = ""  # falls back to llm_api_key when empty
    # JSON replacing the whole built-in tier catalog (see backend/core/catalog.py)
    tier_catalog_json: str = ""
    # --- token metering / quotas / answer cache / cheap routing ---
    answer_cache_enabled: bool = True
    answer_cache_semantic_threshold: float = 0.98  # cosine floor for near-duplicate hits
    answer_cache_max_index: int = 50  # semantic index size (most recent entries)
    cheap_routing_enabled: bool = True  # simple queries -> tier's cheapest model
    embedding_model: str = "text-embedding-3-small"
    embedding_dimension: int = 384
    embedding_api_base: str = ""  # separate from llm_api_base for split providers
    embedding_api_key: str = ""

    # --- Milvus ---
    milvus_host: str = "localhost"
    milvus_port: int = 19530
    # When set, takes precedence over host/port — e.g. a Milvus Lite file
    # path ("./data/milvus_lite.db") for local dev without a server.
    milvus_uri: str = ""
    milvus_collection: str = "legal_chunks"
    milvus_enabled: bool = False  # auto-fallback to in-memory store when False/unreachable
    milvus_connect_timeout_seconds: float = 15.0  # cold WSL/docker handshakes exceed 3s
    milvus_filter_overfetch: int = 4

    # --- Redis ---
    redis_url: str = "redis://localhost:6379/0"
    redis_enabled: bool = False
    cache_ttl_seconds: int = 3600

    # --- PostgreSQL (defaults to local SQLite for development) ---
    database_url: str = "sqlite+aiosqlite:///./data/legal_ai.db"

    # --- Temporal ---
    temporal_address: str = "localhost:7233"
    temporal_namespace: str = "default"
    temporal_enabled: bool = False
    temporal_task_queue: str = "legal-ai"

    # --- Celery ---
    celery_broker_url: str = "redis://localhost:6379/1"

    # --- auth / security ---
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60
    rate_limit_per_minute: int = 30  # anonymous/IP default; tiers override via catalog

    # --- scalability / high availability ---
    # None => resolved as (env == "production"); set explicitly to override.
    strict_infra: Optional[bool] = None
    web_workers: int = 1  # uvicorn workers (compose sets LEGAL_AI_WEB_WORKERS)

    # --- observability ---
    otel_endpoint: str = "http://localhost:4318"
    otel_enabled: bool = False
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = "http://localhost:3100"

    # --- billing (Paddle; disabled = platform fully functional) ---
    paddle_enabled: bool = False
    paddle_env: str = "sandbox"  # sandbox | production
    paddle_api_key: str = ""
    paddle_webhook_secret: str = ""
    paddle_price_pro: str = ""
    paddle_price_cabinet: str = ""
    paddle_checkout_success_url: str = "http://localhost:3000/tarifs?success=1"
    paddle_checkout_cancel_url: str = "http://localhost:3000/tarifs?canceled=1"

    # --- Tuning (policy/tuning knobs; defaults preserve current behavior) ---
    # retry budgets (bounded loops, see docs/workflow.md "RETRY STRATEGY")
    max_planning_retries: int = 1
    max_retrieval_retries: int = 1
    max_reflection_iterations: int = 1
    max_global_retries: int = 1
    # retrieval / ranking
    default_top_k: int = 8
    retrieval_fetch_k: int = 20  # candidates fetched per worker before fusion/rerank
    rrf_k: int = 60  # reciprocal-rank-fusion constant
    retrieval_similarity_floor: float = 0.7  # strong semantic match floor
    retrieval_weak_similarity_floor: float = 0.25
    retrieval_min_shared_tokens: int = 2
    retrieval_cache_namespace: str = "retrieval:"
    dedup_jaccard_threshold: float = 0.9  # near-duplicate detection
    rerank_similarity_weight: float = 0.75
    rerank_confidence_weight: float = 0.25
    ranking_relevance_weight: float = 0.55
    ranking_authority_weight: float = 0.30
    ranking_confidence_weight: float = 0.15
    planner_aux_top_k: int = 5  # top_k for auxiliary (gov/regulation/case-law/news) tasks
    search_timeout_seconds: float = 8.0  # per-request web-source fetch timeout
    search_max_results_per_source: int = 5
    search_max_content_chars: int = 1200  # per-chunk content cap from web sources
    # confidence / escalation
    confidence_threshold: float = 0.55  # below this the answer carries a low-confidence warning
    human_review_threshold: float = 0.40  # below this, escalate to a human legal expert
    min_evidence_score: float = 0.05  # evidence weaker than this is ignored by ranking
    # agents / guardrails
    context_max_turns: int = 10  # conversation window (turns) loaded into context
    memory_recall_limit: int = 5
    answer_max_bullets: int = 6  # evidence bullets in the template answer
    answer_max_evidence: int = 10  # evidence chunks attached to the FinalAnswer
    input_max_chars: int = 8000  # user queries longer than this is truncated
    # tools
    sandbox_timeout_seconds: float = 5.0
    currency_tool_timeout_seconds: float = 5.0

    # --- chunking / ingestion ---
    chunk_parent_size: int = 1200
    chunk_child_size: int = 350
    chunk_overlap: int = 60
    chunk_max_size: int = 1200
    text_cleaning_min_pages_for_header: int = 3
    text_cleaning_header_min_frequency: float = 0.6
    ingestion_html_timeout_seconds: float = 30.0
    ingestion_freshness_timeout_seconds: float = 20.0
    pdf_parser_max_pages: int = 50

    # --- memory ---
    memory_age_full_penalty_days: float = 90.0
    memory_access_full_penalty_days: float = 30.0
    memory_max_records: int = 10000
    memory_min_importance: float = 0.1
    memory_summary_max_turns: int = 20
    memory_summary_max_len: int = 200

    # --- temporal ---
    temporal_connect_timeout_seconds: float = 5.0
    temporal_activity_timeout_seconds: float = 180.0
    temporal_ingestion_timeout_minutes: int = 30
    temporal_max_turns_per_execution: int = 50

    # --- celery ---
    celery_task_time_limit_seconds: int = 600
    celery_worker_max_tasks_per_child: int = 100
    celery_evaluation_timeout_seconds: int = 1800

    # --- crawler ---
    crawler_max_pages: int = 20
    crawler_max_depth: int = 2
    crawler_delay_seconds: float = 1.0
    crawler_timeout_seconds: float = 20.0
    crawler_user_agent: str = "LegalAI-Burkina-Crawler/1.0 (+offline-first)"
    search_user_agent: str = "LegalAI-BurkinaFaso/1.0 (official-source retriever)"

    # --- export ---
    export_pdf_title: str = "Réponse juridique — Assistant Juridique Burkina Faso"

    @property
    def langfuse_enabled(self) -> bool:
        return bool(self.langfuse_public_key and self.langfuse_secret_key)

    @property
    def strict_infra_enabled(self) -> bool:
        """Strict infrastructure mode: no silent fallbacks, /ready can 503.

        Defaults on in production, off elsewhere unless explicitly set.
        """
        if self.strict_infra is not None:
            return self.strict_infra
        return self.env == "production"

    @property
    def billing_enabled(self) -> bool:
        """True only when Paddle is enabled AND fully configured."""
        return bool(
            self.paddle_enabled
            and self.paddle_api_key
            and self.paddle_price_pro
            and self.paddle_price_cabinet
        )

    def ensure_data_dir(self) -> Path:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        return self.data_dir


@lru_cache
def get_settings() -> Settings:
    return Settings()
