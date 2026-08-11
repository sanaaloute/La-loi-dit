"""Benchmark embedding models on real corpus chunks.

For each test query, relevance is proxied by domain+keyword:
- licenciement query -> Code du travail chunks containing "licenci"
- SARL query -> AUSCGIE chunks containing "SARL" or "responsabilité limitée"

Reports per model: top-10 document composition, relevant hits in top-10,
and mean cosine separation (relevant vs irrelevant).
"""

import backend

import asyncio
import json

import litellm
from pymilvus import MilvusClient

from backend.core.config import get_settings

QUERIES = {
    "droits du salarié licencié : préavis, indemnité de licenciement, recours": (
        lambda name, text: "Code du travail" in name and "licenci" in text.lower()
    ),
    "création SARL OHADA capital associés": (
        lambda name, text: "sociétés commerciales" in name
        and ("sarl" in text.lower() or "responsabilité limitée" in text.lower())
    ),
}

MODELS = {
    "nemotron(openrouter)": ("openai/nvidia/nemotron-3-embed-1b:free", "https://openrouter.ai/api/v1"),
    "nomic(local)": ("ollama/nomic-embed-text", "http://localhost:11434"),
    "bge-m3(local)": ("ollama/bge-m3", "http://localhost:11434"),
    "qwen3-emb-8b(local)": ("ollama/dengcao/Qwen3-Embedding-8B:Q4_K_M", "http://localhost:11434"),
}


def cos(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    return dot / (na * nb) if na and nb else 0.0


async def embed(model: str, base: str, key: str, texts: list[str]) -> list[list[float]]:
    kwargs = {}
    if "openrouter" in base:
        kwargs = {"api_key": key, "encoding_format": "float"}
    resp = await litellm.aembedding(model=model, input=texts, api_base=base, **kwargs)
    return [list(d["embedding"]) for d in resp.data]


async def main() -> None:
    s = get_settings()
    key = s.embedding_api_key or s.openrouter_api_key
    c = MilvusClient(uri="http://localhost:19530", timeout=10)
    c.load_collection("legal_chunks")
    rows = c.query(
        collection_name="legal_chunks", filter="",
        output_fields=["chunk_json"], limit=4000,
    )
    parents = []
    for r in rows:
        j = json.loads(r["chunk_json"])
        if j.get("metadata", {}).get("role") == "parent":
            parents.append((j.get("document_name", "?"), j.get("content", "")))
    # cap per document to keep the benchmark fast but representative
    by_doc: dict[str, list[str]] = {}
    for name, text in parents:
        by_doc.setdefault(name, []).append(text)
    samples = [(n, t) for n, texts in by_doc.items() for t in texts[:12]]

    out: list[str] = [f"sample: {len(samples)} parent chunks from {len(by_doc)} documents"]
    for label, (model, base) in MODELS.items():
        try:
            await embed(model, base, key, ["test"])
        except Exception as exc:
            out.append(f"\n=== {label}: UNAVAILABLE ({str(exc)[:120]})")
            continue
        for query, is_rel in QUERIES.items():
            vecs = await embed(model, base, key, [query, *[t[:900] for _, t in samples]])
            qv, cvs = vecs[0], vecs[1:]
            scored = sorted(
                zip([n for n, _ in samples], [t for _, t in samples],
                    (cos(qv, cv) for cv in cvs)),
                key=lambda t: -t[2],
            )
            rel_flags = [is_rel(n, t) for n, t, _ in scored]
            top10 = rel_flags[:10]
            rel_scores = [sc for (_, _, sc), rel in zip(scored, rel_flags) if rel]
            irrel_scores = [sc for (_, _, sc), rel in zip(scored, rel_flags) if not rel]
            sep = (sum(rel_scores) / len(rel_scores) if rel_scores else 0) - (
                sum(irrel_scores) / len(irrel_scores) if irrel_scores else 0
            )
            out.append(f"\n=== {label} | {query[:45]}")
            out.append(
                f"  relevant in top-10: {sum(top10)}/10 | separation: {sep:+.4f}"
            )
            for n, t, sc in scored[:6]:
                mark = "R" if is_rel(n, t) else " "
                out.append(f"  [{mark}] {sc:.4f}  {n[:58]}")

    with open("embed_benchmark2.txt", "w", encoding="utf-8") as fh:
        fh.write("\n".join(out))


asyncio.run(main())
