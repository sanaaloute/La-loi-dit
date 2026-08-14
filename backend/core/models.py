"""Shared domain models — the contract every agent and subsystem uses.

Everything is pydantic v2 so state moving through the LangGraph workflow is
validated, serializable (checkpointing / Temporal / audit logs) and traceable.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


def _new_id() -> str:
    return uuid.uuid4().hex


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------
# Enums
# --------------------------------------------------------------------------


class AuthorityLevel(str, Enum):
    CONSTITUTION = "constitution"
    TREATY_OHADA = "treaty_ohada"
    LAW = "law"
    AMENDED_LAW = "amended_law"
    DECREE = "decree"
    ORDER = "order"
    MINISTERIAL_CIRCULAR = "ministerial_circular"
    OFFICIAL_GAZETTE = "official_gazette"
    CASE_LAW = "case_law"
    OFFICIAL_PRESS_RELEASE = "official_press_release"
    OFFICIAL_NEWS = "official_news"
    UPLOADED_DOCUMENT = "uploaded_document"
    TRUSTED_LEGAL_SITE = "trusted_legal_site"
    NEWS = "news"
    BLOG = "blog"
    UNKNOWN = "unknown"


class SearchKind(str, Enum):
    VECTOR = "vector"
    KEYWORD = "keyword"
    WEB = "web"
    WEBSITE = "website"
    GOVERNMENT = "government"
    CASE_LAW = "case_law"
    NEWS = "news"
    REGULATION = "regulation"
    UPLOADED = "uploaded"
    GRAPH = "graph"  # legal knowledge graph lookup/expansion (spec §19)


class Role(str, Enum):
    ADMIN = "admin"
    LEGAL_EXPERT = "legal_expert"
    USER = "user"
    VIEWER = "viewer"


class QuestionType(str, Enum):
    """Coarse taxonomy of user question intents (spec §30)."""

    FACTUAL = "factual"
    DEFINITION = "definition"
    LEGAL_RULE = "legal_rule"
    RIGHTS = "rights"
    OBLIGATIONS = "obligations"
    PROCEDURE = "procedure"
    COMPARISON = "comparison"
    CASE_ANALYSIS = "case_analysis"
    CALCULATION = "calculation"
    HISTORICAL = "historical"
    CURRENT_LAW = "current_law"
    DOCUMENT_SUMMARY = "document_summary"
    SOURCE_LOOKUP = "source_lookup"
    GENERAL = "general"


class DocumentType(str, Enum):
    """Instrument type of a legal document (spec §6 metadata contract)."""

    CODE = "code"
    LAW = "law"
    DECREE = "decree"
    ORDINANCE = "ordinance"
    DECISION = "decision"
    CASE_LAW = "case_law"
    TREATY = "treaty"
    ARTICLE = "article"
    OTHER = "other"


class SupportLevel(str, Enum):
    """How well one evidence chunk supports a single claim (spec §21)."""

    DIRECT = "direct"  # claim terms and numbers fully grounded in the chunk
    INDIRECT = "indirect"  # topical overlap, discriminative terms partially covered
    INSUFFICIENT = "insufficient"  # no chunk clears the indirect bar
    CONTRADICTORY = "contradictory"  # claim numbers/dates conflict with the chunk


class RiskFlag(str, Enum):
    PROMPT_INJECTION = "prompt_injection"
    JAILBREAK = "jailbreak"
    SENSITIVE_INFO = "sensitive_info"
    ROLE_HIJACKING = "role_hijacking"
    TOOL_ABUSE = "tool_abuse"
    UNSAFE_LEGAL_ADVICE = "unsafe_legal_advice"
    LOW_CONFIDENCE = "low_confidence"
    UNVERIFIED_SOURCE = "unverified_source"
    HALLUCINATION_SUSPECT = "hallucination_suspect"


# --------------------------------------------------------------------------
# Evidence & citations
# --------------------------------------------------------------------------


class EvidenceChunk(BaseModel):
    """One retrieved unit of evidence.

    Every retrieved document MUST carry the metadata below (see spec
    "RAG ARCHITECTURE") so answers stay traceable end to end.
    """

    chunk_id: str = Field(default_factory=_new_id)
    document_id: str = ""
    document_name: str = ""
    content: str = ""
    article: Optional[str] = None
    section: Optional[str] = None
    page: Optional[int] = None
    publication_date: Optional[date] = None
    effective_date: Optional[date] = None  # for legal timeline reasoning
    government_body: Optional[str] = None
    url: Optional[str] = None
    source_kind: SearchKind = SearchKind.VECTOR
    authority: AuthorityLevel = AuthorityLevel.UNKNOWN
    language: str = "fr"
    parent_chunk_id: Optional[str] = None  # parent-child chunking
    child_chunks: list["EvidenceChunk"] = Field(default_factory=list)  # populated on parents after expansion
    # Dual text (spec §7): ``retrieval_text`` is the exact child passage that
    # matched the query, ``context_text`` the enclosing parent (article /
    # section) it was expanded to.  Populated by parent_expansion; both stay
    # None on chunks without a parent, where ``content`` serves both roles.
    retrieval_text: Optional[str] = None
    context_text: Optional[str] = None
    version: int = 1
    confidence: float = 0.0  # source confidence score
    retrieval_score: float = 0.0  # raw score from the retriever
    rerank_score: float = 0.0  # cross-encoder / authority-weighted score
    metadata: dict[str, Any] = Field(default_factory=dict)
    # --- structured legal metadata (spec §6); all optional/backward-compatible ---
    document_type: Optional[DocumentType] = None  # instrument type (code/law/decree/...)
    law_number: Optional[str] = None  # e.g. "028-2008/AN"
    jurisdiction: str = "Burkina Faso"
    # Lifecycle: active | repealed | amended | future | unknown.  Chunks
    # ingested before this field existed deserialize to the "active" default,
    # which the temporal filter never excludes (see backend/retrieval/temporal.py).
    status: str = "active"
    valid_from: Optional[date] = None  # entry into force (defaults from effective_date at ingest)
    valid_until: Optional[date] = None  # repeal/expiry date when known
    # Ordered heading path, highest level first, e.g.
    # {"livre": "I", "titre": "II", "chapitre": "III", "section": "3"}.
    hierarchy: dict[str, str] = Field(default_factory=dict)
    issuing_authority: Optional[str] = None  # defaults from government_body at ingest
    embedding_model: Optional[str] = None  # model that embedded this chunk

    def citation_label(self) -> str:
        parts = [self.document_name or "Document inconnu"]
        if self.article:
            parts.append(f"art. {self.article}")
        if self.section:
            parts.append(f"sec. {self.section}")
        return ", ".join(parts)


class Citation(BaseModel):
    """A citation as it appears in a generated answer, verified or not."""

    label: str  # raw text, e.g. "Code du travail, art. 123"
    chunk_id: Optional[str] = None  # resolved evidence chunk
    document_name: str = ""
    article: Optional[str] = None
    url: Optional[str] = None
    verified: bool = False  # True only if traced to real retrieved evidence


class ConflictReport(BaseModel):
    """Outcome of comparing two sources that disagree."""

    topic: str
    kept_chunk_id: str
    dropped_chunk_id: str
    reason: str  # e.g. "kept newer amended law over older decree"
    resolved: bool = True  # False => uncertainty must be surfaced to the user


class ClaimSource(BaseModel):
    """One evidence chunk a claim was checked against (spec §20)."""

    chunk_id: str
    document_name: str = ""
    article: Optional[str] = None
    support_level: SupportLevel = SupportLevel.INSUFFICIENT


class Claim(BaseModel):
    """One substantive statement of the answer with its support verdict (spec §20)."""

    claim_id: str
    text: str
    support_level: SupportLevel = SupportLevel.INSUFFICIENT  # best of sources
    sources: list[ClaimSource] = Field(default_factory=list)


# --------------------------------------------------------------------------
# Planning
# --------------------------------------------------------------------------


class SearchTask(BaseModel):
    kind: SearchKind
    query: str
    top_k: int = 8
    filters: dict[str, Any] = Field(default_factory=dict)


class RetrievalPlan(BaseModel):
    sub_questions: list[str] = Field(default_factory=list)
    tasks: list[SearchTask] = Field(default_factory=list)
    legal_domains: list[str] = Field(default_factory=list)
    retrieval_language: str = "fr"  # evidence retrieval language
    response_language: str = "fr"  # answer language follows the user
    scenario_date: Optional[date] = None  # legal timeline reasoning anchor
    question_type: QuestionType = QuestionType.GENERAL  # intent taxonomy (spec §30)
    temporal_intent: str = "any"  # "current" | "historical" | "any"
    rationale: str = ""


# --------------------------------------------------------------------------
# Guardrails / reflection / answer
# --------------------------------------------------------------------------


class GuardrailResult(BaseModel):
    allowed: bool = True
    flags: list[RiskFlag] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)
    sanitized_query: Optional[str] = None


class ReflectionResult(BaseModel):
    complete: bool = True
    answered_all_questions: bool = True
    all_claims_cited: bool = True
    contradictions_found: bool = False
    issues: list[str] = Field(default_factory=list)
    should_retry_retrieval: bool = False
    retry_query: Optional[str] = None


class CoverageReport(BaseModel):
    """Deterministic sub-question coverage audit (spec §22).

    Produced by the coverage auditor before drafting: which planned
    sub-questions are backed by evidence, which are not, and whether the
    gap justifies one bounded re-retrieval pass.
    """

    coverage: float = 0.0  # fraction of sub-questions backed by evidence
    covered_issues: list[str] = Field(default_factory=list)
    missing_issues: list[str] = Field(default_factory=list)
    needs_more_retrieval: bool = False


class ConfidenceBreakdown(BaseModel):
    """Multi-dimensional confidence (spec §39).

    Each dimension is a 0-1 heuristic; the single ``FinalAnswer.confidence``
    float remains the derived aggregate (see ``response_generator``).
    ``temporal_confidence`` defaults to 1.0 = no temporal doubt.
    """

    source_confidence: float = 0.0  # mean authority weight of the evidence
    retrieval_confidence: float = 0.0  # normalized top retrieval scores
    legal_support_confidence: float = 0.0  # citation accuracy (legal grounding)
    temporal_confidence: float = 1.0  # 1.0 unless conflicts/undated time-sensitive evidence
    citation_confidence: float = 0.0  # citation accuracy post-verification
    coverage: float = 0.0  # sub-question coverage (CoverageReport)


class FinalAnswer(BaseModel):
    answer: str
    citations: list[Citation] = Field(default_factory=list)
    evidence: list[EvidenceChunk] = Field(default_factory=list)
    confidence: float = 0.0
    confidence_breakdown: Optional[ConfidenceBreakdown] = None  # per-dimension detail (spec §39)
    language: str = "fr"
    warnings: list[str] = Field(default_factory=list)
    conflicts: list[ConflictReport] = Field(default_factory=list)
    requires_human_review: bool = False
    refused: bool = False
    refusal_reason: Optional[str] = None
    claims: list[Claim] = Field(default_factory=list)  # per-statement support (spec §20)
    metadata: dict[str, Any] = Field(default_factory=dict)


# --------------------------------------------------------------------------
# Conversation / memory
# --------------------------------------------------------------------------


class ChatMessage(BaseModel):
    role: Literal["user", "assistant", "system"]
    content: str
    created_at: datetime = Field(default_factory=_utcnow)
    metadata: dict[str, Any] = Field(default_factory=dict)


def parse_answer_json(content: str) -> Optional[dict[str, Any]]:
    """Return the FinalAnswer dict when `content` is its JSON serialization.

    Assistant buffer turns are stored as ``FinalAnswer.model_dump_json()``
    (full structure for the history API); anything else returns None.
    """
    text = content.strip()
    if not text.startswith("{"):
        return None
    import json

    try:
        parsed = json.loads(text)
    except Exception:
        return None
    if isinstance(parsed, dict) and isinstance(parsed.get("answer"), str):
        return parsed
    return None


def plain_message_content(content: str) -> str:
    """Display text of a stored message: unwraps FinalAnswer JSON to its answer."""
    parsed = parse_answer_json(content)
    return parsed["answer"] if parsed is not None else content


class MemoryRecord(BaseModel):
    id: str = Field(default_factory=_new_id)
    user_id: str
    session_id: str = ""
    kind: Literal["buffer", "summary", "semantic", "preference"] = "semantic"
    content: str
    embedding: Optional[list[float]] = None
    importance: float = 0.5
    created_at: datetime = Field(default_factory=_utcnow)
    last_accessed: datetime = Field(default_factory=_utcnow)
    metadata: dict[str, Any] = Field(default_factory=dict)


# --------------------------------------------------------------------------
# API-facing models
# --------------------------------------------------------------------------


class ChatRequest(BaseModel):
    query: str
    session_id: Optional[str] = None
    user_id: Optional[str] = None
    language: Optional[str] = None
    scenario_date: Optional[date] = None
    model: Optional[str] = None  # catalog model id, tier-gated (None = tier default)


class ChatResponse(BaseModel):
    session_id: str
    answer: FinalAnswer
    trace: list[str] = Field(default_factory=list)
    latency_ms: float = 0.0
    trace_id: str = ""  # Langfuse trace id for feedback and debugging


class DocumentIngestResult(BaseModel):
    document_id: str
    document_name: str
    chunks_created: int
    version: int
    status: Literal["indexed", "failed", "skipped_duplicate", "deleted"] = "indexed"
    detail: str = ""


class ReindexSummary(BaseModel):
    """Aggregate result of a ``reindex_directory`` run over the corpus."""

    directory: str
    scanned: int = 0  # pipeline results returned (files processed + stale deletions)
    indexed: int = 0
    skipped_duplicate: int = 0
    failed: int = 0
    deleted: int = 0
    chunks_created: int = 0


class ArticleChunk(BaseModel):
    """One chunk returned by the article lookup endpoint."""

    chunk_id: str
    document_id: str
    document_name: str
    content: str
    article: Optional[str] = None
    section: Optional[str] = None
    page: Optional[int] = None
    publication_date: Optional[date] = None
    effective_date: Optional[date] = None
    url: Optional[str] = None
    authority: AuthorityLevel = AuthorityLevel.UNKNOWN
    metadata: dict[str, Any] = Field(default_factory=dict)


class ArticleLookupResponse(BaseModel):
    document_id: str
    article: str
    count: int
    chunks: list[ArticleChunk] = Field(default_factory=list)


class EvalCaseResult(BaseModel):
    case_id: str
    question: str
    groundedness: float = 0.0
    faithfulness: float = 0.0
    citation_accuracy: float = 0.0
    answer_relevance: float = 0.0
    hallucination_detected: bool = False
    latency_ms: float = 0.0
    passed: bool = False
    detail: str = ""


# --------------------------------------------------------------------------
# Citation / source lookup (spec §48) and admin (spec §49) response models
# --------------------------------------------------------------------------


class CitationRecord(BaseModel):
    """Full evidence record behind one citation/chunk id (spec §48).

    Mirrors the EvidenceChunk metadata contract so a client can resolve any
    citation id back to the exact source passage and its legal metadata.
    """

    chunk_id: str
    document_id: str
    document_name: str
    content: str
    article: Optional[str] = None
    section: Optional[str] = None
    page: Optional[int] = None
    publication_date: Optional[date] = None
    effective_date: Optional[date] = None
    government_body: Optional[str] = None
    url: Optional[str] = None
    authority: AuthorityLevel = AuthorityLevel.UNKNOWN
    language: str = "fr"
    version: int = 1
    document_type: Optional[DocumentType] = None
    law_number: Optional[str] = None
    status: str = "active"
    valid_from: Optional[date] = None
    valid_until: Optional[date] = None
    hierarchy: dict[str, str] = Field(default_factory=dict)
    issuing_authority: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class SourceRecord(BaseModel):
    """Document-level source record (spec §48).

    Combines the version store (version, content hash, article count) with
    the metadata carried by the document's chunks in the vector store.
    """

    document_id: str
    document_name: str = ""
    version: int = 1
    content_hash: str = ""
    article_count: int = 0
    chunk_count: int = 0
    authority: AuthorityLevel = AuthorityLevel.UNKNOWN
    document_type: Optional[DocumentType] = None
    law_number: Optional[str] = None
    status: str = "unknown"
    publication_date: Optional[date] = None
    effective_date: Optional[date] = None
    url: Optional[str] = None
    language: str = "fr"


class AuditLogEntry(BaseModel):
    """One HTTP request as recorded by the audit-log middleware."""

    ts: float
    method: str
    path: str
    status: int
    latency_ms: float
    user: str


class AuditLogResponse(BaseModel):
    entries: list[AuditLogEntry] = Field(default_factory=list)
    count: int = 0
    cap: int = 0
    source: str = "in_memory_ring_buffer"
    note: str = (
        "Per-process in-memory ring buffer: only requests served by this "
        "process since boot are visible; role is not recorded, only the "
        "token subject."
    )


class IngestionDocumentStatus(BaseModel):
    document_id: str
    # Display name from the latest ingestion record ("" when unknown —
    # the frontend falls back to the document id).
    document_name: str = ""
    version: int
    content_hash: str
    article_count: int
    # Real chunk count in the vector store (None when the store is
    # unavailable); versions.json's article count alone could be stale —
    # e.g. chunks deleted before a failed re-chunk.
    chunk_count: Optional[int] = None
    # Latest ingestion outcome from ingestion_results.json ("ingested",
    # "failed", "skipped_duplicate", ...); "" when no record exists.
    last_status: str = ""
    last_error: str = ""


class IngestionStatusResponse(BaseModel):
    documents: list[IngestionDocumentStatus] = Field(default_factory=list)
    total_documents: int = 0
    store_updated_at: Optional[datetime] = None  # versions.json mtime
    failed_documents: list[dict[str, Any]] = Field(default_factory=list)
    note: str = (
        "Built from versions.json (hash/version/articles per document); "
        "failed_documents come from ingestion_results.json, which keeps only "
        "the latest record per document — earlier failures are overwritten."
    )


class EvaluationLatestResponse(BaseModel):
    path: str
    generated_at: Optional[str] = None
    dataset: Optional[str] = None
    total_cases: Optional[int] = None
    pass_rate: Optional[float] = None
    report: dict[str, Any] = Field(default_factory=dict)


class EndpointStats(BaseModel):
    path: str
    requests: int
    errors: int = 0  # status >= 500
    avg_latency_ms: float = 0.0


class UserRequestStats(BaseModel):
    user: str
    requests: int


class RetrievalAnalyticsResponse(BaseModel):
    source: str = "in_memory_audit_log"
    total_requests: int = 0
    by_path: list[EndpointStats] = Field(default_factory=list)
    by_user: list[UserRequestStats] = Field(default_factory=list)
    note: str = (
        "Aggregated from this process's in-memory audit log (all endpoints, "
        "not only retrieval). Per-role breakdown is unavailable — the audit "
        "log records the token subject, not the role. Prometheus /metrics "
        "has the cross-process HTTP counters."
    )
