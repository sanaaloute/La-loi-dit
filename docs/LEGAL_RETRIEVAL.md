# Legal Retrieval

The retrieval stack as implemented after the upgrade. Background on the
pre-upgrade design is in [retrieval.md](retrieval.md); the pipeline these
components plug into is documented in [RAG_ARCHITECTURE.md](RAG_ARCHITECTURE.md).

## Query understanding (deterministic first)

**Question types** — `backend/planner/question_types.py` classifies every query
into the `QuestionType` taxonomy (`factual`, `definition`, `legal_rule`,
`rights`, `obligations`, `procedure`, `comparison`, `case_analysis`,
`calculation`, `historical`, `current_law`, `document_summary`,
`source_lookup`, `general`) using ordered regex/keyword patterns, French-first
with English support. It never calls an LLM and falls back to `general`.
`detect_temporal_intent` separately returns `current` / `historical` / `any`
(historical is checked first, so "en vigueur en 2020" is historical).

**Planner** — `backend/planner/agent.py` is a tool-calling LLM agent that
produces the `RetrievalPlan` (sub-questions, search tasks, legal domains,
languages, scenario date). The deterministic classification seeds
`question_type`/`temporal_intent`; the LLM may override them. Two safety nets
are always applied post-hoc: at least one vector and one keyword task on the
raw query, and one keyword task per sub-question (up to 6) so broad questions
are searched by legal issue, not only by the question's own words.

**Deterministic decomposition** — `backend/planner/decomposition.py` is the
LLM-free fallback (spec §15): a curated French legal-issue taxonomy keyed by
topic (`licenciement`, `contrat_de_travail`, `divorce`, `bail_loyer`,
`succession`, `societe_ohada`). Only broad question types (`rights`,
`obligations`, `procedure`, `legal_rule`, `case_analysis`, `general`) are
decomposed; specific lookups (article numbers, amounts, definitions) are never
rewritten. Matched topics whose domains don't intersect the detected
`legal_domains` are skipped.

**Terminology lexicon & query expansion** — `backend/planner/terminology.py`
(spec §29) is a data-driven lexicon of 56 `TermEntry` records (Burkina Faso /
French legal terms: labour, civil, family, criminal, OHADA commercial,
administrative, tax, land), each carrying `synonyms`, `related_terms`,
`broader_terms` and `narrower_terms`. Matching is accent/case-insensitive
whole-phrase, on canonical forms and synonyms only. `expand_terms(query)`
returns `{canonical: [expansion terms]}` built from **synonyms + related terms
only** — broader/narrower terms are deliberately excluded because moving up or
down the hierarchy changes the legal meaning, and terms already present in the
query are dropped so expansion is purely additive (original terms are never
rewritten). Two wirings: the heuristic planner adds up to
`_MAX_EXPANSION_TASKS=3` extra keyword tasks (one per matched term group, query
= the group's expansion terms joined); the LLM planner can call the
`expand_legal_terms` tool (`backend/agents/tools/planning.py`) itself.

## Hybrid retrieval

`backend/retrieval/` implements `RetrieverProtocol` via the
`RetrievalCoordinator`:

1. **Workers in parallel** (`workers.py`) — one async worker per `SearchKind`:
   `VectorWorker` (vector store ANN; defaults to `role="child"` filter with an
   unfiltered fallback for legacy data), `KeywordWorker` (BM25 over
   `ctx.extras["bm25"]`, French tokenizer), orchestrator-backed workers for
   `government` / `regulation` / `case_law` / `news` / `web`,
   `UploadedWorker` (vector search restricted to `source_kind=uploaded`), and
   `GraphWorker` (`graph_worker.py`, for `SearchKind.GRAPH` tasks — see the
   graph section below).
   Worker failures are isolated: the exception is logged into
   `ctx.extras["retrieval_errors"]` and the other workers' results still fuse.
2. **Dedup + RRF fusion** (`dedup.py`, `fusion.py`) — near-duplicates dropped
   (`dedup_jaccard_threshold`), ranked lists merged with reciprocal rank
   fusion (`score += 1/(k+rank)`, `rrf_k=60`), normalized so the top result
   scores 1.0.
3. **Temporal hard filter** (`temporal.py`) — when the plan's temporal intent
   is `current` or `historical`, fused candidates pass
   `passes_temporal_filter`: for "current", repealed/expired/not-yet-in-force
   documents are dropped; for "historical" with a scenario date, only documents
   not yet in force at that date are dropped (a document repealed *before* the
   date is kept — it may be exactly what the question asks about). Undated,
   unknown-status documents always pass. If the filter would empty the
   candidate set, the unfiltered results are kept with a warning — retrieval
   never silently returns nothing.
4. **Rerank + relevance floor** (`reranker_providers.py`, coordinator) — the
   coordinator delegates to a `RerankerProvider` port
   (`backend/core/ports.py`): the default `HeuristicReranker` wraps the
   existing offline scoring (`rerank_similarity_weight` /
   `rerank_confidence_weight`, optional LLM rescore blended in when
   `rerank_llm_enabled`); `ApiCrossEncoderReranker` POSTs Cohere-style
   `{model, query, documents}` to a `/rerank` endpoint (BGE, Qwen, Cohere,
   vLLM/TEI) in batches (`reranker_batch_size`), retries once, and on any
   failure falls back to the heuristic reranker with a warning. The
   `get_reranker` factory picks from `reranker_provider`
   (`heuristic` default | `api`); `api` without full credentials
   (`reranker_api_base` + `reranker_api_key` + `reranker_model`) degrades to
   heuristic — an offline install always works. After reranking, a relevance
   floor applies: a chunk must share enough content tokens with the query
   (`retrieval_min_shared_tokens`), clear the similarity floor
   (`retrieval_similarity_floor`, capped at 0.45 for real embedding models), or
   share at least one *discriminative* token (rare across the candidate set)
   above `retrieval_weak_similarity_floor`. Truncation to `max(top_k)` happens
   only after the floor, so discriminative matches ranked just below the cut
   are not lost.
5. **Graph expansion** (`graph_worker.py`, coordinator) — with
   `legal_graph_enabled` on (default), the top-ranked fused candidates are
   expanded through the legal knowledge graph: `references` / `amends` /
   `repeals` edges of the first 3 chunks are followed and the target articles
   pulled from the vector store, appended with a fixed low score (0.01, always
   ranked below direct evidence), capped at 8 chunks and marked
   `retrieved_via="graph"`. Fully best-effort: any failure keeps the fused
   results unchanged.

Results are cached per task-set under `retrieval:` (TTL
`retrieval_cache_ttl_seconds`, default 300s); the temporal intent and scenario
date are part of the cache key.

## Legal knowledge graph (spec §19)

`backend/knowledge/` is a **relational** legal knowledge graph — deliberately
no graph database. `LegalGraphStore` (`store.py`) uses SQLAlchemy Core over the
configured `database_url`, falling back to a local SQLite file
(`legal_graph_fallback.db` under `data_dir`) when the database is unreachable;
it creates its schema lazily on first use and **never raises** — outages
degrade to no-op writes and empty reads (counted in `stats["db_failures"]`).
Three tables:

- `documents` — one row per ingested instrument (name, `document_type`,
  `law_number`, jurisdiction, status, dates, `content_hash`, version).
- `legal_articles` — one row per (document, article) with hierarchy, page and
  text preview.
- `legal_relationships` — typed edges: `contains`, `amends`, `repeals`,
  `references`, `referenced_by`, `issued_by`, `applies_to`. Unresolved targets
  keep `dst_document=NULL` and carry the raw mention in `dst_free_text`.

**Extraction** (`extraction.py`) is deterministic and French-first, precision
over recall: an edge is only emitted when an explicit article or law number is
present. Active amendment/repeal verbs ("modifie", "abroge" — passive forms
like "est abrogé" are skipped, the direction would be a guess), qualified
cross-references ("l'article 5 du Code pénal"), bare references (resolve to the
current document), plus `contains` / `issued_by` / `applies_to` from chunk
metadata. Self-references are dropped.

**Ingestion hook** — after a successful upsert, `_persist_legal_graph`
(`backend/ingestion/pipeline.py`) upserts the document row, its article rows
and the extracted edges. Additive and best-effort: a graph failure never fails
ingestion; the whole hook is a no-op when `legal_graph_enabled` is off.

**Query side** — `GraphWorker` (`backend/retrieval/graph_worker.py`) has two
entry points: `run(task)` handles `SearchKind.GRAPH` tasks by resolving
explicit mentions in the query ("article 341 du code du travail", "loi n°
028-2008/AN") through the store and returning the matching chunks from the
vector store (score 1.0, `retrieved_via="graph"`; a bare article number without
a document hint is considered too noisy and skipped); `expand(chunks)` is the
post-rerank expansion described above. `relationships_for` returns edges in
both directions, so "referenced by whom?" needs no materialized inverse edges.
The store is shared per process via `graph_store_for(ctx)` (memoized in
`ctx.extras["legal_graph"]`, `None` when the feature flag is off).

## Temporal model

`EvidenceChunk` carries `status` (`active` | `repealed` | `amended` | `future`
| `unknown`), `valid_from` / `valid_until` (with `effective_date` and
`publication_date` as fallbacks for the window start). Chunks ingested before
these fields existed deserialize to `status="active"` with no dates, score 1.0
for "current" intent and always pass the filter — only explicitly
repealed/expired/future documents are ever excluded. Beyond the hard filter,
`temporal_score` (0–1) is blended into evidence ranking so time-inapplicable
evidence sinks instead of disappearing; undated evidence scores
`temporal_score_unknown` (default 0.3) for "current" intent.

## Authority weighting

`backend/core/constants.py` — `AUTHORITY_WEIGHTS` over the 16-level
`AuthorityLevel` enum (Constitution 1.00 → OHADA treaty 0.95 → amended law
0.92 → law 0.90 → decree 0.80 → … → blog 0.10 → unknown 0.15).
`OFFICIAL_DOMAINS` lists BF/OHADA official domains never outranked by blogs;
`LEGAL_DOMAINS` is the domain vocabulary the planner maps keywords to.

## Evidence ranking (composite score)

`backend/agents/evidence_ranking.py` — deterministic, no LLM:

```text
relevance  = max(chunk.rerank_score, chunk.retrieval_score)
authority  = AUTHORITY_WEIGHTS[chunk.authority]
confidence = chunk.confidence or 0.5
base       = 0.55·relevance + 0.30·authority + 0.15·confidence    (weights from settings)
```

When the plan has a temporal intent (`current`/`historical`), a temporal
component is blended with weight `ranking_temporal_weight` (default 0.15) and
renormalized; intent `any` keeps the legacy base score unchanged. Chunks
scoring below `min_evidence_score` (0.05) are dropped.

## Coverage auditor and the re-retrieval loop

`backend/agents/coverage_auditor.py` runs **after ranking, before drafting** —
deterministically, with no LLM call. Each planned sub-question is checked
against the ranked evidence by discriminative-term matching (significant
lowercase tokens ≥ 4 chars minus a French stopword list; a sub-question is
covered when one chunk carries at least half of its discriminative terms).
When coverage falls below `coverage_retry_threshold` (0.6) and issues are
missing, the graph fans out **one** bounded re-retrieval targeting only the
missing issues (`max_retrieval_retries=1`). The same `audit_coverage` function
is reused by the response generator to score answer completeness.

## Chunk metadata model

`EvidenceChunk` (`backend/core/models.py`) is the contract every retrieved unit
carries:

- **Identity/provenance**: `chunk_id`, `document_id`, `document_name`,
  `url`, `source_kind`, `version`, `language`, `page`.
- **Legal structure**: `article` (normalized — "1er"/"premier" collapse to
  "1"), `section` (deepest heading, compat), `hierarchy` (ordered level map
  `{"livre": "I", "titre": "II", ...}`), `parent_chunk_id` / `child_chunks`.
- **Dual text (spec §7)**: `retrieval_text` / `context_text`, stamped by the
  parent-expansion node on expanded parents — `retrieval_text` is the exact
  child passage that matched the query, `context_text` the enclosing parent
  (article/section) it was expanded to. Both stay `None` on chunks without a
  parent, where `content` serves both roles; when several children share one
  parent, the first matching child provides `retrieval_text` and every child
  stays visible under `parent.child_chunks`.
- **Lifecycle**: `publication_date`, `effective_date`, `status`,
  `valid_from`, `valid_until`.
- **Instrument metadata (spec §6)**: `document_type` (code/law/decree/
  ordinance/decision/case_law/treaty/article/other), `law_number` (e.g.
  "028-2008/AN"), `jurisdiction` (default "Burkina Faso"),
  `issuing_authority`, `government_body`.
- **Scoring**: `authority`, `confidence`, `retrieval_score`, `rerank_score`.
- **Embedding identity**: `embedding_model` — the model that embedded the
  chunk, stamped at upsert time.

**Milvus scalar filters** — `document_id`, `article`, `status` and
`document_type` are native scalar columns filtered via Milvus `expr`
(`build_native_filter_expr` in `backend/vectorstore/milvus_store.py`); all
other filter keys (e.g. `role`, `legal_domains`, `source_kind`) are applied
client-side with `matches_filters`, compensated by overfetch
(`milvus_filter_overfetch=4`).

## Walkthrough: a broad rights question

Golden query (also the §38 regression case, `qa-016` in the golden dataset):
**"Quels sont les droits d'un salarié licencié au Burkina Faso ?"**

1. **Planner**: `classify_question_type` → `rights` (matches
   `droits d'un`); `detect_temporal_intent` → `any`; domain keywords →
   `labor_code`. The deterministic decomposition matches the `licenciement`
   topic and emits 7 issues (motifs légitimes, préavis et indemnité
   compensatrice, indemnité de licenciement, licenciement abusif et
   dommages-intérêts, droits acquis, voies de recours, juridiction
   compétente). Plan: 8 sub-questions (query + 7 issues), vector + keyword
   tasks plus one keyword task per issue, `legal_domains=["labor_code"]`
   filters.
2. **Fan-out**: 8 parallel `retrieval_branch` nodes, each running vector +
   keyword searches for its sub-question through the coordinator (fetch 20 per
   worker, RRF fuse, rerank, relevance floor). Temporal intent is `any`, so no
   temporal filtering applies.
3. **Merge → conflict resolution → parent expansion**: branch results fuse;
   same-article disagreements are resolved by authority/recency; top child
   chunks expand to their parent articles so the LLM sees whole provisions.
4. **Evidence ranking**: composite score; labor-code chunks from the Code du
   travail (authority `law` = 0.90) outrank secondary sources.
5. **Coverage audit**: the 8 sub-questions are checked against the ranked
   evidence. If e.g. only "juridiction compétente" chunks were found, coverage
   is 1/8 < 0.6 → one re-retrieval fan-out on the 7 missing issues.
6. **Reasoning → reflection**: LLM analysis with one bounded retry each.
7. **Response**: `rights` is a complex question type, so the answer uses the
   sectioned format (Réponse / Fondements juridiques / Application / Points
   d'incertitude / Sources) with `[n]` citations; claim and citation
   verification grade every statement; the output guardrail appends the full
   legal disclaimer (high-impact type).
