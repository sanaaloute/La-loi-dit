#!/usr/bin/env python3
"""Probe the LIVE index with targeted queries through the real retrieval path.

Answers one question per query: does the indexed corpus actually surface the
expected code's chunks (Code civil for prescription, Code du travail for the
licenciement recours deadline, family code for divorce)? Run inside the api
container:

    docker cp scripts/probe_retrieval_live.py legal-ai-burkina-api-1:/app/scripts/
    docker exec legal-ai-burkina-api-1 python /app/scripts/probe_retrieval_live.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

QUERIES = [
    ("prescription civile (attendu: Code civil)",
     "délai de prescription des actions personnelles en matière civile"),
    ("prescription point de départ (attendu: Code civil)",
     "point de départ du délai de prescription suspension interruption"),
    ("recours licenciement (attendu: Code du travail)",
     "délai de recours contestation licenciement tribunal du travail"),
    ("recours licenciement bis (attendu: Code du travail)",
     "saisine juridiction compétente litige né d'un licenciement délai"),
    ("divorce (attendu: Code des personnes et de la famille)",
     "procédure de divorce consentement mutuel code des personnes et de la famille"),
]


async def main() -> None:
    from backend.core.context import build_context
    from backend.core.models import SearchKind, SearchTask

    ctx = await build_context()
    for label, query in QUERIES:
        tasks = [
            SearchTask(kind=SearchKind.VECTOR, query=query, top_k=8),
            SearchTask(kind=SearchKind.KEYWORD, query=query, top_k=8),
        ]
        chunks = await ctx.retriever.retrieve(tasks)
        print("=" * 110)
        print(f"{label}\n  Q: {query}\n  -> {len(chunks)} chunks")
        for c in chunks[:6]:
            preview = " ".join(c.content.split())[:160]
            print(
                f"  [{c.document_name[:60]}] art={c.article} "
                f"domains={getattr(c, 'legal_domains', None)} score={max(c.rerank_score, c.retrieval_score):.3f}"
            )
            print(f"      {preview}")


if __name__ == "__main__":
    asyncio.run(main())
