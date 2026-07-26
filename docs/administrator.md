# Administrator Guide

Operational tasks for users with the `admin` (and where noted,
`legal_expert`) role. Day-to-day system care is in
[operations.md](operations.md).

## User & role management

- Roles: `admin`, `legal_expert`, `user`, `viewer`
  (`backend/core/models.py`). Users currently live in an in-memory dev
  store (`backend/api/routers/auth.py`): `admin`/`admin123` in development,
  plus entries from `LEGAL_AI_DEV_USERS` (`user:pass:role,...`). There is
  no self-service registration endpoint; production user management moves
  to the `users` table in [database.md](database.md).
- Deactivate rather than delete users — audit logs and sessions reference
  `user_id`.
- Rotate `LEGAL_AI_SECRET_KEY` only with a planned token-invalidation
  window (all JWTs become invalid).

## Ingestion operations

```bash
# single document or directory (positional target)
python -m backend.ingestion.pipeline path/to/loi-2026.pdf \
    --name "Loi n° 2026-…" --url "https://www.jo.gouv.bf/..."

# scheduled feeds (gazette, government sites): FreshnessMonitor + crawler
# in backend/ingestion/ feed the same pipeline
```

Ingestion is idempotent: identical content → `skipped_duplicate`; changed
content → new `version` + `document_versions` row. Set the document name
and canonical URL at ingest time, and make sure authority/dates are
attached in the metadata — they drive conflict resolution and timeline
reasoning. Verify with `GET /api/v1/documents/{document_id}` and a
spot-check search (`GET /api/v1/search?q=…`).

## Version & freshness management

- The freshness monitor (Celery beat) polls configured official feeds and
  flags documents whose source published a newer version; review the flags
  and approve re-ingestion.
- Old versions are retained for scenario-date queries — do not delete
  versions that still appear in `document_versions` unless the document was
  ingested by mistake.

## Evaluation runs

Run the golden dataset after every ingestion batch, model change or prompt
change:

```bash
make eval    # python -m backend.evaluation.runner --dataset backend/evaluation/golden_dataset.json
```

Results persist to the `evaluations` table. Review regressions (dropped
groundedness / citation accuracy, new hallucination flags) before promoting
a change. Details: [evaluation.md](evaluation.md).

## Human-review escalation

Answers with confidence below `HUMAN_REVIEW_THRESHOLD` (0.40) — or flagged
by the output guardrail — are returned with
`requires_human_review = true` and queued for the `legal_expert` role:

1. Expert opens the session (trace and evidence are included in
   `ChatResponse` / the session record).
2. Checks the warnings, rejected citations and any unresolved
   `ConflictReport`s.
3. Either approves the answer, edits it, or answers the user directly; the
   decision is written to `audit_logs`.

Unresolved conflicts (`resolved = false`) always warrant expert attention —
the system deliberately surfaces them instead of guessing.
