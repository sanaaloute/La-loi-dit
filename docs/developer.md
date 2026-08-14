# Developer Guide

## Setup

```bash
py -m venv .venv && .venv\Scripts\activate
pip install -r requirements.txt
pip install -e .
uvicorn backend.api.main:app --reload
pytest
```

Everything runs offline by default (`mock` LLM, SQLite, in-memory stores).

## Code layout

- `backend/core/` — models, constants, settings, `AppContext`, ports
  (protocols), LLM client, cache, embeddings, `GraphState`
- `backend/planner/` — planner agent + heuristic fallback
- `backend/agents/` — one file per LangGraph node
- `backend/workflows/` — `graph.py` (graph construction, runners)
- `backend/retrieval/`, `vectorstore/`, `memory/`, `guardrails/`,
  `ingestion/`, `tools/`, `sandbox/` — subsystem adapters behind the ports
- `backend/api/`, `security/`, `observability/`, `evaluation/` — service layers

## Coding conventions

- **Pydantic v2** for every piece of data crossing a boundary (state,
  evidence, API payloads). LangGraph state is a `TypedDict(total=False)`;
  nodes return only updated keys.
- **Offline-first**: every node must work with the `mock` provider and no
  external services. LLM/infra failures are caught, recorded in
  `errors`/`trace`, and answered by a deterministic fallback — nodes never
  raise.
- **Lazy heavy imports**: import subsystem modules (`backend.memory`,
  `backend.vectorstore`, `backend.retrieval`, `backend.guardrails`) inside
  functions, not at module top, so `backend.agents` and `backend.workflows`
  stay importable in minimal environments.
- **Retry budgets are sacred**: max 1 everywhere (`backend/core/constants.py`).
- **Grounded answers**: never let a node fabricate legal content; template
  fallbacks quote evidence verbatim.

## Add a new agent node

1. Create `backend/agents/my_node.py` with
   `async def my_node(state: GraphState, ctx: AppContext) -> dict[str, Any]`
   and export it in `backend/agents/__init__.py`.
2. Add the state keys it produces to `GraphState`
   (`backend/core/state.py`) and any new models to
   `backend/core/models.py`.
3. Register it in `build_graph` (`backend/workflows/graph.py`):
   `g.add_node("my_node", await bind(my_node.my_node))`, rewire the edges,
   and add a routing function if it needs a conditional edge.
4. Append a trace line (`trace_step`) and record errors instead of raising.
5. Add tests in `tests/`.

## Add a legal domain

Domains are data-driven: `data/legal_domains.json` maps each slug to a
French label and keywords, loaded by `load_domain_keywords()` /
`load_domain_labels()` in `backend/ingestion/classification.py` and merged
into `LEGAL_DOMAINS` (`backend/core/constants.py`).

1. Preferred: add it at runtime — admin UI (Documents tab) or
   `POST /api/v1/admin/domains` with `{slug, label, keywords}`.
2. Code/config path: add an entry to `data/legal_domains.json`
   (`{"label": "...", "keywords": [...]}`, French + English keywords).
3. If it needs a dedicated source, add a `SearchKind` in
   `backend/core/models.py` and a worker (below).

## Add a retrieval worker

1. Pick/define a `SearchKind` for it.
2. Implement the worker in `backend/retrieval/` returning
   `list[EvidenceChunk]` with complete metadata (authority, dates, source).
3. Register it in `RetrievalCoordinator` so tasks of that kind are
  dispatched, and make its failure return `[]` (never raise).
4. Set a realistic `AuthorityLevel` — it feeds `AUTHORITY_WEIGHTS` and the
   final ranking formula.

## Configuration

New settings go in `Settings` (`backend/core/config.py`) with the
`LEGAL_AI_` prefix and an offline-safe default; document them in
`.env.example` and [deployment.md](deployment.md).
