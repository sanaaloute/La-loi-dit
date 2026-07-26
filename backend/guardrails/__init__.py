"""Guardrails subsystem: deterministic, fast, offline policy checks on
user input (injection / jailbreak / PII) and on generated output (refusal
policy, unsafe legal advice, citation verification)."""

from backend.guardrails.input_guard import check_input
from backend.guardrails.output_guard import check_output

__all__ = ["check_input", "check_output"]
