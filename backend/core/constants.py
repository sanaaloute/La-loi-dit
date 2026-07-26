"""Static domain data: authority weights, official domains, legal domains.

Tunable policy values (retry budgets, thresholds, top-k, timeouts) are NOT
constants — they live in ``backend.core.config.Settings`` (env prefix
``LEGAL_AI_``); never hardcode them here. The retry strategy is
intentionally strict to avoid infinite loops: max retry = 1 everywhere
(planning, retrieval, reflection) by default.
"""

from backend.core.models import AuthorityLevel

# --- Authority-weighted evidence ranking ---
# Constitution > OHADA treaty > (amended) law > decree > order > ministerial
# circular > official gazette/case law > official press release > official news
# > uploaded document > trusted legal site > news > blog.
AUTHORITY_WEIGHTS: dict[AuthorityLevel, float] = {
    AuthorityLevel.CONSTITUTION: 1.00,
    AuthorityLevel.TREATY_OHADA: 0.95,
    AuthorityLevel.LAW: 0.90,
    AuthorityLevel.AMENDED_LAW: 0.92,
    AuthorityLevel.DECREE: 0.80,
    AuthorityLevel.ORDER: 0.72,
    AuthorityLevel.MINISTERIAL_CIRCULAR: 0.65,
    AuthorityLevel.OFFICIAL_GAZETTE: 0.78,
    AuthorityLevel.CASE_LAW: 0.70,
    AuthorityLevel.OFFICIAL_PRESS_RELEASE: 0.55,
    AuthorityLevel.OFFICIAL_NEWS: 0.45,
    AuthorityLevel.UPLOADED_DOCUMENT: 0.50,
    AuthorityLevel.TRUSTED_LEGAL_SITE: 0.40,
    AuthorityLevel.NEWS: 0.25,
    AuthorityLevel.BLOG: 0.10,
    AuthorityLevel.UNKNOWN: 0.15,
}

# Official Burkina Faso / OHADA domains never outranked by blogs.
OFFICIAL_DOMAINS = (
    "gouv.bf",
    "assembleenationale.bf",
    "conseil-constitutionnel.bf",
    "coursupreme.bf",
    "jo.gouv.bf",
    "ohada.org",
    "ohada.com",
    "finances.gov.bf",
    "travail.gov.bf",
    "justice.gov.bf",
)

# Domains the system covers; the planner maps keywords to these.
LEGAL_DOMAINS = (
    "constitution",
    "criminal_law",
    "civil_law",
    "family_code",
    "labor_code",
    "tax_law",
    "commercial_law",
    "ohada_law",
    "administrative_law",
    "land_law",
    "procurement_law",
    "environmental_law",
    "immigration",
    "public_service",
    "elections",
    "health_regulations",
    "education_regulations",
    "government_procedures",
)

DEFAULT_RESPONSE_LANGUAGE = "fr"  # Burkina Faso's official language
