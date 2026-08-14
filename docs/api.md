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

**Trace gating (spec §48)**: the internal chain-of-thought is exposed to
ADMIN tokens only. Non-admin roles receive `"trace": []` here, trace-free
`update` frames on SSE, and a trace-free final object on the WebSocket. The
shared answer cache never stores the trace, so a cached replay cannot leak it
either.

### GET /api/v1/chat/stream (SSE)

Query parameters: `query` (required), `session_id`, `language`, `model`.
Emits one `data:` frame per pipeline node (including one per parallel
`retrieval_branch`), then a final frame with the whole `ChatResponse`:

```
data: {"type": "node_start", "node": "planner"}

data: {"type": "update", "node": "planner", "update": {…}}

data: {"type": "update", "node": "retrieval_branch", "update": {…}}

data: {"type": "final", "response": {"session_id": "…", "answer": {…}, "latency_ms": …}}
```

`node_start` fires when a node BEGINS (real-time "running" indicator);
`update` fires when it completes. Idle periods are kept alive with `: hb`
heartbeat comment frames (every `LEGAL_AI_CHAT_HEARTBEAT_SECONDS`, 10s by
default), and a run is hard-capped at `LEGAL_AI_CHAT_RUN_TIMEOUT_SECONDS`
(280s, below nginx's 300s `proxy_read_timeout`) — on timeout the pump task is
cancelled and an error frame is emitted instead of hanging forever.

Failures surface as `{"type": "error", "detail": "…"}` frames; a user stop
surfaces as `{"type": "cancelled"}`. Responses carry `Cache-Control: no-cache`
and `X-Accel-Buffering: no`.

### POST /api/v1/chat/cancel

Stops an in-flight run: body `{"session_id": "…"}`. Cancelling raises
`CancelledError` inside the LangGraph execution, so the workflow (including
parallel retrieval branches and in-flight LLM calls) really stops in the
backend. Returns `{"cancelled": true|false}` (`false` when no run is active
for that session). Aborted runs persist nothing and are not metered.

### WS /api/v1/ws/chat

WebSocket equivalent: send a `ChatRequest` JSON, receive the same per-node
update objects, then the final `ChatResponse`.

### POST /api/v1/chat/transcribe

Transcribes a voice message to text (voice input for the chat composer — the
chat flow itself is unchanged: the returned text is reviewed by the user,
then sent through the normal chat endpoints). Auth like the other chat
endpoints. Multipart upload, field `file`:

```
POST /api/v1/chat/transcribe
Content-Type: multipart/form-data
file: <audio blob>  (webm, ogg, mp3, wav, m4a, mp4 — extension or content-type)
```

→ `{ "text": "bonjour le droit" }`. A successful transcription is metered as
one request (0 tokens) on the caller's daily usage.

Errors: `400` unsupported format or empty file, `413` audio larger than
`LEGAL_AI_STT_MAX_AUDIO_BYTES` (25 MB default), `503` STT not available on
this server, `502` transcription provider failure.

Configuration (`backend/core/config.py`):

| Setting | Default | Purpose |
|---|---|---|
| `LEGAL_AI_STT_PROVIDER` | `litellm` | `litellm` (LiteLLM gateway, reuses the LLM credentials) or `faster-whisper` (fully local) |
| `LEGAL_AI_STT_MODEL` | `whisper-1` | LiteLLM transcription model |
| `LEGAL_AI_STT_API_KEY` | _(empty)_ | Dedicated transcription API key; falls back to `LEGAL_AI_LLM_API_KEY` |
| `LEGAL_AI_STT_API_BASE` | _(empty)_ | Dedicated transcription endpoint; falls back to `LEGAL_AI_LLM_API_BASE` |
| `LEGAL_AI_STT_LANGUAGE` | `fr` | Transcription language hint |
| `LEGAL_AI_STT_TIMEOUT_SECONDS` | `90` | Provider call timeout (a misconfigured endpoint fails instead of hanging) |
| `LEGAL_AI_FASTER_WHISPER_MODEL_SIZE` | `small` | Local Whisper model size |
| `LEGAL_AI_STT_MODELS_DIR` | `data/stt_models` | Local model download cache |
| `LEGAL_AI_STT_MAX_AUDIO_BYTES` | `26214400` | Upload size cap (HTTP 413) |

The `faster-whisper` package is import-guarded: without it, the local
provider reports unavailable (503) and the rest of the platform is
unaffected.

Note: an Ollama chat setup cannot serve Whisper transcriptions. Use
`LEGAL_AI_STT_PROVIDER=faster-whisper` (local) or point
`LEGAL_AI_STT_API_BASE`/`LEGAL_AI_STT_API_KEY` at a transcription-capable
endpoint (OpenAI `whisper-1`, Groq `groq/whisper-large-v3`, …). Voice notes
are capped at 30 s in the UI; transcription is batch (a few seconds), not
realtime streaming.

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

Version/status info for one document, combining the version store
(`versions.json` — version, content hash, article count) with the document's
chunks in the vector store (name, chunk count); the latest persisted ingestion
record (`ingestion_results.json`) is included under `ingestion` when present.
`404` when the document is unknown to both stores.

```json
{ "document_id": "…", "document_name": "code-du-travail.pdf", "version": 2,
  "content_hash": "…", "article_count": 412, "chunk_count": 830,
  "ingestion": { "status": "indexed", "timestamp": "…" } }
```

## Citations (`backend/api/routers/citations.py`)

### GET /api/v1/citations/{chunk_id}

Resolves a citation/chunk id to its evidence record — a direct primary-key
lookup in the vector store (`get_by_ids`), no similarity search, no LLM.
Returns a `CitationRecord` (metadata subset of `EvidenceChunk`: document
name/id, article, section, dates, url, content…). `404` when the chunk id is
unknown, `503` when the vector store is unavailable.

## Sources (`backend/api/routers/sources.py`)

### GET /api/v1/sources/{document_id}

Document-level source record (`SourceRecord`): version, content hash and
article count from the version store, plus document metadata (name, authority,
`document_type`, `law_number`, status, publication/effective dates, url,
language) taken from the document's chunks. `chunk_count` is always included.
A document is "known" when the version store tracks it or the vector store
still holds one of its chunks — otherwise `404`.

## Admin (`backend/api/routers/admin.py`)

All endpoints require the ADMIN role, are read-only and work offline.

### GET /api/v1/admin/audit-log?limit=100

Most recent entries of the in-memory audit ring buffer (newest first), filled
by the audit middleware. `limit` ∈ 1–1000. Response: `{entries, count, cap}`
where `cap` is the buffer capacity.

### GET /api/v1/admin/ingestion/status

Per-document ingestion state: `documents` (version, content hash, article
count per document from `versions.json`), `total_documents`,
`store_updated_at`, and `failed_documents` — the real failure list read from
`ingestion_results.json` (latest persisted record per document, including the
error detail).

### GET /api/v1/admin/evaluation/latest

The latest offline evaluation report (`data/eval/eval_report.json`) with
`generated_at`, `dataset`, `total_cases`, `pass_rate` and the full report
under `report`. `404` when no report exists, `500` when it is corrupt.

### GET /api/v1/admin/retrieval/analytics

Request analytics aggregated from the in-memory audit log: `total_requests`,
per-path `requests` / `errors` (status ≥ 500) / `avg_latency_ms`, and per-user
request counts. Single-process and in-memory only — Prometheus `/metrics`
stays the cross-process source of truth.

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
