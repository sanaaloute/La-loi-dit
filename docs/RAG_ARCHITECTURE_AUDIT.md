# RAG Architecture Audit — Burkina Faso Legal AI

Date: 2026-08-09
Scope: full-repository audit against the "Production-Grade Agentic Legal RAG Platform" specification.
Baseline at audit time: **164 tests passing** (`pytest tests/ -q`).

---

## 1. Current architecture

### Backend (Python / FastAPI / LangGraph)

```text
User query
  → input_guardrail (regex injection/jailbreak/PII policies, fail-closed)
  → planner (LLM tool-calling agent: domains, language, scenario date, sub-questions, tasks)
  → context_agent → memory_agent (MemGPT-style tiers, Redis/Postgres)
  → retrieval_branch × N (LangGraph Send fan-out, one per sub-question)
      → RetrievalCoordinator: parallel workers (vector / BM25 / government /
        regulation / case_law / news / web / uploaded) → dedup → RRF → rerank
  → retrieval_merge
  → conflict_resolver (authority + version-in-force at scenario date)
  → parent_expansion (child chunk → parent article context)
  → evidence_ranking (relevance + authority + confidence composite)
  → reasoning_agent (LLM; can emit INSUFFICIENT: → bounded re-retrieval)
  → reflection_agent (self-critique; bounded retry)
  → response_generator (LLM-only synthesis, [n] citations)
  → citation_verification (marker-in-range check, strips fabricated citations)
  → output_guardrail (refusal policy, hallucination suspects, disclaimer)
```

Key modules:

| Area | Location | State |
|---|---|---|
| Orchestration | `backend/workflows/graph.py` | 13+ node LangGraph, conditional edges, `Send` fan-out |
| Planning | `backend/planner/agent.py` | LLM decomposition incl. the "salarié licencié" example; heuristic fallback (non-decomposing by design) |
| Retrieval | `backend/retrieval/` | hybrid workers, RRF (`rrf_k=60`), heuristic+LLM rerank, relevance floor |
| Vector store | `backend/vectorstore/` | Milvus (server/Lite) + in-memory fallback; HNSW/COSINE |
| BM25 | `backend/retrieval/bm25.py` | `rank_bm25` with French tokenizer, accent stripping |
| Authority | `backend/core/models.py`, `core/constants.py`, `search/sources.py` | 16-level `AuthorityLevel`, `AUTHORITY_WEIGHTS`, domain→authority map |
| Ingestion | `backend/ingestion/` | pypdf/python-docx/BS4/txt/md loaders, legal parent-child chunking, SHA256 versioning, crawler, freshness monitor |
| LLM | `backend/core/llm.py` | LiteLLM multi-provider + failover + mock offline mode |
| Guardrails | `backend/guardrails/` | deterministic input/output policies, FR+EN |
| Observability | `backend/observability/` | structured JSON logs, Langfuse, OTel, Prometheus |
| Evaluation | `backend/evaluation/` | offline runner, 15-case golden dataset, set-based metrics |
| API | `backend/api/` | chat (REST/SSE/WS), documents, search, auth, billing, export |

### Frontend
Next.js 15 (App Router, TypeScript, Tailwind): chat UI with SSE streaming, agent timeline, citation panel, evidence viewer, JWT login.

### Infrastructure
Docker Compose: API, Celery, Nginx, PostgreSQL, Redis, Milvus, Temporal, Prometheus, Grafana, Langfuse. Offline-first: every external dependency has an in-process fallback.

---

## 2. Strengths (preserve)

- **Orchestration**: complete LangGraph pipeline with conditional re-retrieval, bounded retries, per-worker failure isolation, streaming.
- **Hybrid retrieval**: parallel vector + BM25 + official-source workers, RRF fusion with configurable weights, dedup, relevance floor.
- **Authority model**: 16-level hierarchy (constitution → OHADA → amended law → law → decree → … → blog) consumed in ranking and conflict resolution.
- **Conflict resolution**: version-in-force at scenario date > authority > recency; unresolved conflicts dampen confidence (×0.85 each) and surface a user-facing note, never silently resolved.
- **Citation verification**: post-generation marker verification, fabricated `[n]` stripped, confidence scaled by accuracy; refusal path for zero-evidence answers.
- **LLM abstraction**: LiteLLM multi-provider with failover, token metering, mock offline provider; subscription-tier model catalog.
- **Guardrails**: deterministic injection/jailbreak/PII regex policies (FR+EN), fail-closed input guard, output refusal policy — tested.
- **Observability**: Langfuse traces, Prometheus metrics, structured logs, per-request trace breadcrumbs.
- **Ingestion versioning**: SHA256 content-hash versioning with delete-and-reindex on change; polite crawler with BF-official domain allowlist; RSS/ETag freshness monitor.
- **Offline-first**: entire pipeline runs with zero credentials; 164 tests green.

---

## 3. Weaknesses and technical debt

### 3.1 Retrieval weaknesses
1. **No question-type classification** (FACTUAL/RIGHTS/PROCEDURE/HISTORICAL/CURRENT_LAW/…). Retrieval strategy is not conditioned on question type.
2. **Decomposition is LLM-only.** The heuristic fallback deliberately does not decompose; if the planner LLM fails, a broad rights question degrades to a single raw-query search — exactly the failure mode the spec targets.
3. **Coverage analysis doesn't drive the loop.** `_question_coverage` only adjusts confidence post-hoc; re-retrieval depends on LLM self-reporting (`INSUFFICIENT:`), not on deterministic missing-sub-issue detection.
4. **No knowledge-graph / relationship retrieval** — no `amends`/`repeals`/`references` edges anywhere.
5. **Temporal retrieval is shallow**: no `valid_from`/`valid_until`/status fields on chunks, no current-law filter at retrieval time; temporal reasoning exists only in conflict resolution when two versions collide.
6. **No cross-encoder reranker** — rerank is bi-encoder cosine + lexical + optional LLM blend; composite score lacks temporal and structural signals.
7. **No systematic query expansion** (synonyms/multi-query); no legal terminology normalization layer.
8. **Milvus metadata filtering is client-side** (overfetch ×4 then post-filter); only `document_id` is a native scalar filter.

### 3.2 Document-processing weaknesses
1. **Hierarchy flattened**: Livre/Titre/Chapitre/Section collapse into one `section` string; level types lost; no queryable tree.
2. **No `retrieval_text`/`context_text` distinction** as stored fields (parent/child is the functional equivalent).
3. **Chunk metadata gaps**: no `document_type`, `law_number`, `jurisdiction`, `status`, `expiration_date`, `issuing_authority`, or relationship fields (`amends`, `amended_by`, …).
4. **OCR is dead code** — pytesseract/Pillow not in dependencies; no `.doc`, `.csv`, or image loaders; no layout-aware parser (Docling/Unstructured).
5. **Article-level change detection missing** — versioning is whole-document hash only; old versions deleted, not archived. Broken dead code in `versioning.py` (`get_version`).
6. **Crawler skips PDFs** — the Journal Officiel and most BF gazettes are PDFs, so the most authoritative artifacts are unreachable via crawling.
7. **Embedding identity not persisted** — model name/version/dimension not stored with vectors; switching models silently mixes vector spaces.
8. **No relational corpus schema** — `documents`, `document_versions`, `legal_articles`, `legal_relationships` tables don't exist; corpus truth is a JSON file + Milvus blobs.

### 3.3 Citation weaknesses
1. **No claim-level verification** — no `Claim`/`support_level` (DIRECT/INDIRECT/INSUFFICIENT/CONTRADICTORY). Verification checks that `[n]` is in range, not that the chunk supports the statement.
2. **Citation style is opaque `[n]`** — the spec's "Selon l'article X du Code du travail…" style is not enforced; article numbers quoted in prose are not validated against evidence.
3. **`INSUFFICIENT_EVIDENCE` is implicit**, not a first-class typed state.

### 3.4 Legal-reasoning weaknesses
1. **No case-analysis flow** (facts → issues → applicable law → application; LAW vs APPLICATION vs ASSUMPTION labeling).
2. **No deterministic legal calculators** (préavis, indemnité de licenciement, délais); generic calculator/date tools exist but are unwired.
3. **Confidence model is a single float** (0.4·citation_accuracy + 0.6·coverage with caps), not the spec's multi-dimensional model (source/retrieval/legal_support/temporal/citation/coverage).
4. **Response format** is not the spec's sectioned layout; disclaimers are fixed boilerplate, not context-sensitive.
5. **Retrieved-document injection defense missing** — chunks enter prompts unscanned and unwrapped as DATA; only user-query injection is blocked.

### 3.5 Evaluation / API weaknesses
1. Golden dataset has **15 cases, not 100+**; **no golden case for "Quels sont les droits d'un salarié licencié au Burkina Faso ?"**.
2. **No rank-aware metrics** (Recall@K, MRR, nDCG); no regression baseline tracking.
3. API gaps: no `/api/v1/legal/query`, no reindex/articles/citations endpoints, no admin router.
4. No task-based model tiering (cheap for classification, strong for synthesis).

---

## 4. Recommended architecture

Keep the existing LangGraph spine — it is sound. Extend it rather than replace it:

```text
input_guardrail
  → query_analyzer (NEW: question type, language, jurisdiction, temporal intent)
  → planner (EXTENDED: type-aware strategy + deterministic decomposition fallback)
  → context/memory (unchanged)
  → retrieval fan-out (EXTENDED: + temporal/status filters, + relationship expansion,
                     + terminology-normalized query variants)
  → conflict_resolver (unchanged)
  → parent_expansion (unchanged)
  → evidence_ranking (EXTENDED: + temporal & structural score components)
  → reasoning_agent (unchanged)
  → coverage_auditor (NEW: deterministic sub-issue coverage → re-retrieval loop)
  → response_generator (EXTENDED: sectioned legal format, typed evidence state)
  → claim_verifier (NEW: claim-level support classification)
  → citation_verification (unchanged)
  → output_guardrail (EXTENDED: document-injection screening upstream,
                      context-sensitive disclaimer, multi-dimensional confidence)
```

Storage upgrades: enrich chunk metadata (status, valid_from/until, law_number, jurisdiction, hierarchy path, embedding identity); promote filter fields to Milvus scalar fields; add a lightweight relational corpus schema (documents, document_versions, legal_articles, legal_relationships) in the existing SQLAlchemy layer. Do **not** migrate off Milvus — it meets the requirements; Qdrant is not justified.

---

## 5. Migration plan (implementation order)

Each phase ships with tests; nothing existing is removed until its replacement is tested.

- **Phase 1 — Audit** (this document). ✅
- **Phase 2 — Query analysis & decomposition safety net**: `QuestionType` taxonomy, query analyzer, deterministic decomposition fallback for broad-rights questions, temporal intent detection.
- **Phase 3 — Metadata & temporal model**: enrich `EvidenceChunk` (document_type, law_number, jurisdiction, status, valid_from/valid_until, hierarchy path, embedding identity); ingestion enrichment; temporal filtering in retrieval; native Milvus scalar filters.
- **Phase 4 — Coverage-driven loop**: deterministic sub-issue coverage auditor wired to the re-retrieval edge.
- **Phase 5 — Claim-level citation verification**: `Claim` model with support levels; claim extraction + support classification; insufficient-claim qualification.
- **Phase 6 — Confidence model**: multi-dimensional confidence object (source/retrieval/legal_support/temporal/citation/coverage).
- **Phase 7 — Security hardening**: retrieved-document injection screening + evidence wrapping as DATA.
- **Phase 8 — Response format & disclaimers**: sectioned legal answer format, "Selon l'article X…" style, context-sensitive disclaimers, explicit `INSUFFICIENT_EVIDENCE` state.
- **Phase 9 — Legal relationships**: relationship extraction at ingestion (amends/repeals/references), relational storage, graph-aware retrieval expansion.
- **Phase 10 — Evaluation**: golden case for the salarié-licencié failure, dataset expansion, Recall@K/MRR/nDCG, regression baseline.
- **Phase 11 — Ingestion hardening**: fix `versioning.py` dead code, article-level change detection, CSV/DOC loaders, OCR dependency wiring, crawler PDF support.
- **Phase 12 — API & admin**: `/api/v1/legal/query`, reindex/articles endpoints, admin trace gating.

## 6. Risks

- **LLM-less determinism**: new deterministic components (decomposition fallback, coverage auditor, claim support) must be heuristic-first so the offline/mock mode and tests stay hermetic.
- **Index migration**: enriching Milvus schema requires re-indexing; the existing drop-and-recreate self-heal handles this but wipes vectors — document the reindex path.
- **Scope**: 100+ eval cases and full Neo4j-grade graph are deferred; the relational relationship store is the justified lightweight first step (spec §19).
- **Prompt changes** alter answer shape; golden thresholds must be re-baselined (spec §54).

---

## 7. Post-audit resolution status

Where each major finding landed after the upgrade (details in
[RAG_ARCHITECTURE.md](RAG_ARCHITECTURE.md), [LEGAL_RETRIEVAL.md](LEGAL_RETRIEVAL.md),
[DOCUMENT_PROCESSING.md](DOCUMENT_PROCESSING.md), [CITATION_SYSTEM.md](CITATION_SYSTEM.md),
[MIGRATION.md](MIGRATION.md)):

| Audit finding | Status | Resolution |
|---|---|---|
| 3.1.1 No question-type classification | Resolved | `backend/planner/question_types.py` (`QuestionType` taxonomy + temporal intent), seeded into every plan. |
| 3.1.2 LLM-only decomposition | Resolved | `backend/planner/decomposition.py` deterministic taxonomy fallback. |
| 3.1.3 Coverage not driving the loop | Resolved | `backend/agents/coverage_auditor.py` + bounded re-retrieval fan-out on missing issues. |
| 3.1.4 No knowledge-graph retrieval | Resolved | `backend/knowledge/` (relational store + regex edge extraction), `GraphWorker` lookup + post-rerank expansion. |
| 3.1.5 Shallow temporal retrieval | Resolved | `status`/`valid_from`/`valid_until` on chunks, hard temporal filter in the coordinator, temporal score in ranking. |
| 3.1.6 No cross-encoder reranker | Resolved | `RerankerProvider` port with `ApiCrossEncoderReranker` (`reranker_provider=api`); offline heuristic stays the default. |
| 3.1.7 No query expansion / terminology layer | Resolved | `backend/planner/terminology.py` (56-entry lexicon; synonyms + related terms only) wired into the heuristic planner and the `expand_legal_terms` tool. |
| 3.1.8 Client-side Milvus filtering | Resolved | `document_id`, `article`, `status`, `document_type` are native scalar filters; remaining keys post-filtered with overfetch. |
| 3.2.1 Flattened hierarchy | Resolved | Ordered `hierarchy` level map on every chunk. |
| 3.2.2 No `retrieval_text`/`context_text` | Resolved | Dual fields on `EvidenceChunk`, stamped by the parent-expansion node. |
| 3.2.3 Chunk metadata gaps | Resolved | `document_type`, `law_number`, `jurisdiction`, `status`, dates, `issuing_authority` added; relationship fields live in the graph store. |
| 3.2.4 Dead OCR / missing loaders | Partially resolved | CSV loader added; OCR stays dormant (optional deps), `.doc` and layout-aware parsing deferred (documented limitations). |
| 3.2.5 No article-level change detection | Resolved | Per-article hashes in `versions.json` + `ArticleDiff`; dead `get_version` code removed. |
| 3.2.6 Crawler skips PDFs | Deferred | By design; the Journal Officiel must be ingested from files (documented limitation). |
| 3.2.7 Embedding identity not persisted | Resolved | `embedding_model` stamped on every chunk at upsert. |
| 3.2.8 No relational corpus schema | Resolved | `documents` / `legal_articles` / `legal_relationships` tables (`LegalGraphStore`); corpus truth for retrieval remains `versions.json` + vector store. |
| 3.3.1 No claim-level verification | Resolved | `backend/agents/claim_verification.py` (`Claim`/`SupportLevel`, deterministic grading). |
| 3.3.2 Opaque `[n]` citation style | Resolved | Prose source naming enforced by the prompt contract; unverified-article soft check in the output guard. |
| 3.3.3 Implicit `INSUFFICIENT_EVIDENCE` | Resolved | Fixed bilingual insufficient-evidence answer path (confidence 0.0 + warning); zero-evidence non-declaring answers refused. |
| 3.4.1 No case-analysis flow | Resolved | CASE_ANALYSIS prompt addendum: Faits → Qualification → Règles → Application → Incertitudes with `[LOI]`/`[APPLICATION]`/`[HYPOTHÈSE]` labels. |
| 3.4.2 No deterministic legal calculators | Resolved (library) | `backend/tools/legal_calculations.py` + `legal_rules.json` (honest `verified: false` flags, `RuleNotFound` instead of guesses); wiring into the agent tool loop deferred. |
| 3.4.3 Single-float confidence | Resolved | Multi-dimensional `ConfidenceBreakdown` alongside the aggregate. |
| 3.4.4 Response format / disclaimers | Resolved | Sectioned format for complex types; context-sensitive disclaimers. |
| 3.4.5 No document-injection defense | Resolved | `backend/guardrails/document_guard.py` screening + evidence wrapped as DATA. |
| 3.5.1 Golden dataset too small | Partially resolved | 15 → 25 cases including the salarié-licencié regression case; 100+ cases deferred (scope risk, §6). |
| 3.5.2 No rank-aware metrics | Resolved | recall@k, precision@k, MRR, nDCG@k + issue-coverage gate. |
| 3.5.3 API gaps | Resolved | `/api/v1/legal/query`, reindex, articles, citations, sources endpoints and the admin router (audit-log, ingestion status, evaluation, retrieval analytics). |
| 3.5.4 No task-based model tiering | Resolved | `backend/core/model_roles.py` role overrides (`planner`/`classification`/`analysis`/`synthesis`), default off. |

Open deferrals: 100+ eval cases, Neo4j-grade graph (the relational store is
the deliberate lightweight first step), OCR/layout parsing, `.doc` support,
crawler PDF support, old-version archiving, and wiring the legal calculators
into the agent tool loop.
