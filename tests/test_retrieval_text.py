"""Contextual retrieval_text wiring: BM25 corpus, pipeline embed, reranker.

Article children carry ``retrieval_text`` = context prefix + raw content; it
is the text embedded and keyword-indexed, while heading chunks
(``role="heading"``) never enter the BM25 corpus.  Chunks without
``retrieval_text`` fall back to raw ``content`` everywhere.
"""

from __future__ import annotations

from backend.core.models import EvidenceChunk
from backend.retrieval.bm25 import BM25Retriever


class _RecordingEmbedder:
    def __init__(self) -> None:
        self.texts: list[str] = []

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.texts.extend(texts)
        return [[1.0, 0.0] for _ in texts]


# --- BM25 ----------------------------------------------------------------------


def test_bm25_excludes_heading_chunks_from_the_corpus():
    retriever = BM25Retriever()
    heading = EvidenceChunk(
        content="LIVRE IV : DES CRIMES INTERNATIONAUX",
        metadata={"role": "heading"},
    )
    article = EvidenceChunk(
        content="Article 1\nLe génocide est puni de la réclusion criminelle.",
        metadata={"role": "child"},
    )
    retriever.add_documents([heading, article])
    assert retriever.size == 1
    hits = retriever.search("crimes internationaux", top_k=5)
    assert all(c.metadata.get("role") != "heading" for c in hits)


def test_bm25_indexes_retrieval_text_instead_of_raw_content():
    retriever = BM25Retriever()
    prefixed = EvidenceChunk(
        content="Texte brut sans mot repère.",
        retrieval_text="« Code pénal — Livre IV — Article 1. »\nTexte brut sans mot repère.",
    )
    other = EvidenceChunk(content="Une disposition sans rapport.")
    retriever.add_documents([prefixed, other])
    # "pénal" only exists in the prefix: it must rank the prefixed chunk first
    hits = retriever.search("pénal", top_k=2)
    assert hits and hits[0] is prefixed


def test_bm25_falls_back_to_raw_content_without_retrieval_text():
    retriever = BM25Retriever()
    retriever.add_documents([EvidenceChunk(content="réclusion criminelle à perpétuité")])
    assert retriever.search("réclusion", top_k=5)


# --- pipeline embed --------------------------------------------------------------


async def test_pipeline_embed_uses_retrieval_text_when_set(ctx):
    from backend.ingestion.pipeline import IngestionPipeline

    embedder = _RecordingEmbedder()
    ctx.embedder = embedder
    pipeline = IngestionPipeline(ctx)
    prefixed = EvidenceChunk(
        content="Texte brut.", retrieval_text="« Doc — Article 1. »\nTexte brut."
    )
    plain = EvidenceChunk(content="Sans préfixe.")
    await pipeline._embed([prefixed, plain])
    assert embedder.texts == ["« Doc — Article 1. »\nTexte brut.", "Sans préfixe."]


async def test_reranker_embeds_retrieval_text_when_set():
    from backend.retrieval.reranker import rerank

    embedder = _RecordingEmbedder()
    prefixed = EvidenceChunk(
        content="Texte brut.", retrieval_text="« Doc — Article 1. »\nTexte brut."
    )
    await rerank("question", [prefixed], top_k=1, embedder=embedder)
    assert embedder.texts == ["question", "« Doc — Article 1. »\nTexte brut."]
