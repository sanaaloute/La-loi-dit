"""Verify against the real Milvus server at localhost:19530."""

import asyncio

from pymilvus import MilvusClient

from backend.core.context import build_context


async def main() -> None:
    client = MilvusClient(uri="http://localhost:19530")
    cols = client.list_collections()
    print("SERVER COLLECTIONS:", cols)
    for name in cols:
        stats = client.get_collection_stats(name)
        print(f"  {name}: rows={stats.get('row_count')}")

    ctx = await build_context()
    store = ctx.vector_store
    print("store type:", type(store).__name__, "| count via store:", await store.count())

    probes = [
        "durée du préavis de licenciement",
        "création d'une SARL capital minimum",
        "droits fondamentaux constitution",
    ]
    for query in probes:
        vectors = await ctx.embedder.embed([query])
        hits = await store.search(vectors[0], top_k=3)
        print(f"\nPROBE: {query}")
        if not hits:
            print("  (no hits)")
            continue
        top = hits[0]
        snippet = " ".join(top.content.split())[:300]
        print(f"  top doc: {top.document_name}")
        print(f"  article: {top.article or '-'} | section: {top.section or '-'} | page: {top.page or '-'}")
        print(f"  url: {top.url}")
        print(f"  score: {top.retrieval_score:.4f}")
        print(f"  snippet: {snippet}")


asyncio.run(main())
