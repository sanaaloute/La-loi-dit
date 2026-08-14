"""Application settings (12-factor: everything via env vars, prefix LEGAL_AI_)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Optional

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

#: Dependency names /ready reports on; ``strict_critical_components`` is
#: validated against this set so a typo fails fast at boot instead of
#: silently never matching a check.
KNOWN_INFRA_COMPONENTS = frozenset(
    {
        "milvus",
        "postgres",
        "database_probe",
        "vector_store_probe",
        "cache_probe",
        "redis",
        "llm",
        "embeddings",
        "user_store",
        "memory_store",
        "legal_graph",
    }
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="LEGAL_AI_",
        # ``.env.dev`` is loaded after ``.env`` so local overrides (Milvus Lite
        # URI, ./data dir, dev LLM) win over the docker/production defaults.
        env_file=[".env", ".env.dev"],
        env_file_encoding="utf-8",
        extra="ignore",
        # Settings fields prefixed ``model_`` (e.g. ``model_role_routing_enabled``)
        # would otherwise trip pydantic's protected-namespace warning.
        protected_namespaces=(),
    )

    # --- application ---
    env: str = "development"
    # When true, index data/legal_docs in the background at every boot.
    # Idempotent (content-hash versioning skips unchanged docs) and guarded
    # against multi-worker double-runs via a lock file in the data dir.
    ingest_on_startup: bool = False
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
    # Dedicated Ollama Cloud key; falls back to llm_api_key (documented for
    # single-key setups where the main key IS the ollama.com key).
    ollama_api_key: str = ""
    # Comma-separated providers tried (in order) when the primary LLM fails or
    # returns an empty completion. Providers without a configured API key are
    # skipped silently, so the chain is inert in key-less/offline setups.
    llm_fallback_providers: str = "openrouter,tokenfree"
    # JSON replacing the whole built-in tier catalog (see backend/core/catalog.py)
    tier_catalog_json: str = ""
    # --- token metering / quotas / answer cache / cheap routing ---
    answer_cache_enabled: bool = True
    answer_cache_semantic_threshold: float = 0.98  # cosine floor for near-duplicate hits
    answer_cache_max_index: int = 50  # semantic index size (most recent entries)
    cheap_routing_enabled: bool = True  # simple queries -> tier's cheapest model
    # --- per-node-role model routing (spec §46: cheap models for simple nodes) ---
    # OFF by default (zero behavior change). When ON, each graph node's LLM
    # calls go to the role's override model (same provider as the request's
    # resolved model); roles without an override keep the request's model.
    # Intended mapping: classification_model -> context/memory (cheap),
    # planner_model -> planner (cheap), analysis_model -> reasoning/reflection,
    # synthesis_model -> response_generator (strongest, final answer).
    model_role_routing_enabled: bool = False
    planner_model: Optional[str] = None
    classification_model: Optional[str] = None
    analysis_model: Optional[str] = None
    synthesis_model: Optional[str] = None
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
    retrieval_cache_ttl_seconds: int = 300  # short TTL so re-indexing is visible quickly

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
    rate_limit_per_minute: int = 10_000  # dev: effectively unlimited; tighten at deployment
    rate_limit_per_second: int = 5  # per-user/IP per-second burst cap
    single_session_per_user: bool = True  # invalidate previous tokens on new login
    # Comma-separated browser origins allowed to call the API directly (CORS).
    # Needed for real-time SSE: bypassing the Next.js /backend-api proxy with
    # NEXT_PUBLIC_API_URL requires the API to accept cross-origin requests.
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"

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
    max_retrieval_retries: int = 1
    max_reflection_iterations: int = 1
    # retrieval / ranking
    default_top_k: int = 8
    retrieval_fetch_k: int = 20  # candidates fetched per worker before fusion/rerank
    rerank_llm_enabled: bool = True  # LLM rescore blended into rerank (one extra call per retrieval branch)
    rrf_k: int = 60  # reciprocal-rank-fusion constant
    retrieval_similarity_floor: float = 0.45  # strong semantic match floor
    retrieval_weak_similarity_floor: float = 0.25
    retrieval_min_shared_tokens: int = 2
    # Domain filter (spec §18): when the query clearly maps to legal domains
    # (same taxonomy as ingestion's infer_legal_domains), off-domain chunks are
    # dropped before ranking; untagged chunks and the empty-match fallback are kept.
    retrieval_domain_filter_enabled: bool = True
    retrieval_cache_namespace: str = "retrieval:"
    dedup_jaccard_threshold: float = 0.9  # near-duplicate detection
    rerank_similarity_weight: float = 0.75
    rerank_confidence_weight: float = 0.25
    # --- reranker provider (spec §17/§47) ---
    # "heuristic" (default, fully offline) | "api" (cross-encoder endpoint:
    # BGE/Qwen/Cohere rerank via a Cohere-style /rerank API). "api" without
    # full credentials degrades to the heuristic reranker with a warning.
    reranker_provider: str = "heuristic"
    reranker_model: Optional[str] = None  # e.g. bge-reranker-v2-m3, rerank-multilingual-v3.0
    reranker_api_base: Optional[str] = None
    reranker_api_key: Optional[str] = None
    reranker_batch_size: int = 64
    reranker_timeout_seconds: float = 10.0
    # --- retrieval tuning knobs (promoted from hardcoded constants; every
    # default equals the previous literal, so behavior is unchanged) ---
    retrieval_dense_similarity_floor_cap: float = 0.45  # cap on retrieval_similarity_floor with a real dense embedder
    retrieval_discriminative_df_ratio: float = 0.2  # max candidate-set doc-frequency share for a "discriminative" term
    rerank_llm_excerpt_chars: int = 300  # chars of each chunk shown in the LLM rescore prompt
    rerank_llm_blend_weight: float = 0.5  # weight of the LLM rescore in the final rerank blend
    reranker_max_retries: int = 1  # extra attempts after the first API rerank failure
    graph_expansion_score: float = 0.01  # retrieval score stamped on graph-expansion candidates
    graph_expansion_sources: int = 8  # top-ranked chunks whose graph edges are followed
    graph_expansion_limit: int = 8  # hard cap on candidates appended by graph expansion
    temporal_score_unknown: float = 0.3  # "current" intent score when status/dates are unknown
    temporal_score_repealed_before_date: float = 0.1  # "historical" score when repealed before the scenario date
    temporal_score_unconfirmed: float = 0.5  # "historical" score when applicability cannot be confirmed
    search_web_hit_score: float = 0.5  # initial retrieval_score for official-source web hits
    search_authority_fallback: float = 0.15  # confidence for sources missing from AUTHORITY_WEIGHTS
    milvus_connect_attempts: int = 3  # Milvus connection attempts before in-memory fallback
    milvus_connect_backoff_seconds: float = 1.0  # backoff sleep multiplier (x attempt number)
    embedding_batch_size: int = 200  # texts per embedding API call (NVIDIA via OpenRouter caps at 256)
    ranking_relevance_weight: float = 0.55
    ranking_authority_weight: float = 0.30
    ranking_confidence_weight: float = 0.15
    # Temporal component blended into the evidence ranking only when the plan's
    # temporal_intent is "current"/"historical"; intent "any" skips it, so the
    # legacy relevance/authority/confidence behavior is unchanged (spec §10).
    ranking_temporal_weight: float = 0.15
    planner_aux_top_k: int = 5  # top_k for auxiliary (gov/regulation/case-law/news) tasks
    search_timeout_seconds: float = 8.0  # per-request web-source fetch timeout
    search_max_results_per_source: int = 5
    search_max_content_chars: int = 1200  # per-chunk content cap from web sources
    # confidence / escalation
    confidence_threshold: float = 0.55  # below this the answer carries a low-confidence warning
    human_review_threshold: float = 0.40  # below this, escalate to a human legal expert
    min_evidence_score: float = 0.05  # evidence weaker than this is ignored by ranking
    coverage_retry_threshold: float = 0.6  # below this the coverage auditor requests re-retrieval
    # agents / guardrails
    context_max_turns: int = 10  # conversation window (turns) loaded into context
    memory_recall_limit: int = 5
    # Evidence selection is per sub-question: each retrieval branch keeps its
    # best chunks (score >= min_evidence_score), capped at this many — no
    # global cap, so one sub-question cannot starve the others of evidence.
    answer_max_evidence_per_subquestion: int = 5
    input_max_chars: int = 8000  # user queries longer than this is truncated
    input_max_words: int = 200  # user queries longer than this are rejected (HTTP 400)
    evidence_injection_screening: bool = True  # scan retrieved chunks for embedded instructions before prompting
    # --- chat streaming / run bounds ---
    chat_heartbeat_seconds: float = 10.0  # SSE keepalive frame interval
    chat_run_timeout_seconds: float = 280.0  # hard cap per run (below nginx's 300s proxy_read_timeout)
    # tools
    sandbox_timeout_seconds: float = 5.0
    currency_tool_timeout_seconds: float = 5.0

    # --- uploads ---
    # Per-role upload caps, enforced by the API with HTTP 413. The admin cap
    # must stay <= nginx's client_max_body_size (100m) or nginx rejects first.
    max_upload_bytes_admin: int = 100 * 1024 * 1024
    max_upload_bytes_user: int = 25 * 1024 * 1024

    # --- speech-to-text (voice messages, POST /chat/transcribe) ---
    # "litellm" (default: transcription via the LiteLLM gateway — OpenAI
    # Whisper or any compatible endpoint, reusing the main LLM credentials) |
    # "faster-whisper" (fully local; the package is import-guarded, so the
    # platform runs unchanged without it and the endpoint reports 503).
    stt_provider: str = "litellm"
    stt_model: str = "whisper-1"  # LiteLLM transcription model
    stt_language: str = "fr"
    # Optional dedicated transcription endpoint credentials; when empty the
    # main LLM api_key/api_base are reused (same convention as embeddings).
    stt_api_key: str = ""
    stt_api_base: str = ""
    stt_timeout_seconds: float = 90.0
    faster_whisper_model_size: str = "small"  # local Whisper model size
    # Local Whisper model download cache; None = data_dir/"stt_models".
    stt_models_dir: Optional[Path] = None
    stt_max_audio_bytes: int = 25 * 1024 * 1024

    # --- chunking / ingestion ---
    # Legal docs use boundary-based parents (whole articles) with alinéa-based
    # children; ``chunk_parent_size`` only applies to the unstructured
    # ``parent_child`` fallback, and ``chunk_child_size`` caps child length.
    chunk_parent_size: int = 2000
    chunk_child_size: int = 500
    chunk_overlap: int = 100
    chunk_max_size: int = 2000
    text_cleaning_min_pages_for_header: int = 3
    text_cleaning_header_min_frequency: float = 0.6
    ingestion_html_timeout_seconds: float = 30.0
    ingestion_freshness_timeout_seconds: float = 20.0
    pdf_parser_max_pages: int = 50
    # --- OCR (scanned PDFs) ---
    # PaddleOCR (French, CPU) behind backend.ingestion.ocr; import-guarded, so
    # ingestion works normally without the OCR stack installed — scanned pages
    # simply stay unrecovered instead of failing the loader.
    ocr_enabled: bool = True
    ocr_lang: str = "fr"  # PaddleOCR recognition language
    # PaddleOCR/PaddleX model cache (PADDLE_PDX_CACHE_HOME); None = data_dir/"ocr_models".
    ocr_models_dir: Optional[Path] = None
    ocr_max_pages: int = 0  # per-document OCR page cap; 0 = no cap (all pages, batched)
    # Pages per OCR subprocess: bounds per-run memory and contains the damage
    # of a crash/timeout to one batch instead of the whole document.
    ocr_batch_pages: int = 25
    # Rendering DPI for scanned pages: 200 keeps printed legal text readable
    # for OCR while using ~2.25x less image memory than 300 dpi.
    ocr_render_dpi: int = 200
    # Thread-pool cap for the OCR child process (OMP/MKL): unbounded pools
    # spike RAM hard enough to trigger the host OOM killer on loaded hosts.
    ocr_cpu_threads: int = 4
    # Base budget (seconds) for one OCR batch subprocess; the effective
    # timeout adds 30 s per page on top of this.
    ocr_subprocess_timeout_seconds: int = 120
    # Model directory names under <ocr_models_dir>/official_models used as
    # explicit local paths (offline deployments); must match the pinned
    # paddleocr release's default det/rec models for ocr_lang.
    ocr_det_model_name: str = "PP-OCRv5_mobile_det"  # mobile: server_det OOMs on small hosts
    ocr_rec_model_name: str = "latin_PP-OCRv5_mobile_rec"
    # Last-resort LLM classification at ingest: one completion, only when the
    # heuristics found neither legal domains nor authority (never runs on the
    # happy path for known documents, never with the mock provider).
    ingestion_llm_classification_enabled: bool = True
    # Persist the relational legal knowledge graph (backend/knowledge) at ingest
    # and let the graph retrieval worker use it; failures never block either path.
    legal_graph_enabled: bool = True

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

    # --- externalized jurisdictional data files (additive; offline-first) ---
    # All default to None = load the bundled files under data/ (with embedded
    # fallbacks on missing/corrupt files, always with a structured warning).
    # Set a filesystem path to adapt the platform to another jurisdiction.
    terminology_path: Optional[str] = None  # lexicon JSON for backend/planner/terminology.py (default data/terminology.json)
    decomposition_path: Optional[str] = None  # issue taxonomy JSON for backend/planner/decomposition.py (default data/decomposition.json)
    # Single JSON with search_registry / crawler_allowed_domains /
    # freshness_registry / document_titles sections (default data/legal_sources.json).
    legal_sources_path: Optional[str] = None
    # Comma-separated extra domains merged into the crawler allowlist at crawl time.
    crawler_extra_allowed_domains: str = ""
    # Standalone {filename: display title} JSON; overrides the document_titles
    # section of the legal-sources file when set.
    document_titles_path: Optional[str] = None
    # Domain-keyword taxonomy JSON for ingestion classification
    # ({"version": 1, "domains": {slug: [unaccented stems]}}; default
    # data/legal_domains.json; falls back to the embedded map when corrupt).
    legal_domains_path: Optional[str] = None
    # JSON with optional authority_weights / official_domains / legal_domains
    # keys, merged onto the backend.core.constants defaults (see load_* there).
    authority_config_path: Optional[str] = None
    # Legal rule store for backend/tools/legal_calculations.py (None = bundled legal_rules.json).
    legal_rules_path: Optional[str] = None

    # --- export ---
    # Heading of exported answer documents (PDF/Word/Markdown). The default
    # matches the long-standing hardcoded title.
    export_pdf_title: str = "Réponse juridique"

    # --- prompt overrides ---
    # Optional directory of prompt override files for the registry in
    # backend.core.prompts: `<NAME>.md` (or `.txt`) where NAME is a registry
    # key (e.g. PLANNER_SYSTEM.md). Missing files fall back to the built-in
    # defaults; unset (None) = built-ins only. Override contents are cached
    # per file and re-read when the file's mtime changes.
    prompts_dir: Optional[str] = None

    # --- strict-mode hardening & formerly env-only switches (additive) ---
    # Comma-separated infra components whose "degraded" status makes /ready
    # return 503 in strict mode (validated against KNOWN_INFRA_COMPONENTS).
    # cache/memory_store/legal_graph stay non-critical by default: their
    # fallbacks (in-memory cache, sqlite/in-memory memory, no-op graph) are
    # survivable per request and must not pull the API out of rotation.
    strict_critical_components: str = "milvus,postgres,database_probe,llm,embeddings,user_store"
    # Extra dev logins, "user:password:role,..." (same parsing as the legacy
    # LEGAL_AI_DEV_USERS env var, which pydantic maps onto this setting).
    dev_users: str = ""
    # Optional sandbox runtimes (off = local subprocess sandbox only); the
    # LEGAL_AI_E2B_ENABLED / LEGAL_AI_PYODIDE_ENABLED env vars map here.
    e2b_enabled: bool = False
    pyodide_enabled: bool = False

    @field_validator("strict_critical_components")
    @classmethod
    def _validate_strict_critical_components(cls, value: str) -> str:
        unknown = [
            name
            for name in (c.strip() for c in value.split(","))
            if name and name not in KNOWN_INFRA_COMPONENTS
        ]
        if unknown:
            raise ValueError(
                f"unknown strict critical component(s): {', '.join(unknown)} "
                f"(known: {', '.join(sorted(KNOWN_INFRA_COMPONENTS))})"
            )
        return value

    @property
    def strict_critical_list(self) -> list[str]:
        """Parsed ``strict_critical_components`` (empty names dropped)."""
        return [c.strip() for c in self.strict_critical_components.split(",") if c.strip()]

    # --- semantic guardrails (LLM Guard) ---
    guardrails_enabled: bool = True
    guardrails_input_scanners: str = "PromptInjection,Jailbreak,Toxicity,Secrets,Anonymize"
    guardrails_output_scanners: str = "Toxicity,Bias,MaliciousURLs,Deanonymize"
    guardrails_threshold: float = 0.75
    guardrails_pii_language: str = "en"

    # --- agent behavior knobs (planner / drafting / verification agents) ---
    # Defaults preserve the previous hardcoded behavior exactly.
    # planner agent
    planner_max_tool_iterations: int = 3  # LLM tool-calling loop budget for the planner
    planner_max_expansion_tasks: int = 3  # cap on terminology-expansion keyword tasks per plan
    planner_max_sub_question_tasks: int = 6  # cap on sub-question keyword tasks added to the plan
    # response generator / confidence
    answer_max_excerpt_chars: int = 4000  # per-excerpt char cap in the evidence block sent to the LLM
    answer_child_preview_chars: int = 200  # child-chunk preview length inside a parent excerpt
    confidence_citation_weight: float = 0.4  # weight of citation accuracy in the aggregate confidence
    confidence_coverage_weight: float = 0.6  # weight of sub-question coverage in the aggregate confidence
    confidence_unresolved_conflict_cap: float = 0.6  # confidence cap while source conflicts stay unresolved
    confidence_reflection_gap_cap: float = 0.75  # confidence cap when reflection flags unanswered parts
    source_default_authority_weight: float = 0.15  # authority weight assumed for unknown authority levels
    retrieval_top_mean_count: int = 3  # top-N relevance scores averaged into retrieval confidence
    temporal_conflict_penalty: float = 0.5  # temporal confidence while conflicts are unresolved
    temporal_undated_penalty: float = 0.6  # temporal confidence for time-sensitive plans backed by undated sources
    # reasoning agent
    reasoning_max_excerpt_chars: int = 2000  # per-chunk char cap in the reasoning evidence digest
    # coverage auditor / claim verification
    coverage_term_min_length: int = 4  # min length of a discriminative coverage term
    coverage_term_match_ratio: float = 0.5  # fraction of a question's terms one text must carry to cover it
    claim_min_chars: int = 40  # sentences shorter than this are connectors, not standalone claims
    claim_direct_term_coverage: float = 0.7  # term-coverage bar for DIRECT support
    claim_indirect_term_coverage: float = 0.4  # term-coverage bar for INDIRECT support
    claim_contradiction_term_coverage: float = 0.6  # topical-overlap bar above which a number mismatch contradicts
    # conflict resolver / context agent
    conflict_prefix_chars: int = 80  # identical-content prefix length compared before declaring a contradiction
    context_buffer_limit: int = 20  # default window size of the load_conversation_buffer tool
    context_message_max_chars: int = 2000  # per-message char cap in the loaded conversation buffer

    @property
    def langfuse_enabled(self) -> bool:
        return bool(self.langfuse_public_key and self.langfuse_secret_key)

    @property
    def ocr_models_path(self) -> Path:
        """Effective OCR model cache directory (``ocr_models_dir`` or the default)."""
        return self.ocr_models_dir or (self.data_dir / "ocr_models")

    @property
    def stt_models_path(self) -> Path:
        """Effective STT model cache directory (``stt_models_dir`` or the default)."""
        return self.stt_models_dir or (self.data_dir / "stt_models")

    @property
    def is_production(self) -> bool:
        return self.env == "production"

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
