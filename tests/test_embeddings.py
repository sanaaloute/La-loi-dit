"""Token-safe embedding batching (backend/core/embeddings.py).

Dense French OCR runs ≈1 token/char, so a count-only batch of 1400-char
children overflows the embedding model's 8192-token context. Batches must
respect both the count cap and the char budget, without reordering texts.
"""

import pytest

from backend.core.config import Settings
from backend.core.embeddings import LiteLLMEmbeddings


class _RecordingEmbeddings(LiteLLMEmbeddings):
    def __init__(self, settings):
        super().__init__(settings)
        self.batches: list[list[str]] = []

    async def _embed_batch(self, texts):
        self.batches.append(list(texts))
        return [[float(i)] for i, _ in enumerate(texts)]


def _settings() -> Settings:
    return Settings(
        embedding_batch_size=4,
        embedding_batch_max_chars=1000,
        embedding_dimension=8,
    )


@pytest.mark.asyncio
async def test_batches_respect_char_budget_and_order():
    emb = _RecordingEmbeddings(_settings())
    texts = ["a" * 600, "b" * 600, "c" * 300, "d" * 700, "e" * 100]
    vectors = await emb.embed(texts)

    assert [len(v) for v in vectors] == [1] * 5
    joined = [t for batch in emb.batches for t in batch]
    assert joined == texts  # order preserved
    for batch in emb.batches:
        assert sum(len(t) for t in batch) <= 1000 or len(batch) == 1
    assert emb.batches[0] == ["a" * 600]  # 600+600 would overflow the budget
    assert emb.batches[1] == ["b" * 600, "c" * 300]  # 600+300 fits


@pytest.mark.asyncio
async def test_batches_respect_count_cap():
    emb = _RecordingEmbeddings(_settings())
    texts = ["x"] * 9
    await emb.embed(texts)
    assert [len(b) for b in emb.batches] == [4, 4, 1]


@pytest.mark.asyncio
async def test_oversized_single_text_is_truncated_to_budget():
    # A lone text beyond the char budget would overflow the model's context
    # window and fail the whole call; it is truncated instead of sent raw.
    emb = _RecordingEmbeddings(_settings())
    texts = ["a" * 5000, "b" * 10]
    await emb.embed(texts)
    assert emb.batches == [["a" * 1000], ["b" * 10]]


@pytest.mark.asyncio
async def test_pipeline_embeds_children_only(tmp_path):
    # Parents (whole articles, possibly beyond the embedding context window)
    # and headings get a zero vector: retrieval filters on role="child".
    from types import SimpleNamespace

    from backend.core.models import EvidenceChunk
    from backend.ingestion.pipeline import IngestionPipeline

    class _Fake:
        dimension = 4

        def __init__(self):
            self.texts: list[str] = []

        async def embed(self, texts):
            self.texts.extend(texts)
            return [[1.0] * 4 for _ in texts]

    embedder = _Fake()
    ctx = SimpleNamespace(
        settings=SimpleNamespace(data_dir=tmp_path, embedding_model="fake-model"),
        embedder=embedder,
    )
    pipe = IngestionPipeline(ctx)
    chunks = [
        EvidenceChunk(document_id="d", content="LONG PARENT " * 500, metadata={"role": "parent"}),
        EvidenceChunk(document_id="d", content="child one", metadata={"role": "child"}),
        EvidenceChunk(document_id="d", content="child two", metadata={"role": "child"}),
        EvidenceChunk(document_id="d", content="TITRE I", metadata={"role": "heading"}),
    ]
    vectors = await pipe._embed(chunks)
    assert embedder.texts == ["child one", "child two"]
    assert vectors[0] == [0.0] * 4
    assert vectors[3] == [0.0] * 4
    assert vectors[1] == [1.0] * 4
    assert vectors[2] == [1.0] * 4
