"""Vector store adapters: in-memory (default/offline) and Milvus (production)."""

from backend.vectorstore.factory import get_vector_store
from backend.vectorstore.memory_store import InMemoryVectorStore

__all__ = ["InMemoryVectorStore", "get_vector_store"]
