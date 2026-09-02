# Migration Guide: Pre-Upgrade → Agentic Legal RAG Platform

What changed when the baseline system (see
[RAG_ARCHITECTURE_AUDIT.md](RAG_ARCHITECTURE_AUDIT.md) for the pre-upgrade
snapshot) was upgraded per the "Production-Grade Agentic Legal RAG Platform"
spec, and what operators must do. Detailed documentation of each area:
[RAG_ARCHITECTURE.md](RAG_ARCHITECTURE.md), [LEGAL_RETRIEVAL.md](LEGAL_RETRIEVAL.md),
[DOCUMENT_PROCESSING.md](DOCUMENT_PROCESSING.md), [CITATION_SYSTEM.md](CITATION_SYSTEM.md),
[EVALUATION.md](EVALUATION.md).

## What changed, per area

- **Planner** — deterministic question-type classification (`QuestionType`,
  French-first heuristics) and temporal-intent detection (`current` /
  `historical` / `any`) now seed every plan; a curated deterministic
  decomposition taxonomy (`backend/planner/decomposition.py`) breaks broad
  rights/obligations/procedure questions into their underlying legal issues
  even when the LLM is unavailable. Broad questions are always searched per
  issue (one keyword task per sub-question).
- **Retrieval** — the coordinator applies a hard temporal filter to fused
  candidates when the plan has a temporal intent, and a discriminative-token
  relevance floor after reranking. Vector workers default to child-chunk
  retrieval (with an unfiltered fallback for legacy indexes).
- **Metadata model** — `EvidenceChunk` gained `document_type`, `law_number`,
  `jurisdiction`, `status`, `valid_from`/`valid_until`, `hierarchy`,
  `issuing_authority` and `embedding_model` (all optional/backward-compatible
  at deserialization; old chunks read as `status="active"`, no dates).
- **Pipeline nodes** — added `parent_expansion`, `coverage_auditor` and
  `claim_verification`; `citation_verification` moved **after**
  `response_generator` so it verifies the actual draft. Bounded re-retrieval
  loops (coverage gap, `INSUFFICIENT:` reasoning, reflection) each allow one
  retry by default.
- **Guardrails** — retrieved-document injection screening before prompting
  (chunks sanitized, evidence wrapped in DATA delimiters), unverified-article
  soft check, context-sensitive disclaimers (full legal vs short note).
- **Answer & confidence** — LLM-only synthesis (template fallback removed),
  sectioned format for complex question types, multi-dimensional
  `ConfidenceBreakdown` alongside the single aggregate confidence, per-statement
  `Claim` support levels on the `FinalAnswer`.
- **Evaluation** — golden dataset grew 15 → 25 cases including the §38
  dismissal-rights regression case with `expected_issues`/`expected_articles`;
  new rank-aware metrics (recall@k, precision@k, MRR, nDCG@k) and an
  issue-coverage pass gate.
- **Legal knowledge graph** — new relational store (`backend/knowledge/`,
  spec §19/§34): `documents`, `legal_articles` and `legal_relationships`
  tables populated at ingestion (regex extraction of `contains` / `amends` /
  `repeals` / `references` / `issued_by` / `applies_to` edges) and queried by a
  graph retrieval worker (explicit article/law lookup + post-rerank neighbour
  expansion). Enabled by default (`legal_graph_enabled`), always best-effort.
- **Terminology expansion** — a 56-entry legal lexicon
  (`backend/planner/terminology.py`) adds recall-oriented keyword tasks
  (synonyms + related terms only) in the heuristic planner, and an
  `expand_legal_terms` tool for the LLM planner.
- **Reranker provider** — reranking is now behind a `RerankerProvider` port
  with an optional API cross-encoder (`reranker_provider=api`); the default
  stays the offline heuristic reranker.
- **Task-based model tiering** — optional per-role model overrides
  (`model_role_routing_enabled`, default off) let cheap models serve
  classification/planner nodes while synthesis keeps the strongest model.
- **API** — new endpoints (below); trace gating changed (see behavioral
  changes).

## Required action: Milvus schema migration and reindex

The Milvus collection gained native scalar columns `article`, `status` and
`document_type` (joining `document_id`). On connect, the store self-heals: a
collection missing any required field is **dropped and recreated** (a warning
is logged), which wipes the indexed vectors.

**After deploying the upgrade, re-index the corpus:**

```bash
# inside the running stack
scripts/reindex.sh                 # incremental; add --full-reindex to wipe first

# or via the API (LEGAL_EXPERT role or above)
curl -X POST http://localhost:8000/api/v1/documents/reindex -H "Authorization: Bearer $TOKEN"
```

Content-hash versioning skips unchanged documents, but a freshly recreated
collection is empty, so everything is re-ingested on the first run. Answers use
the new index immediately (allow ~5 min for the retrieval cache,
`retrieval_cache_ttl_seconds=300`).

The in-memory fallback store needs no migration (schema-less).

## Legal graph tables

The legal knowledge graph (`legal_graph_enabled=true`, default) creates three
tables — `documents`, `legal_articles`, `legal_relationships` — via SQLAlchemy
`create_all` on first use, in the configured `LEGAL_AI_DATABASE_URL` database,
falling back to `data/legal_graph_fallback.db` (SQLite) when that database is
unreachable. No manual migration is needed: the schema is additive (new tables
only), a re-ingest re-stamps a document's rows (`clear_document` then upsert),
and when the store is unreachable the graph silently degrades to no-op writes
and empty reads — ingestion and retrieval are unaffected. Existing corpora pick
up graph data as documents are re-ingested (e.g. via the reindex above).

## `ingestion_results.json`

Every ingest now persists its outcome (document id, name, status, version,
chunk count, timestamp, error for failures) into
`data/ingestion_results.json` — latest record per document, atomic temp-file +
`os.replace` writes. This is what powers the real `failed_documents` list of
`GET /api/v1/admin/ingestion/status` and the `ingestion` block of
`GET /api/v1/documents/{id}`. The file appears on the first ingest after the
upgrade; before that the endpoints report empty/absent data.

## `versions.json` backward compatibility

`data/versions.json` records gained an optional `articles` map (article key →
SHA256 of the article text) for article-level change detection. Legacy records
without the map keep working: they are treated as having an empty article map,
so the first ingest after the upgrade reports every article as *added* in the
`ArticleDiff` (logged and embedded in the ingest result detail), then tracks
per-article changes from there. No manual migration is needed.

## New settings

All settings use the `LEGAL_AI_` env prefix (`backend/core/config.py`):

| Env var | Default | Effect |
|---|---|---|
| `LEGAL_AI_COVERAGE_RETRY_THRESHOLD` | `0.6` | Below this sub-question coverage, the coverage auditor requests the (single) re-retrieval pass; also gates the "potentially incomplete answer" warning. |
| `LEGAL_AI_RANKING_TEMPORAL_WEIGHT` | `0.15` | Weight of the temporal component in evidence ranking; only active when the plan's temporal intent is `current`/`historical`. |
| `LEGAL_AI_EVIDENCE_INJECTION_SCREENING` | `true` | Scan retrieved chunks for embedded instructions before prompting; disable only for debugging. |
| `LEGAL_AI_LEGAL_GRAPH_ENABLED` | `true` | Persist the legal knowledge graph at ingestion and enable the graph worker (lookup + expansion). When off, both paths are no-ops. |
| `LEGAL_AI_RERANKER_PROVIDER` | `heuristic` | `heuristic` (offline default) or `api` (cross-encoder `/rerank` endpoint). `api` without full credentials degrades to heuristic with a warning. |
| `LEGAL_AI_RERANKER_MODEL` | unset | Rerank model name for the `api` provider (e.g. `bge-reranker-v2-m3`, `rerank-multilingual-v3.0`). |
| `LEGAL_AI_RERANKER_API_BASE` | unset | Base URL of the rerank endpoint for the `api` provider. |
| `LEGAL_AI_RERANKER_API_KEY` | unset | Bearer key for the rerank endpoint. |
| `LEGAL_AI_RERANKER_BATCH_SIZE` | `64` | Documents per rerank API call. |
| `LEGAL_AI_RERANKER_TIMEOUT_SECONDS` | `10.0` | HTTP timeout of the rerank client. |
| `LEGAL_AI_MODEL_ROLE_ROUTING_ENABLED` | `false` | Enable per-role model tiering (spec §46). Default off — zero behavior change. |
| `LEGAL_AI_PLANNER_MODEL` | unset | Model override for the planner node when role routing is on. |
| `LEGAL_AI_CLASSIFICATION_MODEL` | unset | Model override for `context_agent` / `memory_agent` (cheap tier). |
| `LEGAL_AI_ANALYSIS_MODEL` | unset | Model override for `reasoning_agent` / `reflection_agent`. |
| `LEGAL_AI_SYNTHESIS_MODEL` | unset | Model override for `response_generator` (strongest tier). |

Related pre-existing knobs that the new components also honor:
`max_retrieval_retries` / `max_reflection_iterations` (loop budgets, both 1),
`min_evidence_score`, `confidence_threshold`, `human_review_threshold`.

## New API endpoints

| Endpoint | Role | Purpose |
|---|---|---|
| `POST /api/v1/legal/query` | viewer | Versioned alias of `POST /api/v1/chat` — same contract, auth, caching and tracing. |
| `POST /api/v1/documents/reindex` | legal_expert | Re-ingest everything under `data_dir/legal_docs` (incremental by content hash, GC of stale documents); returns a `ReindexSummary`. |
| `GET /api/v1/articles/{document_id}/{article}` | viewer | Direct chunk lookup by document + article number (no similarity search); 404 when nothing matches. |
| `GET /api/v1/citations/{chunk_id}` | viewer | Resolve a citation/chunk id to its `CitationRecord` straight from the vector store (PK lookup, no similarity search); 404 when unknown. |
| `GET /api/v1/sources/{document_id}` | viewer | Document-level `SourceRecord` combining the version store (version, content hash, article count) with chunk-carried metadata (authority, type, law number, dates, url); 404 when unknown. |
| `GET /api/v1/admin/audit-log` | admin | Most recent in-memory audit entries (newest first, `limit` ≤ 1000). |
| `GET /api/v1/admin/ingestion/status` | admin | Per-document version/hash/article counts from `versions.json`, plus real `failed_documents` from `ingestion_results.json`. |
| `GET /api/v1/admin/evaluation/latest` | admin | Latest offline evaluation report (`data/eval/eval_report.json`); 404 when none exists. |
| `GET /api/v1/admin/retrieval/analytics` | admin | Request/error/latency aggregates from the in-memory audit log (Prometheus `/metrics` stays the cross-process source of truth). |

## Behavioral changes operators will notice

- **Trace is admin-only (spec §48)**: on `POST /chat`, `GET /chat/stream` and
  the WebSocket, non-admin roles receive an empty `trace` list and trace-free
  stream frames; the internal chain-of-thought is exposed to ADMIN tokens only.
  The shared answer cache never stores the trace, so a cached replay never
  leaks it either.
- **`GET /api/v1/documents/{id}` works again** — it previously always returned
  503; it now returns version/hash/article/chunk counts (version store + vector
  store) plus the latest persisted ingestion record, 404 when the document is
  unknown.
- **Case-analysis labeling**: `case_analysis` answers use their own sectioned
  structure (Faits / Qualification juridique / Règles applicables / Application
  / Incertitudes / Sources) with mandatory per-statement `[LOI]` /
  `[APPLICATION]` / `[HYPOTHÈSE]` prefixes.

- **Sectioned answers** for complex questions (rights, obligations, procedure,
  legal_rule, case_analysis, comparison): Réponse / Fondements juridiques /
  Application / Points d'incertitude / Sources.
- **Context-sensitive disclaimers**: full legal disclaimer for high-impact or
  low-confidence answers, a short informational note otherwise.
- **New warnings** on answers: neutralized evidence excerpts (injection
  screening), "citation d'article non vérifiée", "réponse potentiellement
  incomplète", unverifiable-citation removals, claim-verification flags, and
  `requires_human_review` escalations for contradicted or unsafe content.
- **Confidence semantics**: the aggregate is 0.4 × citation accuracy + 0.6 ×
  sub-question coverage, dampened ×0.85 per unresolved conflict and capped at
  0.75 for reflection-flagged gaps — a fully-cited but partial answer no longer
  displays "100%". Per-dimension detail is in `confidence_breakdown`;
  per-statement verdicts in `claims`.
- **Latency**: a coverage gap, `INSUFFICIENT:` reasoning or a reflection retry
  can trigger one extra retrieval fan-out per run; `rerank_llm_enabled` adds
  one LLM rescore call per retrieval branch.
- **First boot against an old Milvus collection**: the collection is dropped
  and recreated (schema self-heal) — expect empty answers until the reindex
  completes.

## Rollback

Reverting to the pre-upgrade code is safe for the **new** Milvus schema: the
old store only requires a subset of the fields, so it keeps working against the
upgraded collection (the extra scalar columns are ignored). `versions.json` is
likewise forward-written but backward-readable (unknown keys ignored by the old
code path). To roll back: redeploy the previous release, then reindex only if
you also restored an old-schema backup of the collection — otherwise no data
action is required. Note that answers generated by the new pipeline carry
fields (`claims`, `confidence_breakdown`) that the old frontend simply does not
render.
