.PHONY: install dev test lint run docker-up docker-down eval ingest reindex reindex-local

install:
	pip install -r requirements.txt
	pip install -e .

dev:
	uvicorn backend.api.main:app --reload --host 0.0.0.0 --port 8000

run:
	python -m backend.api.main

test:
	pytest

lint:
	python -m compileall backend -q

docker-up:
	./scripts/init-data-dir.sh
	docker compose up -d --build

docker-down:
	docker compose down

eval:
	python -m backend.evaluation.runner --dataset backend/evaluation/golden_dataset.json

ingest:
	python -m backend.ingestion.pipeline --help

# Re-index after adding/editing/deleting documents in ./data/legal_docs.
# The pipeline diffs content hashes: changed files are re-indexed, unchanged
# ones skipped, deleted files dropped from the index (GC). --full-reindex
# wipes and rebuilds the whole index.
reindex:
	docker compose exec -T api python -m backend.ingestion.pipeline /app/data/legal_docs

reindex-local:
	python -m backend.ingestion.pipeline data/legal_docs
