"""Retrieval tools: child-first vector search, keyword, official sources and
parent lookup.

These tools wrap the existing retrieval subsystem so agents can invoke them
without importing infrastructure directly.
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field

from backend.agents.tools.base import ToolResult, tool
from backend.agents.tools.registry import register_tool


class VectorSearchArgs(BaseModel):
    query: str
    top_k: int = 8
    filters: dict[str, Any] = Field(default_factory=dict)


class KeywordSearchArgs(BaseModel):
    query: str
    top_k: int = 8
    filters: dict[str, Any] = Field(default_factory=dict)


class OfficialSourceSearchArgs(BaseModel):
    query: str
    top_k: int = 5
    kind: str = "government"  # SearchKind value


class FetchParentChunksArgs(BaseModel):
    parent_chunk_ids: list[str]


class EmbedQueryArgs(BaseModel):
    query: str


@tool("vector_search_children", "Dense semantic search over child chunks in the local vector index.")
async def vector_search_children(ctx: Any, state: Any, args: VectorSearchArgs) -> list[Any]:
    """Return child chunks most similar to the query."""
    if ctx.vector_store is None:
        raise RuntimeError("vector store is not available")
    vector = (await ctx.embedder.embed([args.query]))[0]
    filters = dict(args.filters)
    filters["role"] = "child"
    return await ctx.vector_store.search(vector, top_k=args.top_k, filters=filters)


@tool("keyword_search", "BM25 keyword search over the indexed corpus.")
async def keyword_search(ctx: Any, state: Any, args: KeywordSearchArgs) -> list[Any]:
    """Return keyword matches for the query."""
    from backend.retrieval.bm25 import BM25Retriever

    retriever = ctx.extras.get("bm25")
    if retriever is None:
        retriever = BM25Retriever()
        ctx.extras["bm25"] = retriever
    filters = dict(args.filters)
    return retriever.search(args.query, top_k=args.top_k, filters=filters or None)


@tool("official_source_search", "Search Burkina Faso official sources (government, gazette, OHADA, etc.).")
async def official_source_search(ctx: Any, state: Any, args: OfficialSourceSearchArgs) -> list[Any]:
    """Return evidence from official web/RSS sources."""
    from backend.core.models import SearchKind, SearchTask
    from backend.search.orchestrator import search_sources

    try:
        kind = SearchKind(args.kind)
    except ValueError:
        raise ValueError(f"unknown search kind: {args.kind!r}")
    task = SearchTask(kind=kind, query=args.query, top_k=args.top_k)
    return await search_sources([task])


@tool("fetch_parent_chunks", "Fetch the parent chunks of a set of child chunks by their IDs.")
async def fetch_parent_chunks(ctx: Any, state: Any, args: FetchParentChunksArgs) -> list[Any]:
    """Return parent chunks for the supplied parent_chunk_ids."""
    if ctx.vector_store is None:
        raise RuntimeError("vector store is not available")
    if not args.parent_chunk_ids:
        return []
    return await ctx.vector_store.get_by_ids(list(args.parent_chunk_ids))


@tool("embed_query", "Embed a query string for downstream retrieval or comparison.")
async def embed_query(ctx: Any, state: Any, args: EmbedQueryArgs) -> list[float]:
    """Return the embedding vector for the query."""
    return (await ctx.embedder.embed([args.query]))[0]


register_tool(vector_search_children)
register_tool(keyword_search)
register_tool(official_source_search)
register_tool(fetch_parent_chunks)
class ExecuteRetrievalPlanArgs(BaseModel):
    tasks: list[dict[str, Any]]


@tool("execute_retrieval_plan", "Execute all search tasks in parallel, deduplicate, fuse and rerank the results.")
async def execute_retrieval_plan(ctx: Any, state: Any, args: ExecuteRetrievalPlanArgs) -> list[Any]:
    """Run the retrieval coordinator over a list of SearchTask dicts."""
    from backend.core.models import SearchTask

    if ctx.retriever is None:
        raise RuntimeError("retriever is not available")
    tasks = [SearchTask.model_validate(t) for t in args.tasks]
    return await ctx.retriever.retrieve(tasks)


register_tool(execute_retrieval_plan)
