.PHONY: install dev test lint run docker-up docker-down eval ingest

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
	docker compose up -d --build

docker-down:
	docker compose down

eval:
	python -m backend.evaluation.runner --dataset backend/evaluation/golden_dataset.json

ingest:
	python -m backend.ingestion.pipeline --help
