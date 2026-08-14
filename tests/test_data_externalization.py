"""Data externalization tests (jurisdiction-configurable JSON data files).

Covers: bundled defaults match the previous built-ins, custom path overrides
change behavior through the new settings, and missing/corrupt files fall back
with a structured warning instead of crashing.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from backend.core import constants
from backend.core.config import Settings
from backend.core.models import AuthorityLevel, QuestionType, SearchKind
from backend.ingestion import crawler, freshness
from backend.ingestion.pipeline import IngestionPipeline, load_document_titles
from backend.planner import decomposition, terminology
from backend.search import sources
from backend.tools import legal_calculations

DATA_DIR = Path(__file__).resolve().parents[1] / "data"


def _settings(monkeypatch: pytest.MonkeyPatch, **overrides) -> Settings:
    """Point the lazy ``get_settings()`` lookups at an ad-hoc Settings."""
    settings = Settings(**overrides)
    monkeypatch.setattr("backend.core.config.get_settings", lambda: settings)
    return settings


# ---------------------------------------------------------------------------
# Bundled JSON files: valid + matching the previous built-ins
# ---------------------------------------------------------------------------


def test_bundled_json_files_are_valid():
    terminology_data = json.loads((DATA_DIR / "terminology.json").read_text(encoding="utf-8"))
    decomposition_data = json.loads((DATA_DIR / "decomposition.json").read_text(encoding="utf-8"))
    sources_data = json.loads((DATA_DIR / "legal_sources.json").read_text(encoding="utf-8"))

    assert isinstance(terminology_data["terms"], list) and terminology_data["terms"]
    assert isinstance(decomposition_data["topics"], dict) and decomposition_data["topics"]
    for section in (
        "search_registry",
        "crawler_allowed_domains",
        "freshness_registry",
        "document_titles",
    ):
        assert sources_data[section], section


def test_terminology_default_matches_builtin():
    loaded = terminology.load_lexicon()
    assert len(loaded) == len(terminology.LEXICON) == 56
    assert {e.canonical for e in loaded} == {e.canonical for e in terminology.LEXICON}
    # End-to-end semantics unchanged.
    expansions = terminology.expand_terms("droits d'un salarié licencié")
    assert "licenciement" in expansions
    assert "rupture du contrat de travail" in expansions["licenciement"]


def test_decomposition_default_matches_builtin():
    loaded = decomposition.load_taxonomy()
    assert set(loaded) == set(decomposition._TOPIC_TAXONOMY)
    assert "licenciement" in loaded
    issues = decomposition.deterministic_decompose(
        "Quels sont mes droits en cas de licenciement ?", QuestionType.RIGHTS, ["labor_code"]
    )
    assert "indemnité de licenciement" in issues


def test_search_registry_default_matches_builtin():
    registry = sources.load_registry()
    assert len(registry) == len(sources.DEFAULT_REGISTRY) == 9
    domains = {s.base_url for s in registry}
    assert "https://www.gouv.bf" in domains
    assert "https://www.jo.gouv.bf" in domains
    assert "https://www.ohada.org" in domains


def test_crawler_allowed_domains_default_matches_builtin():
    domains = crawler.load_allowed_domains()
    assert tuple(domains) == crawler.DEFAULT_ALLOWED_DOMAINS
    assert "legiburkina.bf" in domains
    assert "ohada.org" in domains


def test_freshness_registry_default_matches_builtin():
    registry = freshness.load_registry()
    assert len(registry) == len(freshness.DEFAULT_REGISTRY) == 4
    assert any(s.url == "https://www.ohada.org/feed/" for s in registry)


def test_document_titles_default_matches_builtin():
    titles = load_document_titles()
    assert titles == IngestionPipeline._DOCUMENT_TITLE_MAP
    assert (
        IngestionPipeline._display_name("code-du-travail-burkina-faso.pdf")
        == "Code du travail du Burkina Faso (Loi 028-2008/AN)"
    )


# ---------------------------------------------------------------------------
# Custom path overrides change behavior
# ---------------------------------------------------------------------------


def test_terminology_path_override(monkeypatch, tmp_path):
    custom = tmp_path / "terms.json"
    custom.write_text(
        json.dumps(
            {
                "terms": [
                    {
                        "canonical": "droit coutumier",
                        "synonyms": ["customary law"],
                        "related_terms": ["justice traditionnelle"],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    _settings(monkeypatch, terminology_path=str(custom))
    try:
        assert terminology.lookup("customary law") is not None
        assert terminology.lookup("licenciement") is None  # file fully replaces
        assert terminology.expand_terms("customary law") == {
            "droit coutumier": ["justice traditionnelle"]  # query term itself dropped
        }
    finally:
        terminology._LEXICON_CACHE.pop(str(custom), None)
        terminology._ALIAS_INDEX.pop(str(custom), None)


def test_decomposition_path_override(monkeypatch, tmp_path):
    custom = tmp_path / "topics.json"
    custom.write_text(
        json.dumps(
            {
                "topics": {
                    "coutume": {
                        "domains": ["customary_law"],
                        "keywords": ["coutume"],
                        "issues": ["rôle de la coutume"],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    _settings(monkeypatch, decomposition_path=str(custom))
    try:
        issues = decomposition.deterministic_decompose(
            "Quelle est la place de la coutume ?", QuestionType.GENERAL, ["customary_law"]
        )
        assert issues == ["rôle de la coutume"]
        # Built-in topics are replaced by the file.
        assert decomposition.deterministic_decompose(
            "licenciement", QuestionType.RIGHTS, []
        ) == []
    finally:
        decomposition._TAXONOMY_CACHE.pop(str(custom), None)


def test_legal_sources_path_override(tmp_path):
    custom = tmp_path / "sources.json"
    custom.write_text(
        json.dumps(
            {
                "search_registry": [
                    {
                        "name": "Portail Exemple",
                        "base_url": "https://www.example.gov",
                        "authority": "official_news",
                        "kind": "government",
                        "government_body": "Gouvernement Exemple",
                    }
                ],
                "crawler_allowed_domains": ["example.gov"],
                "freshness_registry": [
                    {"name": "Flux Exemple", "url": "https://www.example.gov/feed/", "kind": "rss"}
                ],
                "document_titles": {"code-exemple.pdf": "Code Exemple (Loi 001-2020)"},
            }
        ),
        encoding="utf-8",
    )
    registry = sources.load_registry(custom)
    assert [s.base_url for s in registry] == ["https://www.example.gov"]
    # Injectable-param behavior preserved.
    gov = sources.sources_for_kind(SearchKind.GOVERNMENT, registry)
    assert len(gov) == 1 and gov[0].name == "Portail Exemple"
    assert sources.sources_for_kind(SearchKind.CASE_LAW, registry) == []

    assert crawler.load_allowed_domains(custom) == ("example.gov",)

    freshness_registry = freshness.load_registry(custom)
    assert [s.url for s in freshness_registry] == ["https://www.example.gov/feed/"]


def test_legal_sources_path_override_document_titles(monkeypatch, tmp_path):
    custom = tmp_path / "sources.json"
    custom.write_text(
        json.dumps({"document_titles": {"code-exemple.pdf": "Code Exemple (Loi 001-2020)"}}),
        encoding="utf-8",
    )
    _settings(monkeypatch, legal_sources_path=str(custom))
    from backend.ingestion import pipeline as pipeline_module

    key = f"{custom}#document_titles"
    try:
        assert pipeline_module.load_document_titles()["code-exemple.pdf"] == "Code Exemple (Loi 001-2020)"
        assert IngestionPipeline._display_name("code-exemple.pdf") == "Code Exemple (Loi 001-2020)"
    finally:
        pipeline_module._TITLE_CACHE.pop(key, None)


def test_document_titles_path_override(monkeypatch, tmp_path):
    custom = tmp_path / "titles.json"
    custom.write_text(json.dumps({"nouveau-code.pdf": "Nouveau Code (Loi 002-2021)"}), encoding="utf-8")
    _settings(monkeypatch, document_titles_path=str(custom))
    from backend.ingestion import pipeline as pipeline_module

    try:
        assert IngestionPipeline._display_name("nouveau-code.pdf") == "Nouveau Code (Loi 002-2021)"
        # Standalone file fully replaces the embedded map.
        assert (
            IngestionPipeline._display_name("code-du-travail-burkina-faso.pdf")
            == "code-du-travail-burkina-faso.pdf"
        )
    finally:
        pipeline_module._TITLE_CACHE.pop(str(custom), None)


def test_crawler_extra_allowed_domains():
    settings = Settings(crawler_extra_allowed_domains="example.gov, beta.gouv.bf ,,")
    allowed = crawler._default_allowed("seed.example.gov", settings)
    assert "seed.example.gov" in allowed
    assert "example.gov" in allowed
    assert "beta.gouv.bf" in allowed
    assert "legiburkina.bf" in allowed  # file defaults still merged


def test_authority_config_override(tmp_path):
    custom = tmp_path / "authority.json"
    custom.write_text(
        json.dumps(
            {
                "authority_weights": {"blog": 0.99, "LAW": 0.91},
                "official_domains": ["example.gov"],
                "legal_domains": ["customary_law"],
            }
        ),
        encoding="utf-8",
    )
    weights = constants.load_authority_weights(custom)
    assert weights[AuthorityLevel.BLOG] == 0.99  # overridden (enum value key)
    assert weights[AuthorityLevel.LAW] == 0.91  # overridden (enum name key)
    assert weights[AuthorityLevel.CONSTITUTION] == 1.00  # partial merge keeps defaults
    assert constants.load_official_domains(custom) == ("example.gov",)
    assert constants.load_legal_domains(custom) == ("customary_law",)


def test_authority_config_path_setting_routes_authority_for_url(monkeypatch, tmp_path):
    custom = tmp_path / "authority.json"
    custom.write_text(json.dumps({"official_domains": ["example.gov"]}), encoding="utf-8")
    _settings(monkeypatch, authority_config_path=str(custom))
    assert sources.authority_for_url("https://www.example.gov/texte") == AuthorityLevel.OFFICIAL_NEWS
    assert sources.authority_for_url("https://blog.example.com/x") == AuthorityLevel.UNKNOWN


def test_legal_rules_path_override(monkeypatch, tmp_path):
    custom = tmp_path / "rules.json"
    custom.write_text(
        json.dumps(
            {
                "notice_periods": [
                    {
                        "rule_id": "test-notice",
                        "label": "Catégorie test",
                        "categories": ["categorie test"],
                        "duration": 5,
                        "unit": "days",
                        "source": "Texte de test",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    _settings(monkeypatch, legal_rules_path=str(custom))
    legal_calculations.load_rules.cache_clear()
    try:
        rules = legal_calculations.load_rules()
        assert rules["notice_periods"][0]["rule_id"] == "test-notice"
        result = legal_calculations.compute_notice_period("categorie test")
        assert result.value == 5
        assert result.unit == "days"
    finally:
        legal_calculations.load_rules.cache_clear()


# ---------------------------------------------------------------------------
# Missing/corrupt files: fallback + structured warning, never a crash
# ---------------------------------------------------------------------------


def test_terminology_corrupt_file_falls_back(monkeypatch, tmp_path, caplog):
    corrupt = tmp_path / "terms.json"
    corrupt.write_text("{not valid json", encoding="utf-8")
    _settings(monkeypatch, terminology_path=str(corrupt))
    try:
        with caplog.at_level(logging.WARNING, logger="backend.planner.terminology"):
            expansions = terminology.expand_terms("droits d'un salarié licencié")
        assert "licenciement" in expansions  # embedded fallback still works
        record = next(r for r in caplog.records if r.message == "terminology_load_failed")
        assert record.fallback == "embedded_lexicon"
        assert record.path == str(corrupt)
    finally:
        terminology._LEXICON_CACHE.pop(str(corrupt), None)
        terminology._ALIAS_INDEX.pop(str(corrupt), None)


def test_decomposition_missing_file_falls_back(monkeypatch, tmp_path, caplog):
    missing = tmp_path / "nope.json"
    _settings(monkeypatch, decomposition_path=str(missing))
    try:
        with caplog.at_level(logging.WARNING, logger="backend.planner.decomposition"):
            issues = decomposition.deterministic_decompose(
                "licenciement", QuestionType.RIGHTS, []
            )
        assert issues  # embedded taxonomy still decomposes
        assert any(r.message == "decomposition_load_failed" for r in caplog.records)
    finally:
        decomposition._TAXONOMY_CACHE.pop(str(missing), None)


def test_legal_sources_corrupt_file_falls_back(tmp_path, caplog):
    corrupt = tmp_path / "sources.json"
    corrupt.write_text("[1, 2", encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        registry = sources.load_registry(corrupt)
        domains = crawler.load_allowed_domains(corrupt)
        monitored = freshness.load_registry(corrupt)
    assert len(registry) == 9  # embedded fallbacks
    assert "legiburkina.bf" in domains
    assert any(s.url == "https://www.ohada.org/feed/" for s in monitored)
    assert any(r.message == "search_registry_load_failed" for r in caplog.records)
    assert any(r.message == "crawler_allowed_domains_load_failed" for r in caplog.records)
    assert any(r.message == "freshness_registry_load_failed" for r in caplog.records)


def test_document_titles_corrupt_file_falls_back(monkeypatch, tmp_path, caplog):
    corrupt = tmp_path / "titles.json"
    corrupt.write_text("42", encoding="utf-8")  # valid JSON, wrong shape
    _settings(monkeypatch, document_titles_path=str(corrupt))
    from backend.ingestion import pipeline as pipeline_module

    try:
        with caplog.at_level(logging.WARNING, logger="backend.ingestion.pipeline"):
            title = IngestionPipeline._display_name("code-du-travail-burkina-faso.pdf")
        assert title == "Code du travail du Burkina Faso (Loi 028-2008/AN)"
        assert any(r.message == "document_titles_load_failed" for r in caplog.records)
    finally:
        pipeline_module._TITLE_CACHE.pop(str(corrupt), None)


def test_authority_config_corrupt_file_falls_back(monkeypatch, tmp_path, caplog):
    corrupt = tmp_path / "authority.json"
    corrupt.write_text("{broken", encoding="utf-8")
    _settings(monkeypatch, authority_config_path=str(corrupt))
    with caplog.at_level(logging.WARNING, logger="backend.core.constants"):
        assert constants.load_official_domains() == constants.OFFICIAL_DOMAINS
        domains = constants.load_legal_domains()
        weights = constants.load_authority_weights()
    # Defaults first, then any extra slugs merged from data/legal_domains.json.
    assert domains[: len(constants.LEGAL_DOMAINS)] == constants.LEGAL_DOMAINS
    assert "traffic_law" in domains
    assert weights == constants.AUTHORITY_WEIGHTS
    assert any(r.message == "authority_config_load_failed" for r in caplog.records)


def test_load_legal_domains_merges_taxonomy_slugs(monkeypatch, tmp_path):
    """Domains added to the taxonomy file reach retrieval planning (no override)."""
    from backend.ingestion import classification

    taxonomy = tmp_path / "legal_domains.json"
    taxonomy.write_text(
        json.dumps(
            {"version": 1, "domains": {"space_law": {"label": "Droit spatial", "keywords": ["satellite"]}}}
        ),
        encoding="utf-8",
    )
    _settings(monkeypatch, legal_domains_path=str(taxonomy))
    try:
        domains = constants.load_legal_domains()
        assert domains[: len(constants.LEGAL_DOMAINS)] == constants.LEGAL_DOMAINS
        assert "space_law" in domains
    finally:
        classification.invalidate_domain_cache()


def test_load_rules_default_still_bundled():
    legal_calculations.load_rules.cache_clear()
    try:
        rules = legal_calculations.load_rules()
        assert rules["notice_periods"]  # bundled backend/tools/legal_rules.json
    finally:
        legal_calculations.load_rules.cache_clear()
