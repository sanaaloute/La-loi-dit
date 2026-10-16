# Administrator Guide

Operational tasks for users with the `admin` (and where noted,
`legal_expert`) role. Day-to-day system care is in
[operations.md](operations.md).

## User & role management

- Roles: `admin`, `legal_expert`, `user`, `viewer`
  (`backend/core/models.py`). Regular accounts are stored in the `users`
  table and can self-register with `role=user`. The in-memory dev store
  (`backend/api/routers/auth.py`) still provides `admin`/`admin123` in
  development and accepts `LEGAL_AI_DEV_USERS` (`user:pass:role,...`);
  production bootstraps its single admin through that variable. These roles
  remain access controls even though subscription tiers no longer exist.
- Prefer **suspension** over deletion for audit retention. Admins can suspend,
  reactivate, or permanently delete accounts in the Users tab. Suspension and
  deletion revoke all active sessions immediately; deletion also removes the
  account's user-owned database rows and chat/memory history.
- Safeguards: an admin cannot suspend or delete themselves, and the last
  active admin cannot be suspended or deleted. Reactivation has no such
  restriction. API routes: `POST /api/v1/admin/users/{id}/suspend`,
  `POST /api/v1/admin/users/{id}/reactivate`, and
  `DELETE /api/v1/admin/users/{id}`.
- Rotate `LEGAL_AI_SECRET_KEY` only with a planned token-invalidation
  window (all JWTs become invalid).

## Daily quotas

- All features and models are free for every user. Legacy `gratuit` / `pro` /
  `cabinet` values remain in old rows and tokens but no longer change access.
- Every account shares the same default daily budget: **30 requests and
  5 000 000 tokens** (`backend/core/catalog.py`). Per-minute/per-second
  anti-abuse limits still apply.
- Adjust the global budget from the **Quotas** admin tab, or via the API:
  `GET /api/v1/admin/settings/daily-budgets` returns the effective budget and
  built-in default; `PATCH /api/v1/admin/settings/daily-budgets` merges a flat
  override, e.g. `{"daily_token_budget": 20000000, "daily_request_budget": 30}`.
  Omitted fields keep their current value; negative values are rejected, and
  `0` lifts that budget (unlimited). Overrides use the `daily_budgets` app
  setting and survive restarts.

## Ingestion operations

```bash
# single document or directory (positional target)
python -m backend.ingestion.pipeline path/to/loi-2026.pdf \
    --name "Loi n° 2026-…" --url "https://www.jo.gouv.bf/..."

# corpus maintenance (host; dry-run by default, --apply writes):
python scripts/corpus_fix.py dates    # backfill publication_date from first page/filename
python scripts/corpus_fix.py dedupe   # remove byte-identical documents indexed twice
python scripts/corpus_fix.py repeal   # mark superseded versions status=repealed (guarded:
                                      # same-law dupes + explicit abrogation only)

# scheduled freshness polling (RSS/ETag of official sources): runs inside the
# API lifespan when LEGAL_AI_FRESHNESS_CHECK_ENABLED=true (default interval 24h,
# LEGAL_AI_FRESHNESS_INTERVAL_HOURS); detected changes land in
# data/freshness_events.jsonl and are served at GET /api/v1/freshness/events.
# NOTE: the crawler (backend/ingestion/crawler.py) is still NOT wired — new
# documents are ingested manually or via ingest_on_startup.
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
