# Database Schema

PostgreSQL in production (`LEGAL_AI_DATABASE_URL`), SQLite via `aiosqlite`
for development and tests.

**Implementation status.** The SQLAlchemy tables currently defined in code
are `messages`, `memories` and `preferences`, built in
`backend/memory/store.py` (`MemoryStore._build_schema`, created on first
use; if the database is unreachable the store falls back to a local SQLite
file and, failing that, to in-memory only). The remaining tables below are
the **designed schema** for the API/security/evaluation/Temporal layers
(users are currently an in-memory dev store in
`backend/api/routers/auth.py`); they are documented here as the target
model those layers persist to.

```mermaid
erDiagram
    users ||--o{ sessions : owns
    users ||--o{ memories : has
    users ||--o{ preferences : has
    users ||--o{ audit_logs : triggers
    sessions ||--o{ messages : contains
    sessions ||--o| workflow_state : "durable run"
    documents ||--o{ document_versions : versions
    documents ||--o{ memories : "ingested as (optional)"
    evaluations }o--|| sessions : "evaluates (optional)"

    users {
        uuid id PK
        string email UK
        string password_hash
        string role "admin|legal_expert|user|viewer"
        boolean is_active
        timestamp created_at
    }

    sessions {
        string id PK "session_id"
        uuid user_id FK
        string title
        timestamp created_at
        timestamp last_active
    }

    messages {
        uuid id PK
        string session_id FK
        string role "user|assistant|system"
        text content
        json metadata
        timestamp created_at
    }

    memories {
        string id PK
        uuid user_id FK
        string session_id
        string kind "buffer|summary|semantic|preference"
        text content
        vector embedding
        float importance
        timestamp created_at
        timestamp last_accessed
        json metadata
    }

    preferences {
        uuid id PK
        uuid user_id FK
        string key
        json value
        timestamp updated_at
    }

    documents {
        string id PK "document_id"
        string name
        string source_url
        string authority "AuthorityLevel"
        string government_body
        date publication_date
        date effective_date
        int current_version
        string status "indexed|failed|skipped_duplicate"
        timestamp created_at
    }

    document_versions {
        uuid id PK
        string document_id FK
        int version
        string content_hash
        int chunks_created
        timestamp ingested_at
    }

    evaluations {
        uuid id PK
        string case_id
        string session_id FK
        float groundedness
        float faithfulness
        float citation_accuracy
        float answer_relevance
        boolean hallucination_detected
        float latency_ms
        boolean passed
        text detail
        timestamp run_at
    }

    audit_logs {
        uuid id PK
        uuid user_id FK
        string action
        string resource
        json detail
        string ip
        timestamp created_at
    }

    workflow_state {
        string session_id PK
        string temporal_workflow_id
        string status "running|awaiting_input|completed|failed"
        json state
        timestamp updated_at
    }
```

## Notes

- `memories.kind` mirrors the MemGPT tiers described in
  [memory.md](memory.md); `embedding` lives in Milvus for semantic recall,
  with the row in Postgres as the system of record.
- `documents` / `document_versions` back the ingestion pipeline and legal
  timeline reasoning: `effective_date` per version lets the conflict
  resolver pick the version in force at a scenario date. Re-ingesting a
  changed document bumps `current_version` and records a new
  `document_versions` row (deduped by `content_hash` →
  `skipped_duplicate`).
- `evaluations` stores one row per golden-dataset case run
  (`EvalCaseResult` in `backend/core/models.py`).
- `audit_logs` records security-relevant events (logins, blocked queries,
  ingestion, role changes) — see [security.md](security.md).
- `workflow_state` lets the API reconnect a `session_id` to its Temporal
  workflow after a restart (see [temporal.md](temporal.md)).
