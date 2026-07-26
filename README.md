# Burkina Faso Legal AI

A production-grade **agentic AI legal research assistant** for Burkina Faso law
and regulations (national law, OHADA law, case law, official gazette). It
answers legal questions in French (and other languages) with verified,
traceable citations to real legal sources.

Design priorities, in order: **accuracy, reliability, explainability,
traceability, low hallucination**. The system refuses to guess: every
substantive statement must trace to a retrieved evidence chunk, fabricated
citations are rejected automatically, and low-confidence answers are flagged
or escalated for human review.

> **Legal disclaimer.** This system is a legal *research* aid. Its answers are
> grounded in the cited sources but do **not** constitute legal advice. Always
> consult a licensed legal professional for your specific situation.

## Features

| Capability | How it works |
|---|---|
| Multi-agent reasoning pipeline | 13-node LangGraph workflow: guardrail → planner → context → memory → retrieval → conflict resolution → evidence ranking → reasoning → reflection → citation verification → response → output guardrail |
| Grounded answers | Responses are composed strictly from retrieved evidence; every claim carries a numeric citation verified against the evidence list |
| Hybrid retrieval | Vector (Milvus) + keyword (BM25) + government/regulation/case-law/news workers, fused with RRF and authority-weighted ranking |
| Legal conflict resolution | Official source > newer amendment > timeline-in-force; unresolvable conflicts are surfaced, never silently resolved |
| Authority-weighted ranking | Constitution > OHADA treaty > amended law > law > decree > … > blog (see `backend/core/constants.py`) |
| Anti-hallucination controls | Citation verification, reflection self-critique, bounded retries (max 1), grounded-answer policy, refusal path |
| Memory | MemGPT-style tiers: short-term buffer, summaries, long-term semantic memory, user preferences |
| Durable conversations | Temporal workflows survive restarts; memory buffer is persisted |
| Security | JWT + RBAC, input/output guardrails, rate limiting, sandboxed tools, audit logging |
| Observability | Prometheus metrics, Grafana dashboards, Langfuse traces, OpenTelemetry |
| Offline-first | Every dependency (LLM, Milvus, Redis, Temporal) has an in-process fallback; the whole pipeline runs with zero credentials |

## Architecture

```mermaid
flowchart LR
    client[Client] --> nginx[Nginx]
    nginx --> api[FastAPI API]

    subgraph pipeline["LangGraph multi-agent pipeline"]
        guard[Input guardrail] --> plan[Planner]
        plan --> ctx[Context agent]
        ctx --> mem[Memory agent]
        mem --> ret[Retrieval coordinator]
        ret --> conf[Conflict resolver]
        conf --> rank[Evidence ranking]
        rank --> reas[Reasoning agent]
        reas --> refl[Reflection agent]
        refl --> cit[Citation verification]
        cit --> resp[Response generator]
        resp --> og[Output guardrail]
    end

    api --> pipeline
    ret --> milvus[(Milvus)]
    ret --> bm25[BM25 / web workers]
    mem --> redis[(Redis)]
    mem --> pg[(PostgreSQL)]
    api --> temporal[Temporal]
    api --> prom[Prometheus]
    prom --> grafana[Grafana]
    api --> langfuse[Langfuse]
```

## Quickstart

### 1. Local install

```bash
python -m venv .venv
source .venv/bin/activate   # Linux/macOS
# .venv\Scripts\activate   # Windows

pip install -r requirements.txt
pip install -e .
```

### 2. Run offline (no credentials needed)

The default configuration uses the deterministic `mock` LLM provider, an
in-memory vector store, an in-process cache and SQLite — nothing external is
required.

Copy the development template and start the server:

```bash
cp .env.dev.example .env.dev
uvicorn backend.api.main:app --host 0.0.0.0 --port 8000
```

`.env.dev` is loaded after `.env` and overrides production defaults while you
are developing locally. It is gitignored and should never be committed.

### 3. Full stack with Docker Compose on Ubuntu

This project is configured for a split Ollama setup:

- **LLM**: [Ollama Cloud](https://ollama.com/cloud) — uses an Ollama API key.
- **Embeddings**: local Ollama running on the Ubuntu Docker host.

Copy the production template and fill in your secrets:

```bash
cp .env.example .env
```

Edit `.env`:

```ini
# Ollama Cloud LLM
LEGAL_AI_LLM_PROVIDER=ollama
LEGAL_AI_LLM_MODEL=gpt-oss:120b
LEGAL_AI_LLM_API_BASE=https://ollama.com
LEGAL_AI_LLM_API_KEY=<your-ollama-cloud-api-key>

# Local Ollama embeddings (Docker host)
LEGAL_AI_EMBEDDING_MODEL=ollama/nomic-embed-text
LEGAL_AI_EMBEDDING_DIMENSION=768

# Secrets — change every placeholder
LEGAL_AI_SECRET_KEY=<random-secret>
POSTGRES_PASSWORD=<strong-password>
MINIO_ROOT_PASSWORD=<strong-password>
GRAFANA_ADMIN_PASSWORD=<strong-password>
LANGFUSE_NEXTAUTH_SECRET=<random-secret>
LANGFUSE_SALT=<random-secret>
```

On the Docker host, pull the embedding model and bind Ollama to all interfaces
so the containers can reach it:

```bash
ollama pull nomic-embed-text
OLLAMA_HOST=0.0.0.0:11434 ollama serve
```

> The `api` and `celery-worker` services reach the host via
> `host.docker.internal:11434` with `extra_hosts: ["host.docker.internal:host-gateway"]`
> for Linux.

Start the core stack:

```bash
docker compose up -d --build
```

Start Langfuse (optional, on port 3002 to avoid conflict with the frontend):

```bash
docker compose --profile observability up -d
```

This starts the API, Celery worker, Nginx, PostgreSQL, Redis, Milvus,
Temporal (+ UI on :8080), Prometheus (:9090), Grafana (:3001) and
Langfuse (:3002).

### 4. Example request

Chat endpoints require a JWT. In `development` a dev user
`admin` / `admin123` exists (see [docs/api.md](docs/api.md)):

```bash
# 1. get a token
TOKEN=$(curl -s -X POST http://localhost:8000/api/v1/auth/token \
  -H "Content-Type: application/json" \
  -d '{"username": "admin", "password": "admin123"}' | python -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

# 2. ask a question
curl -X POST http://localhost:8000/api/v1/chat \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"query": "Quels sont les droits d\u0027un salarie licencie au Burkina Faso ?"}'
```

Response (abridged):

```json
{
  "session_id": "…",
  "answer": {
    "answer": "Selon le Code du travail … [1]",
    "citations": [{ "label": "Code du travail, art. 39", "verified": true }],
    "confidence": 0.82,
    "requires_human_review": false,
    "warnings": []
  },
  "trace": ["input_guardrail: allowed", "planner: 3 search tasks", "…"],
  "latency_ms": 1342.5,
  "trace_id": "<langfuse-trace-id>"
}
```

## Frontend

A Next.js 15 (App Router, TypeScript, Tailwind CSS) web UI lives in
[`frontend/`](frontend/). It provides the conversation interface with live
SSE streaming of the agent pipeline, an agent execution timeline, a citation
panel, an evidence viewer with full source metadata, and an optional JWT login
(anonymous calls work in development).

```bash
cd frontend
npm install
npm run dev          # http://localhost:3000
```

The API base URL is configurable via `NEXT_PUBLIC_API_URL`. The default behavior
proxies `/backend-api/*` server-side to `http://localhost:8000`, which avoids
CORS issues because the API does not enable CORS. Set
`NEXT_PUBLIC_API_URL=http://localhost:8000` in `frontend/.env.local` to call
the API directly when CORS is handled upstream (e.g., via Nginx).

An optional Docker service is also available:

```bash
docker compose --profile frontend up -d --build frontend
```

> Note: the `frontend` service publishes host port **3000**, which Langfuse
> also uses — adjust one of the port mappings if you run both.

## Langfuse tracing

The project integrates Langfuse via LiteLLM callbacks and a per-request
`traced_chat_run()` helper. It captures every LLM generation (model, tokens,
cost), the full LangGraph node tree, user/session IDs, feature tags, and the
sanitised final answer. User feedback is recorded as a Langfuse score.

The [Langfuse AI skill](https://github.com/langfuse/skills) is installed under
`.kimi-code/skills/langfuse/` and is used to keep the instrumentation aligned
with Langfuse best practices (descriptive trace names, environment attribute,
nested spans, meaningful input/output, and feedback scores).

## Repository layout

```
.
├── backend/
│   ├── agents/            # LangGraph node implementations (13 nodes)
│   ├── planner/           # planner agent (LLM + heuristic fallback)
│   ├── workflows/         # LangGraph graph construction & runners
│   ├── core/              # models, constants, config, context, ports, llm, cache
│   ├── retrieval/         # hybrid retrieval coordinator & workers
│   ├── vectorstore/       # Milvus adapter + in-memory fallback
│   ├── ingestion/         # document ingestion pipeline
│   ├── memory/            # MemGPT-style memory store
│   ├── guardrails/        # input/output safety checks
│   ├── sandbox/           # sandboxed tool execution
│   ├── tools/             # agent tools
│   ├── api/               # FastAPI application (REST / SSE / WebSocket)
│   ├── security/          # JWT, RBAC, rate limiting
│   ├── observability/     # Prometheus metrics, OTel, Langfuse
│   ├── evaluation/        # metrics, golden dataset, runner
│   └── temporal/          # durable conversation workflows
├── docs/                  # documentation (see below)
├── frontend/              # Next.js web UI (chat, agent timeline, citations, evidence)
├── docker/                # Dockerfile, Nginx, Prometheus, Grafana
├── .github/workflows/     # CI
├── docker-compose.yml     # full stack
├── requirements.txt
└── pyproject.toml
```

## Documentation

- [Architecture](docs/architecture.md) — system design and component responsibilities
- [Agents](docs/agents.md) — the 13 pipeline nodes in detail
- [Workflow](docs/workflow.md) — LangGraph graph, conditional edges, retry budgets
- [Retrieval](docs/retrieval.md) — hybrid RAG pipeline and chunk metadata
- [Memory](docs/memory.md) — MemGPT-style memory tiers
- [Temporal](docs/temporal.md) — durable conversations and resume semantics
- [Database](docs/database.md) — schema (ER diagram)
- [API reference](docs/api.md) — endpoints with examples
- [Deployment](docs/deployment.md) — compose topology and production checklist
- [Operations](docs/operations.md) — runbook and failure modes
- [Monitoring](docs/monitoring.md) — metrics, dashboards, alerts
- [Security](docs/security.md) — auth, guardrails, threat model
- [Testing](docs/testing.md) — running and writing tests
- [Developer guide](docs/developer.md) — extending agents, domains, retrievers
- [Administrator guide](docs/administrator.md) — users, ingestion, evaluations
- [Evaluation](docs/evaluation.md) — quality metrics and the evaluation runner

## Configuration

Everything is configured via environment variables prefixed `LEGAL_AI_` (see
`.env.example` for production/Docker and `.env.dev.example` for local
development, plus [deployment docs](docs/deployment.md)).

| File | Purpose | Gitignored? |
|---|---|---|
| `.env` | Production values used by Docker Compose | yes |
| `.env.dev` | Local development overrides (mock LLM, SQLite, localhost services) | yes |
| `.env.example` | Production/Docker template | no |
| `.env.dev.example` | Local development template | no |

`backend/core/config.py` loads `.env` first, then `.env.dev`, so local
settings override production defaults when both files are present. Keep
`.env.dev` empty/absent on production machines.

## Development

Run the test suite:

```bash
pytest -q
```

Run the frontend type-check and build:

```bash
cd frontend
npm run build
```
