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

## Tier quotas

- Each tier has a default daily token budget: `gratuit` 1 000 000,
  `pro` 10 000 000, `cabinet` 100 000 000 (`backend/core/catalog.py`).
- Adjust them from the **Quotas** admin tab, or via the API:
  `GET /api/v1/admin/settings/tier-budgets` returns the effective budgets
  plus the built-in defaults; `PATCH /api/v1/admin/settings/tier-budgets`
  merges overrides, e.g. `{"pro": {"daily_token_budget": 20000000}}`.
  Omitted tiers/fields keep their current value; non-positive values are
  rejected. Overrides are persisted as an app setting and survive restarts.

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

## Legal domain management

- The legal-domain taxonomy (slug → French label + keywords) lives in
  `data/legal_domains.json`; the French labels are what the UI displays.
- Add or delete domains from the admin UI (Documents tab, « Domaines
  juridiques ») or via the API: `GET /api/v1/admin/domains`,
  `POST /api/v1/admin/domains`, `DELETE /api/v1/admin/domains/{slug}`.
  Slugs must match `[a-z0-9_]+`; deleting a domain is refused while a
  `legal_docs/{slug}` folder still holds documents.

## Upload limits

- Upload size caps: 100 MB for admins, 25 MB for regular users
  (`max_upload_bytes_admin` / `max_upload_bytes_user` in
  `backend/core/config.py`).
- nginx must accept those bodies: `client_max_body_size 100m` in
  `docker/nginx/nginx.conf` and in the host config
  `docker/host-nginx/yawoto.neobytech.net.conf`. On the server, (re)install
  and reload the host config with `scripts/install-nginx-config.sh` after
  any change.

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
