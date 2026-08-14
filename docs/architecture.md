# System Architecture

The system is a multi-agent legal research pipeline built on **LangGraph**.
A single user question flows through 18 nodes — safety checks, query
routing, planning, memory, retrieval, ranking, reasoning, verification and
generation — before an answer is returned. Right after the input guardrail,
a `query_router` node decides whether the question needs retrieval at all:
direct (small-talk / meta) questions short-circuit straight to the response
generator, skipping the whole retrieval pipeline. The guiding constraint:
**an answer may only contain claims that trace to retrieved evidence**.

```mermaid
flowchart TB
    subgraph edge["Edge"]
        nginx[Nginx reverse proxy]
        api[FastAPI app<br/>REST / SSE / WebSocket]
        nginx --> api
    end

    subgraph graph["LangGraph pipeline (backend/workflows/graph.py)"]
        ig[input_guardrail] -->|allowed| qr[query_router]
        ig -->|blocked| ref[refusal]
        qr -->|direct| rg[response_generator]
        qr -->|retrieval| pl[planner]
        pl --> ca[context_agent]
        ca --> ma[memory_agent]
        ma --> fan{{"fan-out: one retrieval_branch per sub-question (parallel Send)"}}
        fan --> rm[retrieval_merge]
        rm --> cr[conflict_resolver]
        cr --> er[evidence_ranking]
        er --> cva[coverage_auditor]
        cva -->|coverage gap<br/>max 1 retry| fan
        cva --> ra[reasoning_agent]
        ra -->|needs evidence<br/>max 1 retry| fan
        ra --> rf[reflection]
        rf -->|retry retrieval<br/>max 1 iteration| fan
        rf --> rg[response_generator]
        rg -->|direct| og
        rg --> clv[claim_verification]
        clv --> cv[citation_verification]
        cv --> og[output_guardrail]
    end

    api --> graph

    subgraph retrieval["Retrieval & memory subsystems"]
        coord[RetrievalCoordinator]
        milvus[(Milvus vector store)]
        bm25[BM25 keyword index]
        web[Web / gov / case-law / news workers]
        memstore[MemoryStore<br/>MemGPT tiers]
        coord --> milvus
        coord --> bm25
        coord --> web
    end

    fan --> coord
    ma --> memstore
    ca --> memstore

    subgraph infra["Infrastructure"]
        redis[(Redis<br/>cache + Celery broker)]
        pg[(PostgreSQL<br/>users, sessions, memory,<br/>documents, audit)]
        temporal[Temporal<br/>durable workflows]
        celery[Celery workers<br/>ingestion & batch jobs]
    end

    memstore --> redis
    memstore --> pg
    api --> temporal
    api --> celery

    subgraph obs["Observability"]
        prom[Prometheus]
        graf[Grafana]
        lf[Langfuse]
    end

    api -.metrics.-> prom
    prom --> graf
    api -.traces.-> lf
```

## Component responsibilities

| Component | Location | Responsibility |
|---|---|---|
| API | `backend/api/` | HTTP/SSE/WebSocket entry points, auth, request validation |
| Workflow graph | `backend/workflows/graph.py` | Compiles the `StateGraph`, wires nodes and conditional edges, provides `run_query` / `stream_query` |
| Agents | `backend/agents/` | One node per concern; each is `async def node(state, ctx) -> dict` |
| Planner | `backend/planner/agent.py` | Turns the question into a `RetrievalPlan` (LLM or deterministic heuristic) |
| Core | `backend/core/` | Shared models (`models.py`), constants, config, `AppContext` wiring, ports, LLM client, cache, embeddings |
| Retrieval | `backend/retrieval/` | Parallel workers, RRF fusion, dedup, rerank |
| Vector store | `backend/vectorstore/` | Milvus adapter with in-memory fallback (`VectorStoreProtocol`) |
| Memory | `backend/memory/` | Buffer, summaries, semantic memory, preferences (`MemoryStoreProtocol`) |
| Guardrails | `backend/guardrails/` | Prompt-injection/jailbreak detection, unsafe-advice checks |
| Ingestion | `backend/ingestion/` | Parse → clean → chunk → embed → index |
| Security | `backend/security/` | JWT, RBAC roles (`admin`, `legal_expert`, `user`, `viewer`), rate limiting |
| Observability | `backend/observability/` | Prometheus metrics, OpenTelemetry, Langfuse |
| Temporal | `backend/temporal/` | Durable conversation workflow, resume after interruption |
| Evaluation | `backend/evaluation/` | Golden dataset, quality metrics, runner |

## Design principles

### Ports and adapters

Agents never import infrastructure SDKs. They depend on the protocols in
`backend/core/ports.py` — `VectorStoreProtocol`, `RetrieverProtocol`,
`MemoryStoreProtocol`, `CacheProtocol`, `EmbeddingProvider` — and
`backend/core/context.py` wires concrete adapters into the `AppContext`
dataclass once per process. Every node receives the same `ctx`.

### Offline-first fallbacks

Every external dependency degrades to a working in-process substitute:

| Dependency | Fallback when unavailable |
|---|---|
| LLM provider | Deterministic `mock` provider + heuristic planners/templates |
| Milvus | In-memory vector store (`LEGAL_AI_MILVUS_ENABLED=false`) |
| Redis | Process-local TTL cache (`InMemoryCache`) |
| PostgreSQL | SQLite via `aiosqlite` (default `LEGAL_AI_DATABASE_URL`) |
| Temporal | Synchronous execution (`LEGAL_AI_TEMPORAL_ENABLED=false`) |

Node functions never raise on LLM or infrastructure failure; they fall back
to deterministic behavior and record the incident in `state["errors"]` and
`state["trace"]`. This is why the API and the test-suite boot with zero
credentials.

### Grounded answer policy

- The response generator writes **only** from numbered evidence excerpts and
  must cite every substantive statement with `[n]`.
- The citation verification agent rejects any citation that cannot be
  resolved to a real retrieved chunk; rejected citations are removed and
  recorded as warnings.
- With insufficient evidence, agents say so explicitly instead of guessing.
- Confidence below `CONFIDENCE_THRESHOLD` (0.55) attaches a warning; below
  `HUMAN_REVIEW_THRESHOLD` (0.40) the answer is escalated for human review.

### Bounded retries

Retry budgets are **1** by default (`max_retrieval_retries`,
`max_reflection_iterations` in `backend/core/config.py`); the planner's
corrective JSON retry is a fixed single retry in
`LLMClient.complete_json`. There is no path that can loop indefinitely.

### Traceability

`GraphState` carries `trace` (human-readable step log), `errors`,
`EvidenceChunk` objects with full source metadata, and `ConflictReport`
records. `ChatResponse` returns the trace to the caller, so every answer can
be audited step by step.
