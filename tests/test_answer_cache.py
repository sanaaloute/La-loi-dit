import asyncio
from datetime import date

from backend.core.answer_cache import AnswerCache, cache_identity, is_cacheable
from backend.core.cache import InMemoryCache
from backend.core.config import Settings
from backend.core.embeddings import HashEmbeddings
from backend.ingestion.versioning import VersionStore


def _identity(settings, **overrides):
    kwargs = {
        "model_id": "afrikia/nemotron-3-nano",
        "language": "fr",
        "scenario_date": None,
        "user_id": "user-1",
    }
    kwargs.update(overrides)
    return cache_identity(settings, **kwargs)


def test_cache_identity_isolates_answer_changing_context(tmp_path):
    settings = Settings(llm_provider="mock", data_dir=tmp_path)
    base = _identity(settings)

    assert _identity(settings) == base
    assert _identity(settings, language="en") != base
    assert _identity(settings, scenario_date=date(2020, 1, 1)) != base
    assert _identity(settings, user_id="user-2") != base
    assert _identity(settings, model_id="afrikia/other") != base

    VersionStore(tmp_path).commit_version("doc-1", "hash", 1)
    assert _identity(settings) != base


def test_exact_and_semantic_cache_do_not_cross_contexts(tmp_path):
    settings = Settings(llm_provider="mock", data_dir=tmp_path, embedding_dimension=16)
    cache = AnswerCache(InMemoryCache(), HashEmbeddings(16), settings)
    first = _identity(settings)
    second = _identity(settings, scenario_date=date(2020, 1, 1))
    response = {"answer": {"answer": "Réponse", "confidence": 1.0}}

    asyncio.run(cache.set("Quel est le préavis ?", first, response))

    assert asyncio.run(cache.get("Quel est le préavis ?", first)) == response
    assert asyncio.run(cache.get("Quel est le préavis ?", second)) is None
    assert asyncio.run(cache.get("Quel est le preavis", second)) is None


def test_abstaining_or_low_coverage_answers_are_not_cacheable(tmp_path):
    settings = Settings(llm_provider="mock", data_dir=tmp_path)
    solid = {"answer": {"answer": "Réponse", "confidence": 0.9, "metadata": {}}}
    abstaining = {
        "answer": {
            "answer": "Non couvert",
            "confidence": 0.96,
            "metadata": {"abstention_detected": ["n'est pas couverte"]},
        }
    }
    low_coverage = {
        "answer": {
            "answer": "Partiel",
            "confidence": 0.9,
            "metadata": {"answer_coverage": 0.2},
        }
    }

    assert is_cacheable(solid, settings) is True
    assert is_cacheable(abstaining, settings) is False
    assert is_cacheable(low_coverage, settings) is False


class _ConstantEmbedder:
    """Every text maps to the same vector: without the numeric-token guard,
    any two same-model queries would be a perfect semantic hit."""

    async def embed(self, texts):
        return [[1.0, 0.0, 0.0, 0.0] for _ in texts]


def test_semantic_hit_requires_identical_numeric_tokens(tmp_path):
    settings = Settings(llm_provider="mock", data_dir=tmp_path)
    cache = AnswerCache(InMemoryCache(), _ConstantEmbedder(), settings)
    identity = _identity(settings)
    response = {"answer": {"answer": "Délai de l'article 218", "confidence": 0.9}}

    asyncio.run(cache.set("Quel est le délai de l'article 218 ?", identity, response))

    # Same template, different article number: a DIFFERENT legal question.
    assert asyncio.run(cache.get("Quel est le délai de l'article 341 ?", identity)) is None
    # Same numbers: semantic hit allowed (exact-match would hit anyway).
    assert asyncio.run(cache.get("délai article 218", identity)) == response
