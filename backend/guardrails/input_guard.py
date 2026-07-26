"""Input guardrail: first gate every user query passes through.

Deterministic and fast (regex tables only, no I/O, no LLM):
  * prompt injection / jailbreak / role hijacking / tool abuse -> blocked
  * PII -> redacted into ``sanitized_query``, flagged, still allowed
  * unsafe legal advice requests -> flagged, still allowed (the output
    guardrail enforces refusal/review on the answer side)
  * length cap configurable via LEGAL_AI_INPUT_MAX_CHARS
"""

from __future__ import annotations

from backend.core.models import GuardrailResult, RiskFlag
from backend.guardrails.policies import (
    BLOCKING_TABLES,
    PII_PATTERNS,
    UNSAFE_LEGAL_ADVICE_PATTERNS,
)


async def check_input(query: str, settings) -> GuardrailResult:
    """Screen a user query against the input policies. Never raises."""
    result = GuardrailResult(allowed=True)
    text = query or ""
    max_query_chars = getattr(settings, "input_max_chars", 8000)

    # --- length cap ---
    if len(text) > max_query_chars:
        text = text[:max_query_chars]
        result.sanitized_query = text
        result.reasons.append(f"query truncated to {max_query_chars} characters")

    # --- blocking detectors ---
    for flag, table in BLOCKING_TABLES:
        for pattern, reason in table:
            if pattern.search(text):
                result.allowed = False
                if flag not in result.flags:
                    result.flags.append(flag)
                result.reasons.append(f"{flag.value}: {reason}")
                break  # one reason per category is enough
    if not result.allowed:
        return result

    # --- unsafe legal advice: flag but allow through ---
    for pattern, reason in UNSAFE_LEGAL_ADVICE_PATTERNS:
        if pattern.search(text):
            if RiskFlag.UNSAFE_LEGAL_ADVICE not in result.flags:
                result.flags.append(RiskFlag.UNSAFE_LEGAL_ADVICE)
            result.reasons.append(f"unsafe_legal_advice: {reason}")
            break

    # --- PII redaction (allowed, sanitized) ---
    sanitized = text
    pii_found: list[str] = []
    for pattern, desc, placeholder in PII_PATTERNS:
        if pattern.search(sanitized):
            sanitized = pattern.sub(placeholder, sanitized)
            pii_found.append(desc)
    if pii_found:
        if RiskFlag.SENSITIVE_INFO not in result.flags:
            result.flags.append(RiskFlag.SENSITIVE_INFO)
        result.reasons.append("sensitive_info: redacted " + ", ".join(sorted(set(pii_found))))
        result.sanitized_query = sanitized

    return result
