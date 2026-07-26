# API Reference

Base URL: `http://localhost:8000`. App: `backend.api.main:app`
(`create_app()`). All `/api/v1/*` endpoints require
`Authorization: Bearer <jwt>` (minimum role `viewer` for chat/search;
document ingestion requires higher privileges — see
[security.md](security.md)).

## Auth (`backend/api/routers/auth.py`)

There is no public registration endpoint. Users come from an in-memory dev
store: in `development` an `admin` / `admin123` user is created at boot, and
extra users can be injected via the `LEGAL_AI_DEV_USERS` env var
(`user1:pass1:role,user2:pass2:role`, role ∈ `admin|legal_expert|user|viewer`).

### POST /api/v1/auth/token

```json
{ "username": "admin", "password": "admin123" }
```

→ `200`

```json
{ "access_token": "<jwt>", "token_type": "bearer", "expires_in": 3600, "role": "admin" }
```

Tokens are HS256 JWTs (`sub`, `role`, `exp`), lifetime
`LEGAL_AI_JWT_EXPIRE_MINUTES` (default 60).

### GET /api/v1/auth/me

Returns the validated token payload: `{ "sub": "…", "role": "…", "exp": … }`.

## Chat (`backend/api/routers/chat.py`)

### POST /api/v1/chat

Runs the full agentic workflow for one query (`ChatRequest`):

```json
{
  "query": "Quels sont les droits d'un salarié licencié au Burkina Faso ?",
  "session_id": null,
  "language": "fr",
  "scenario_date": "2026-07-01"
}
```

→ `ChatResponse` (abridged):

```json
{
  "session_id": "a1b2c3…",
  "answer": {
    "answer": "Selon le Code du travail … [1]",
    "citations": [{ "label": "Code du travail, art. 39", "verified": true }],
    "confidence": 0.82,
    "language": "fr",
    "warnings": [],
    "conflicts": [],
    "requires_human_review": false,
    "refused": false
  },
  "trace": ["input_guardrail: allowed", "planner: 3 search tasks (heuristic planner)", "…"],
  "latency_ms": 1342.5
}
```

### GET /api/v1/chat/stream (SSE)

Query parameters: `query` (required), `session_id`, `language`. Emits one
`data:` frame per pipeline node, then a final frame with the whole
`ChatResponse`:

```
data: {"type": "update", "node": "planner", "update": {…}}

data: {"type": "update", "node": "retrieval_coordinator", "update": {…}}

data: {"type": "final", "response": {"session_id": "…", "answer": {…}, "latency_ms": …}}
```

Failures surface as `{"type": "error", "detail": "…"}` frames. Responses
carry `Cache-Control: no-cache` and `X-Accel-Buffering: no`.

### WS /api/v1/ws/chat

WebSocket equivalent: send a `ChatRequest` JSON, receive the same per-node
update objects, then the final `ChatResponse`.

## Documents (`backend/api/routers/documents.py`)

### POST /api/v1/documents

Multipart upload (PDF, DOCX, HTML, MD, …) — ingests and indexes the file.
→ `DocumentIngestResult`:

```json
{ "document_id": "…", "document_name": "code-du-travail.pdf",
  "chunks_created": 412, "version": 2, "status": "indexed" }
```

`status` is `indexed`, `failed` or `skipped_duplicate` (content hash match).

### GET /api/v1/documents/{document_id}

Ingestion status/metadata for one document.

## Search (`backend/api/routers/search.py`)

### GET /api/v1/search?q=…&top_k=8

Runs one vector + one keyword task through the retrieval coordinator and
returns raw evidence:

```json
{ "query": "licenciement abusif", "count": 12,
  "results": [ { "document_name": "Code du travail", "article": "39", "authority": "law", "…": "…" } ] }
```

`top_k` ∈ 1–50. `503` if the retriever is unavailable.

## Health & metrics (`backend/api/main.py`)

| Endpoint | Purpose |
|---|---|
| `GET /health` | Liveness — `{ "status": "ok" }`, no auth |
| `GET /ready` | Readiness — per-backend status (live vs. fallback) |
| `GET /metrics` | Prometheus exposition (see [monitoring.md](monitoring.md)) |

## Errors

Errors return `{ "detail": "…" }` with the appropriate status (`401/403`
auth, `422` validation, `429` rate limit, `5xx` internal). Guardrail
refusals are **200** chat responses with `answer.refused = true` and a
`refusal_reason` — not errors.
