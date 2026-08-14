"""Domain exceptions. Nodes catch these and degrade gracefully instead of crashing the graph."""


class LegalAIError(Exception):
    """Base exception for the platform."""


class GuardrailViolation(LegalAIError):
    """Input or output blocked by a guardrail policy."""


class LLMError(LegalAIError):
    """LLM call failed or returned unparseable output after retry."""


class RetrievalError(LegalAIError):
    """A retrieval worker failed in a non-recoverable way."""


class IngestionError(LegalAIError):
    """Document ingestion failed."""


class SandboxError(LegalAIError):
    """Sandboxed execution failed or was rejected."""


class CitationVerificationError(LegalAIError):
    """A citation could not be traced to verified evidence."""


class STTError(LegalAIError):
    """Audio transcription failed or the STT provider is misconfigured."""


class AuthenticationError(LegalAIError):
    """JWT / credentials invalid."""


class UserAlreadyExistsError(LegalAIError):
    """Registration attempted with an email that already has an account."""


class AuthorizationError(LegalAIError):
    """Authenticated but insufficient role."""


class RateLimitError(LegalAIError):
    """Rate limit exceeded."""


class QuotaExceededError(LegalAIError):
    """Daily token budget exhausted for the caller's subscription tier."""
