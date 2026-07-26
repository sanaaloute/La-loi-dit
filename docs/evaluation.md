# Evaluation

The evaluation subsystem (`backend/evaluation/`) measures answer quality
against a golden dataset of Burkina Faso legal questions
(`backend/evaluation/golden_dataset.json`). Results are returned as
`EvalCaseResult` objects (`backend/core/models.py`) and persisted to the
`evaluations` table.

## Metric definitions

| Metric | What it measures |
|---|---|
| **Groundedness** | Fraction of answer statements supported by the retrieved evidence chunks (the [n] citations actually back the claim) |
| **Faithfulness** | The answer does not contradict the evidence; no claim introduces facts absent from it |
| **Citation accuracy** | Fraction of citations that resolve to a real chunk **and** the chunk supports the cited statement — fabricated citations score 0 |
| **Answer relevance** | The answer addresses the question (and all its sub-parts) |
| **Hallucination detected** | Boolean: any legal provision, article number or date present in the answer but not in the evidence |
| **Precision / recall (retrieval)** | Of the chunks the golden case marks relevant, how many were retrieved (recall) and how much of the retrieved set is relevant (precision) |
| **Latency** | End-to-end chat latency per case (`latency_ms`) |
| **Cost** | LLM tokens per case × provider price (0 for the `mock` provider) |

A case **passes** when its metric thresholds are met and no hallucination
is detected.

## Running an evaluation

```bash
make eval
# = python -m backend.evaluation.runner --dataset backend/evaluation/golden_dataset.json
```

Useful flags: `--out report.json` (write the report), `--provider mock`
(force the deterministic provider for regression runs),
`--case <id>` (single case).

Run the suite after: ingestion of new documents, switching LLM
provider/model, changing prompts, or touching retrieval/ranking code.

## Reading the report

- Per case: the four quality scores, `hallucination_detected`, latency and
  `passed`, plus a `detail` string explaining failures (e.g. which citation
  failed to resolve).
- Aggregate: mean scores, pass rate, p50/p95 latency, estimated cost.
- Regression workflow: diff the aggregate against the previous run; any
  drop in groundedness or citation accuracy, or a new hallucination flag,
  blocks promotion (see [administrator.md](administrator.md)).

## Extending the golden dataset

Cases in `backend/evaluation/golden_dataset.json`:

```json
{
  "id": "qa-011",
  "question": "Quel est le préavis de licenciement pour un employé mensualisé au Burkina Faso ?",
  "expected_keywords": ["préavis", "un mois"],
  "expected_documents": ["Code du travail du Burkina Faso"],
  "domain": "labor_code",
  "difficulty": "easy"
}
```

(`domain` is one of `LEGAL_DOMAINS`; `difficulty` is `easy|medium|hard`.)

Guidelines:

- Cover every domain in `LEGAL_DOMAINS` and the hard paths: conflicting
  versions of a law, scenario-date questions, questions with **no** evidence
  (expected behavior: honest "insufficient evidence", not a guess), and
  prompt-injection attempts (expected: refusal).
- `expected_documents` / `expected_keywords` drive retrieval recall and
  groundedness checks — keep them precise.
- The shipped cases use **synthetic seed evidence** (see the dataset's own
  disclaimer and `seed_data.py`) — they validate the pipeline, not legal
  correctness. Cases run offline with the `mock` provider for CI; quality
  gates should be validated with the production provider before release.
