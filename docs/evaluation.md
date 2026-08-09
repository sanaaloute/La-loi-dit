# Evaluation

The offline evaluation framework as implemented. Background and usage notes
are in [evaluation.md](evaluation.md); this document covers the post-upgrade
dataset schema, metric set and gates.

Everything runs hermetically: the runner (`backend/evaluation/runner.py`)
builds an offline `AppContext` (mock LLM, `HashEmbeddings`, in-memory cache and
vector store), seeds the store with the synthetic evidence of
`backend/evaluation/seed_data.py`, runs the **real compiled LangGraph
pipeline** over the golden dataset and writes JSON + Markdown reports.

## Golden dataset

`backend/evaluation/golden_dataset.json` — 25 illustrative cases covering 10
legal domains (≥ 2 cases each). Per-case schema:

| Field | Required | Meaning |
|---|---|---|
| `id` | yes | Case id (`qa-NNN`) |
| `question` | yes | User question (French) |
| `expected_keywords` | no | Keywords the answer must contain (accent-insensitive) |
| `expected_documents` | no | Document names that must appear in the evidence (substring, accent-insensitive) |
| `expected_articles` | no | Article numbers expected in the evidence (drives rank-aware metrics) |
| `expected_issues` | no | `[{"category": ..., "keywords": [...]}]` issue categories the answer must touch |
| `domain`, `difficulty`, `language`, `scenario_date`, `note` | no | Metadata; `scenario_date` anchors timeline questions |

The dataset header carries a `disclaimer` (sample cases, not legal advice) and
a `coverage_note` (growing the set to 100+ cases with real verified texts is
planned future work).

### The §38 golden regression case

`qa-016` — **"Quels sont les droits d'un salarié licencié au Burkina Faso ?"** —
declares `expected_articles` (90, 95, 96, 97, 98, 100) and seven
`expected_issues`: `dismissal_grounds`, `notice`, `compensation`,
`accrued_rights`, `unfair_dismissal`, `legal_remedies`, `jurisdiction`.

The **issue-coverage gate** enforces the regression: `issue_coverage` counts an
issue category as covered when at least one of its keywords appears in the
answer; a case fails whenever any declared category is missing
(`not missing_issues` is part of the pass condition). An answer discussing only
tribunal jurisdiction therefore fails even if every citation is verified —
which is exactly the pre-upgrade failure mode the spec calls out.

## Metrics

`backend/evaluation/metrics.py` — pure, offline functions:

**Answer-level**
- `citation_accuracy` — fraction of citations verified against evidence (1.0
  when there are none).
- `groundedness` — fraction of citation-marked statements whose `[n]` markers
  all resolve to a real evidence chunk.
- `answer_relevance` — fraction of `expected_keywords` present (accent/case
  insensitive).
- `hallucination_detected` — true when any citation is unverified, or an
  evidence-backed answer has a substantive line (≥ 8 words, not a header or
  disclaimer line) with no citation marker.

**Retrieval-level (set-based)**
- `retrieval_precision` / `retrieval_recall` against `expected_documents`.

**Rank-aware** (over the evidence order, reported at k=5)
- `recall_at_k`, `precision_at_k` (denominator `min(k, len(ranked))`), `mrr`,
  `ndcg_at_k` (binary relevance). Computed per expectation kind the case
  declares — document names and/or article numbers — and averaged.

**Completeness**
- `issue_coverage` — fraction of `expected_issues` categories covered, plus the
  list of missing categories.

## Running

```bash
python -m backend.evaluation.runner \
    --dataset backend/evaluation/golden_dataset.json \
    --out data/eval/eval_report
```

`--dataset` defaults to the bundled golden dataset; `--out` is a path prefix —
`<out>.json` (machine-readable aggregate + per-case results) and `<out>.md`
(Markdown report: aggregate table + per-case PASS/FAIL table) are written.

## Pass thresholds

Per case (constants in `runner.py`):

| Gate | Threshold |
|---|---|
| hallucination | none detected |
| issue coverage | no missing expected issue category |
| citation accuracy | ≥ 0.99 |
| groundedness | ≥ 0.8 |
| answer relevance | ≥ 0.5 |
| retrieval recall | ≥ 0.5 (only when the case declares expected documents) |

## Current baseline

Recorded in `data/eval/eval_report.md` / `.json` (generated 2026-08-09, offline
mock-LLM run over the synthetic seed corpus — it validates pipeline mechanics,
not real-world legal accuracy):

- **25/25 cases passed (100%)**
- mean groundedness 1.000, citation accuracy 1.000, answer relevance 1.000,
  issue coverage 1.000
- mean recall@5 0.993, precision@5 0.421, MRR 0.910, nDCG@5 0.929
- hallucination rate 0.000; mean latency 40 ms, p95 42 ms

## Adding cases

1. Append a case object to `backend/evaluation/golden_dataset.json` following
   the schema above (id, question, plus whichever expectations apply).
2. If the case expects documents/articles not covered by the synthetic seed
   corpus, extend `backend/evaluation/seed_data.py` so retrieval can actually
   find them.
3. Re-run the runner and compare against the baseline; prompt or pipeline
   changes that alter answer shape require re-baselining (spec §54).
