"""LLM Guard integration — semantic input/output guardrails.

This module wraps the open-source `llm-guard` library (MIT) as a second-layer
filter behind the deterministic regex guards in `backend.guardrails.policies`.
If scanners cannot be imported/initialised (missing dependency or model), the
module logs a warning and disables itself so the app keeps running.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from backend.core.config import Settings
from backend.core.models import GuardrailResult, RiskFlag
from backend.guardrails.input_guard import check_input as deterministic_check_input

logger = logging.getLogger(__name__)

_INPUT_SCANNER_NAMES = {
    "PromptInjection": ("llm_guard.input_scanners", "PromptInjection"),
    "Jailbreak": ("llm_guard.input_scanners", "Jailbreak"),
    "Toxicity": ("llm_guard.input_scanners", "Toxicity"),
    "Secrets": ("llm_guard.input_scanners", "Secrets"),
    "Anonymize": ("llm_guard.input_scanners", "Anonymize"),
}

_OUTPUT_SCANNER_NAMES = {
    "Toxicity": ("llm_guard.output_scanners", "Toxicity"),
    "Bias": ("llm_guard.output_scanners", "Bias"),
    "MaliciousURLs": ("llm_guard.output_scanners", "MaliciousURLs"),
    "Deanonymize": ("llm_guard.output_scanners", "Deanonymize"),
}


def _load_scanners(names: dict[str, tuple[str, str]], settings: Settings) -> dict[str, Any]:
    """Import and instantiate the configured scanners.

    Returns only scanners that successfully initialised. A failure is logged
    once and that scanner is skipped.
    """
    scanners: dict[str, Any] = {}
    enabled = set(
        name.strip()
        for name in getattr(settings, "guardrails_input_scanners", "").split(",")
        + getattr(settings, "guardrails_output_scanners", "").split(",")
        if name.strip()
    )
    for name, (module, cls) in names.items():
        if name not in enabled:
            continue
        try:
            mod = __import__(module, fromlist=[cls])
            scanner_cls = getattr(mod, cls)
            if name == "Anonymize":
                from llm_guard.vault import Vault

                scanners[name] = scanner_cls(
                    Vault(),
                    language=getattr(settings, "guardrails_pii_language", "en"),
                )
            elif name == "Deanonymize":
                from llm_guard.vault import Vault

                scanners[name] = scanner_cls(Vault())
            else:
                scanners[name] = scanner_cls()
        except Exception as exc:
            logger.warning("llm-guard scanner %s unavailable: %s", name, exc)
    return scanners


class _LLMGuardScanners:
    """Lazy per-process scanner cache."""

    def __init__(self):
        self._input: Optional[dict[str, Any]] = None
        self._output: Optional[dict[str, Any]] = None

    def input_scanners(self, settings: Settings) -> dict[str, Any]:
        if self._input is None:
            self._input = _load_scanners(_INPUT_SCANNER_NAMES, settings)
        return self._input

    def output_scanners(self, settings: Settings) -> dict[str, Any]:
        if self._output is None:
            self._output = _load_scanners(_OUTPUT_SCANNER_NAMES, settings)
        return self._output


_SCANNERS = _LLMGuardScanners()


def _input_scanner_list(settings: Settings) -> list[str]:
    return [n.strip() for n in getattr(settings, "guardrails_input_scanners", "").split(",") if n.strip()]


def _output_scanner_list(settings: Settings) -> list[str]:
    return [n.strip() for n in getattr(settings, "guardrails_output_scanners", "").split(",") if n.strip()]


def _threshold(settings: Settings) -> float:
    return float(getattr(settings, "guardrails_threshold", 0.75))


# LLM Guard scanner names are PascalCase; our RiskFlag enum is lower_snake_case.
_RISK_FLAG_MAP: dict[str, RiskFlag] = {
    "PromptInjection": RiskFlag.PROMPT_INJECTION,
    "Jailbreak": RiskFlag.JAILBREAK,
    "Toxicity": RiskFlag.UNSAFE_LEGAL_ADVICE,
    "Secrets": RiskFlag.SENSITIVE_INFO,
    "Anonymize": RiskFlag.SENSITIVE_INFO,
    "Bias": RiskFlag.UNSAFE_LEGAL_ADVICE,
    "MaliciousURLs": RiskFlag.UNVERIFIED_SOURCE,
    "Deanonymize": RiskFlag.SENSITIVE_INFO,
}


def _risk_flags_for(names: list[str], base: list[RiskFlag] | None = None) -> list[RiskFlag]:
    flags: list[RiskFlag] = list(base or [])
    for name in names:
        flag = _RISK_FLAG_MAP.get(name)
        if flag and flag not in flags:
            flags.append(flag)
    return flags


def _scan(text: str, scanners: dict[str, Any], threshold: float) -> tuple[str, bool, list[str], list[str]]:
    """Run text through all loaded scanners sequentially.

    Returns (sanitized_text, allowed, flags, reasons). Scanners that mutate the
    text (Anonymize) are allowed through but may redact content.
    """
    current = text
    allowed = True
    flags: list[str] = []
    reasons: list[str] = []
    for name, scanner in scanners.items():
        try:
            result = scanner.scan(current)
        except Exception as exc:
            logger.warning("llm-guard scanner %s failed: %s", name, exc)
            continue
        # Scanner.scan returns (sanitized, is_valid, risk_score) or similar.
        if len(result) >= 3:
            sanitized, is_valid, risk_score = result[0], result[1], result[2]
        elif len(result) == 2:
            sanitized, is_valid = result
            risk_score = 0.0
        else:
            continue
        current = sanitized if isinstance(sanitized, str) else current
        if not is_valid or (isinstance(risk_score, float) and risk_score > threshold):
            allowed = False
            if name not in flags:
                flags.append(name)
            reasons.append(f"llm-guard/{name}: risk_score={risk_score}")
    return current, allowed, flags, reasons


async def scan_input(
    query: str, settings: Settings, *, run_deterministic: bool = True
) -> GuardrailResult:
    """Run deterministic + LLM Guard input checks on ``query``.

    The deterministic layer runs first and can block immediately. LLM Guard is
    then applied for semantic jailbreak/prompt-injection/PII detection when
    ``guardrails_enabled`` is True. PII is redacted (Anonymize) and returned in
    ``sanitized_query``.

    ``run_deterministic=False`` lets callers that already ran the regex guard
    skip it and go straight to the semantic LLM Guard layer.
    """
    if run_deterministic:
        # First-layer deterministic regex guards (fast, no model download).
        result = await deterministic_check_input(query, settings)
        if not result.allowed:
            return result
        text = result.sanitized_query if result.sanitized_query is not None else query
        base_reasons = list(result.reasons)
        base_flags = list(result.flags)
    else:
        result = None
        text = query
        base_reasons = []
        base_flags = []

    if not getattr(settings, "guardrails_enabled", True):
        return GuardrailResult(
            allowed=True,
            sanitized_query=text,
            reasons=base_reasons,
            flags=base_flags,
        )

    scanners = _SCANNERS.input_scanners(settings)
    wanted = _input_scanner_list(settings)
    active = {k: v for k, v in scanners.items() if k in wanted}

    if not active:
        return GuardrailResult(
            allowed=True,
            sanitized_query=text,
            reasons=base_reasons,
            flags=base_flags,
        )

    sanitized, allowed, flags, reasons = _scan(text, active, _threshold(settings))
    risk_flags = _risk_flags_for(flags, base_flags)

    if not allowed:
        return GuardrailResult(
            allowed=False,
            reasons=base_reasons + reasons,
            flags=risk_flags,
        )

    final_reasons = list(base_reasons)
    final_flags = list(risk_flags)
    if sanitized != text:
        final_reasons.append("llm-guard/Anonymize: PII redacted")
        if RiskFlag.SENSITIVE_INFO not in final_flags:
            final_flags.append(RiskFlag.SENSITIVE_INFO)
    final_reasons.extend(reasons)
    return GuardrailResult(
        allowed=True,
        sanitized_query=sanitized,
        reasons=final_reasons,
        flags=final_flags,
    )


async def scan_output(
    answer_text: str,
    settings: Settings,
    *,
    run_deterministic: bool = True,
) -> tuple[str, list[str], list[str]]:
    """Run deterministic + LLM Guard output checks on the generated answer.

    Returns (sanitized_text, reasons, flags). Output scanning is advisory by
    default — it returns warnings and redacted text so the response can still be
    delivered to the user. To block, the caller should inspect the returned
    flags and raise a GuardrailViolation if required.
    """
    if run_deterministic:
        from backend.guardrails.output_guard import check_output as deterministic_check_output
        from backend.core.models import FinalAnswer

        answer = FinalAnswer(answer=answer_text)
        answer = await deterministic_check_output(answer, [], settings)
        text = answer.answer
        base_reasons = list(answer.warnings)
        base_flags: list[RiskFlag] = []
        for raw in answer.metadata.get("risk_flags", []):
            try:
                base_flags.append(RiskFlag(raw))
            except ValueError:
                pass
        if answer.refused:
            return text, base_reasons, [f.value for f in base_flags]
    else:
        text = answer_text
        base_reasons = []
        base_flags = []

    if not getattr(settings, "guardrails_enabled", True):
        return text, base_reasons, [f.value for f in base_flags]

    scanners = _SCANNERS.output_scanners(settings)
    wanted = _output_scanner_list(settings)
    active = {k: v for k, v in scanners.items() if k in wanted}
    if not active:
        return text, base_reasons, [f.value for f in base_flags]

    sanitized, allowed, flags, reasons = _scan(text, active, _threshold(settings))
    risk_flags = _risk_flags_for(flags, base_flags)

    out_reasons = list(base_reasons)
    if not allowed:
        out_reasons.extend(
            [f"llm-guard output flagged ({', '.join(flags)}): {r}" for r in reasons]
        )
    else:
        out_reasons.extend(reasons)
    return sanitized, out_reasons, [f.value for f in risk_flags]
