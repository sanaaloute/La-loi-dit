import asyncio
from types import SimpleNamespace

from backend.core.config import Settings
from backend.core.models import EvidenceChunk
from backend.ingestion.versioning import VersionStore
from backend.retrieval.bm25 import hydrate_bm25
from backend.vectorstore.memory_store import InMemoryVectorStore


def test_hydrate_and_refresh_from_shared_vector_store(tmp_path):
    settings = Settings(llm_provider="mock", data_dir=tmp_path)
    store = InMemoryVectorStore()
    versions = VersionStore(tmp_path)
    first = EvidenceChunk(
        chunk_id="child-1",
        document_id="doc-1",
        content="Le préavis de licenciement est d'un mois.",
        metadata={"role": "child"},
    )
    heading = EvidenceChunk(
        chunk_id="heading-1",
        document_id="doc-1",
        content="Titre I",
        metadata={"role": "heading"},
    )
    third = EvidenceChunk(
        chunk_id="child-3",
        document_id="doc-3",
        content="La constitution est laïque.",
        metadata={"role": "child"},
    )
    fourth = EvidenceChunk(
        chunk_id="child-4",
        document_id="doc-4",
        content="Les impôts sont obligatoires.",
        metadata={"role": "child"},
    )
    asyncio.run(store.upsert(
        [first, heading, third, fourth],
        [[1.0, 0.0], [0.0, 1.0], [1.0, 1.0], [1.0, 0.0]],
    ))
    versions.commit_version("doc-1", "hash-1", 1)
    versions.commit_version("doc-3", "hash-3", 1)
    versions.commit_version("doc-4", "hash-4", 1)
    ctx = SimpleNamespace(settings=settings, vector_store=store, extras={})

    hydrated = asyncio.run(hydrate_bm25(ctx))

    assert hydrated.size == 3
    assert hydrated.search("licenciement", top_k=3)[0].chunk_id == "child-1"
    assert ctx.extras["bm25_chunk_count"] == 3

    second = EvidenceChunk(
        chunk_id="child-2",
        document_id="doc-2",
        content="Le divorce est prononcé par le tribunal.",
        metadata={"role": "child"},
    )
    asyncio.run(store.upsert([second], [[0.0, 1.0]]))
    versions.commit_version("doc-2", "hash-2", 1)

    refreshed = asyncio.run(hydrate_bm25(ctx))

    assert refreshed.size == 4
    assert refreshed.search("divorce", top_k=3)[0].chunk_id == "child-2"
    assert ctx.extras["bm25_chunk_count"] == 4
