"""One-shot end-to-end check with the REAL .env config and Milvus DB.

Runs one question through the full graph and prints which path the response
generator took (LLM synthesis vs template fallback) plus the trace/errors.
Run:  .venv/Scripts/python.exe verify_e2e_tmp.py
"""

import asyncio
import sys

from backend.core.context import build_context
from backend.workflows.graph import build_graph, initial_state

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

QUERY = "Quels sont les droits d'un salarié licencié au Burkina Faso ?"


async def main() -> None:
    ctx = await build_context()
    graph = build_graph(ctx)
    final = await graph.ainvoke(initial_state(QUERY))
    answer = final["final_answer"]
    print("=" * 100)
    print(answer.answer[:1800])
    print("=" * 100)
    print("confidence:", answer.confidence)
    print("citations:", [(c.label, c.document_name, c.article) for c in answer.citations][:5])
    print("--- errors ---")
    for line in final.get("errors", []):
        print(" ", line)
    print("--- trace ---")
    for line in final.get("trace", []):
        print(" ", line)


if __name__ == "__main__":
    asyncio.run(main())
