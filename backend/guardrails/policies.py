"""Policy pattern tables for the input/output guardrails.

Everything here is deterministic regex-based detection (case-insensitive,
French + English) so checks are fast, offline and auditable. Each entry is
a ``(compiled_pattern, human-readable reason)`` pair; PII patterns instead
map to the redaction placeholder used when sanitizing the query.
"""

from __future__ import annotations

import re

from backend.core.models import RiskFlag

# ---------------------------------------------------------------------------
# Blocking patterns (input rejected when one matches)
# ---------------------------------------------------------------------------

PROMPT_INJECTION_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"ignore\s+(all\s+)?(previous|prior|above|preceding|tes|vos|les)\s+(instructions|consignes)", re.I),
     "attempt to override prior instructions"),
    (re.compile(r"ignore\s+tes\s+instructions", re.I), "tentative de contournement des instructions"),
    (re.compile(r"system\s*prompt", re.I), "reference to the system prompt"),
    (re.compile(r"(reveal|show|display|print|révèle|affiche|montre)\s+(me\s+)?(your|ton|votre|le)\s+(prompt|instructions|consignes)", re.I),
     "attempt to extract the system prompt"),
    (re.compile(r"\bDAN\b|\bdo\s+anything\s+now\b", re.I), "DAN-style prompt injection"),
    (re.compile(r"developer\s+mode|mode\s+développeur", re.I), "developer-mode injection attempt"),
    (re.compile(r"new\s+instructions?\s*:", re.I), "attempt to inject new instructions"),
]

JAILBREAK_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"jailbreak|évasion\s+de\s+(l'|la\s+)?IA", re.I), "explicit jailbreak attempt"),
    (re.compile(r"(sans|without)\s+(aucune\s+)?(restrictions?|limites?|filtres?|censure)", re.I),
     "request to disable safety restrictions"),
    (re.compile(r"bypass\s+(your\s+)?(safety|filters?|guardrails?)|contourne\s+(tes|les)\s+(filtres|protections|garde-fous)", re.I),
     "attempt to bypass safety filters"),
    (re.compile(r"pretend\s+(you\s+)?(have|has)\s+no\s+(rules|limits)|fais\s+comme\s+si\s+tu\s+n'avais\s+aucune\s+règle", re.I),
     "pretend-no-rules jailbreak"),
]

ROLE_HIJACKING_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"tu\s+es\s+maintenant", re.I), "role hijacking attempt (FR)"),
    (re.compile(r"you\s+are\s+now\b", re.I), "role hijacking attempt"),
    (re.compile(r"\bact\s+as\s+(a|an|if)\b", re.I), "act-as role hijacking"),
    (re.compile(r"\bassume\s+the\s+role\s+of|joue\s+le\s+rôle\s+de", re.I), "role reassignment attempt"),
    (re.compile(r"from\s+now\s+on\s+(you|tu)\s+(are|es|seras|will\s+be)", re.I), "persistent role override attempt"),
]

TOOL_ABUSE_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\bos\.system\b", re.I), "os.system invocation"),
    (re.compile(r"\bsubprocess\b", re.I), "subprocess invocation"),
    (re.compile(r"\brm\s+-rf\b", re.I), "destructive shell command"),
    (re.compile(r"\b(eval|exec)\s*\(", re.I), "dynamic code execution"),
    (re.compile(r"__import__", re.I), "dynamic import attempt"),
    (re.compile(r"\bexecute\s+(this|the\s+following|ce)\s+(code|command|commande|script)\b", re.I),
     "request to execute code/commands"),
    (re.compile(r"\b(curl|wget)\s+[^\s]+\s*\|\s*(bash|sh)\b", re.I), "pipe-to-shell download"),
]

# ---------------------------------------------------------------------------
# Non-blocking flags
# ---------------------------------------------------------------------------

UNSAFE_LEGAL_ADVICE_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"(évader|évasion|fraude[rz]?|échapper\s+(à|aux))\s+(l'impôt|les\s+impôts|le\s+fisc|la\s+loi|l'impôts)", re.I),
     "request to evade law or taxes"),
    (re.compile(r"\b(evade|avoid|cheat)\s+(tax(es)?|the\s+law|prosecution)\b", re.I),
     "request to evade law or taxes"),
    (re.compile(r"comment\s+(frauder|corrompre|soudoyer|blanchir)", re.I),
     "request for fraud/bribery/laundering instructions"),
    (re.compile(r"how\s+to\s+(bribe|launder|defraud|forge)", re.I),
     "request for fraud/bribery/forgery instructions"),
    (re.compile(r"pot[- ]de[- ]vin|corrompre\s+un\s+(agent|fonctionnaire|juge|policier)", re.I),
     "bribery-related request"),
    (re.compile(r"fausse\s+(déclaration|facture|identité)|faux\s+(document|papier)s?", re.I),
     "false declaration / forged document request"),
]

# ---------------------------------------------------------------------------
# PII / sensitive info (redacted, not blocked)
# ---------------------------------------------------------------------------

# Burkina Faso national ID (CNIB)-like numbers: long digit runs, often
# prefixed "BF". Pattern intentionally conservative to limit false hits.
_PII_RAW: list[tuple[str, str, str]] = [
    (r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b", "email address", "[EMAIL_MASQUÉ]"),
    (r"(?<!\d)(?:\+?226[\s.-]?)?(?:\d[\s.-]?){8}(?!\d)", "phone number", "[TÉLÉPHONE_MASQUÉ]"),
    (r"\bBF[-\s]?\d{6,10}\b", "Burkina Faso ID number", "[IDENTIFIANT_MASQUÉ]"),
    (r"\b(?:\d[ -]?){13,16}\b", "payment card number", "[CARTE_MASQUÉE]"),
]

PII_PATTERNS: list[tuple[re.Pattern, str, str]] = [
    (re.compile(p, re.I), desc, placeholder) for p, desc, placeholder in _PII_RAW
]

# ---------------------------------------------------------------------------
# Lookup used by both guards
# ---------------------------------------------------------------------------

BLOCKING_TABLES: list[tuple[RiskFlag, list[tuple[re.Pattern, str]]]] = [
    (RiskFlag.PROMPT_INJECTION, PROMPT_INJECTION_PATTERNS),
    (RiskFlag.JAILBREAK, JAILBREAK_PATTERNS),
    (RiskFlag.ROLE_HIJACKING, ROLE_HIJACKING_PATTERNS),
    (RiskFlag.TOOL_ABUSE, TOOL_ABUSE_PATTERNS),
]

__all__ = [
    "BLOCKING_TABLES",
    "PROMPT_INJECTION_PATTERNS",
    "JAILBREAK_PATTERNS",
    "ROLE_HIJACKING_PATTERNS",
    "TOOL_ABUSE_PATTERNS",
    "UNSAFE_LEGAL_ADVICE_PATTERNS",
    "PII_PATTERNS",
]
