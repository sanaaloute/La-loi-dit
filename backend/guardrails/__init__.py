"""Guardrails subsystem: deterministic, fast, offline policy checks on
user input (injection / jailbreak / PII), on retrieved documents (embedded
instruction screening) and on generated output (refusal policy, unsafe legal
advice, citation verification)."""

from backend.guardrails.document_guard import check_evidence
from backend.guardrails.input_guard import check_input
from backend.guardrails.output_guard import check_output

__all__ = ["check_evidence", "check_input", "check_output"]
