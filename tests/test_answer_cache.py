import asyncio
from datetime import date

from backend.core.answer_cache import AnswerCache, cache_identity
from backend.core.cache import InMemoryCache
from backend.core.config import Settings
from backend.core.embeddings import HashEmbeddings
from backend.ingestion.versioning import VersionStore


def _identity(settings, **overrides):
    kwargs = {
        "model_id": "afrikia/gpt-oss-20b",
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
