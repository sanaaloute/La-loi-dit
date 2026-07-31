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


class Role(str, Enum):
    ADMIN = "admin"
    LEGAL_EXPERT = "legal_expert"
    USER = "user"
    VIEWER = "viewer"


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
    version: int = 1
    confidence: float = 0.0  # source confidence score
    retrieval_score: float = 0.0  # raw score from the retriever
    rerank_score: float = 0.0  # cross-encoder / authority-weighted score
    metadata: dict[str, Any] = Field(default_factory=dict)

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


class FinalAnswer(BaseModel):
    answer: str
    citations: list[Citation] = Field(default_factory=list)
    evidence: list[EvidenceChunk] = Field(default_factory=list)
    confidence: float = 0.0
    language: str = "fr"
    warnings: list[str] = Field(default_factory=list)
    conflicts: list[ConflictReport] = Field(default_factory=list)
    requires_human_review: bool = False
    refused: bool = False
    refusal_reason: Optional[str] = None
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
    status: Literal["indexed", "failed", "skipped_duplicate"] = "indexed"
    detail: str = ""


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
