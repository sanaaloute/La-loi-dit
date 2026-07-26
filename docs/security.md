# Security

## Authentication & authorization

- **JWT** — HS256 (`LEGAL_AI_JWT_ALGORITHM`), signed with
  `LEGAL_AI_SECRET_KEY`, expiry `LEGAL_AI_JWT_EXPIRE_MINUTES` (default 60).
  Passwords hashed with bcrypt.
- **RBAC** — four roles (`backend/core/models.py`):
  `admin` (user management, ingestion, evaluation),
  `legal_expert` (ingestion, human-review queue),
  `user` (chat, own sessions),
  `viewer` (read-only).

## Rate limiting

Two layers: Nginx (`limit_req_zone`, 10 r/s per IP, burst 20, 429 on
excess) and an in-app limiter (`LEGAL_AI_RATE_LIMIT_PER_MINUTE`, default 30
per user).

## Guardrails

- **Input** (`backend/guardrails/input_guard.py` via the
  `input_guardrail` node): prompt injection, jailbreak, sensitive-info,
  role-hijacking and tool-abuse detection. Blocked queries terminate in the
  `refusal` node with a bilingual explanation and are audit-logged;
  sanitizable ones continue with the sanitized query.
- **Output** (`output_guardrail` node): unsafe-legal-advice detection,
  confidence thresholds (warning < 0.55, human review < 0.40), mandatory
  legal disclaimer on every answer.
- **Citation verification**: fabricated citations are programmatically
  removed — the model cannot cite what was not retrieved.

## Sandbox isolation

Agent tools (e.g. a code-execution helper for date arithmetic) run in the
`backend/sandbox/` subsystem: no network access, CPU/time limits, a
restricted builtins set, and no filesystem outside a scratch directory.
Tools are allow-listed; anything not registered is unreachable from the
LLM.

## Audit logging

Security-relevant events — logins, blocked queries, ingestion actions, role
changes, evaluation runs — are written to the `audit_logs` table with user,
action, resource, detail and IP (see [database.md](database.md)).

## Secrets handling

- All secrets come from `LEGAL_AI_*` env vars / `.env` (gitignored);
  nothing is hard-coded.
- LLM API keys live only in the server environment; they are never exposed
  through the API, traces (Langfuse keys are server-side) or logs.
- The default `secret_key` is `change-me-in-production` — rotate before any
  real deployment, together with Postgres/Grafana/Langfuse credentials.

## Threat model & mitigations

| Threat | Mitigation |
|---|---|
| Prompt injection via user query | input guardrail patterns + refusal path; bounded retries prevent loop-based abuse |
| Jailbreak / role hijacking | same guardrail flags (`jailbreak`, `role_hijacking`) |
| Hallucinated legal advice | grounded-answer policy, citation verification, confidence thresholds, human-review escalation, disclaimer |
| Data exfiltration via tools | sandboxed tools, allow-list, no network in sandbox |
| Credential theft | env-only secrets, bcrypt password hashing, short-lived JWTs |
| DoS / cost abuse | Nginx + app rate limits, bounded retry budgets (max 1), LLM timeouts (`LEGAL_AI_LLM_TIMEOUT_SECONDS`) |
| Supply-chain / image tampering | pinned base images, non-root container user, CI build checks |
| Stale legal information presented as current | document versioning, effective-date metadata, freshness monitor (see [operations.md](operations.md)) |
