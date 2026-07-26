"""Retrieval subsystem: BM25, fusion, dedup, reranking, workers, coordinator."""

from backend.retrieval.coordinator import RetrievalCoordinator

__all__ = ["RetrievalCoordinator"]
