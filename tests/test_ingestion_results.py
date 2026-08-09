"""Ingestion-result persistence (spec §49).

Every ingest — success or failure — appends/merges a compact record into
``data/ingestion_results.json`` (latest record per document), so failed
documents stay visible beyond the POST /documents/reindex response.
"""

from __future__ import annotations

from backend.ingestion.pipeline import IngestionPipeline, load_ingestion_results


async def test_successful_ingest_records_success(ctx, settings):
    pipeline = IngestionPipeline(ctx)
    result = await pipeline.ingest_text(
        "code-travail-results.txt",
        "Article 1: Tout travailleur a droit à un salaire équitable.",
    )
    assert result.status == "indexed"

    records = load_ingestion_results(settings.data_dir)
    record = records[result.document_id]
    assert record["document_id"] == result.document_id
    assert record["document_name"] == result.document_name
    assert record["status"] == "indexed"
    assert record["version"] == 1
    assert record["chunks_created"] == result.chunks_created > 0
    assert record["timestamp"]
    assert "error" not in record


async def test_unreadable_file_records_failure(ctx, settings, tmp_path):
    pipeline = IngestionPipeline(ctx)
    missing = tmp_path / "unreadable.txt"  # never created: the loader fails
    result = await pipeline.ingest_path(missing)
    assert result.status == "failed"

    records = load_ingestion_results(settings.data_dir)
    record = records[result.document_id]
    assert record["status"] == "failed"
    assert record["error"]
    assert record["path"] == str(missing)
    assert record["chunks_created"] == 0
    assert record["timestamp"]


async def test_chunking_failure_records_failure(ctx, settings):
    """Failures raised inside _ingest_document (not only loader errors) persist."""
    pipeline = IngestionPipeline(ctx)
    result = await pipeline.ingest_text("empty.txt", "   \n\t  ")
    assert result.status == "failed"

    record = load_ingestion_results(settings.data_dir)[result.document_id]
    assert record["status"] == "failed"
    assert "No extractable text" in record["error"]


async def test_latest_record_per_document_overwrites(ctx, settings):
    pipeline = IngestionPipeline(ctx)
    text = "Article 1: Le présent code régit les relations de travail."
    first = await pipeline.ingest_text("code-latest.txt", text)
    assert first.status == "indexed"
    second = await pipeline.ingest_text("code-latest.txt", text)
    assert second.status == "skipped_duplicate"
    assert second.document_id == first.document_id

    records = load_ingestion_results(settings.data_dir)
    matching = [r for r in records.values() if r["document_id"] == first.document_id]
    assert len(matching) == 1
    assert matching[0]["status"] == "skipped_duplicate"
