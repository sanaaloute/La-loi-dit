# Product Roadmap — becoming the reference for Burkinabè law

> **Status (September 2026)** — P0 DONE and verified in production: latency
> ~90 s → ~55 s (~37 s simple questions) via env tuning + local role routing +
> fast lane; corpus dates backfilled (101/105 docs), duplicates removed,
> guarded repeal pass; freshness loop wired (`LEGAL_AI_FRESHNESS_CHECK_ENABLED`);
> eval `--live` mode; Milvus auto-reconnect. P1 backend DONE: sources
> list/articles, freshness events, bookmarks, public share links,
> preferences/memories endpoints. P1 UI (web + mobile) shipped.
> **Next-step batch DONE**: push notifications (Expo Push fan-out on freshness
> events; Android production needs FCM credentials in EAS — user step) and the
> Assemblée Nationale incremental crawler (`scripts/crawl_assemblee.py`, 8 new
> laws ingested on first run). JO/case-law scraping BLOCKED: jo.gouv.bf,
> sgg.gov.bf, conseil-constitutionnel.bf and coursupreme.bf are DNS-dead from
> the production network — needs manual sourcing or a network path.
> Monetization pivots to ads (P2 dropped as written). iOS store build waits on
> the Apple Developer account.

Grounded in a full codebase audit (September 2026: backend pipeline, corpus data,
mobile app, infra). Three investigations fed this: corpus/data quality, latency
profiling, feature inventory.

## The honest frame

"Facebook of law students" is the wrong first target — social features only work
on top of a habit, and the habit in legal is **research**: fast, trustworthy,
complete answers. The winning analog is closer to *Perplexity for Burkinabè law*:
people open it reflexively because it's faster than reading the code and more
reliable than ChatGPT. Your real moat already exists in code: **verified citations,
claim verification, conflict detection, grounded answers** — nobody else in the
region has that. The audit below is ordered by what kills the vision if unfixed.

## P0 — Existential: speed and corpus truth

### 1. Latency: ~160 s/answer is consumer-fatal

At the September audit, a typical run made **6 + N LLM calls** (N =
sub-questions), strictly sequential except retrieval branches, and everything
serialized on one Ollama model. The current implementation instead uses two
fixed role models on the Afrikia provider: `qwen2.5-7b` for structured tasks
and `gpt-oss-20b` for
reasoning/synthesis. The SSE "streaming" showed progress labels; the answer
appeared at the end as a cosmetic typewriter replay (`chat.py:462-488`).

Env-only quick wins (no code change, deploy = restart api container):

| Knob | Effect |
|---|---|
| `LEGAL_AI_RERANK_LLM_ENABLED=false` | −N optional Qwen 2.5 LLM rescore calls (one per branch); heuristic/cross-encoder reranking is separate |
| `LEGAL_AI_CLAIM_LLM_REFINEMENT_ENABLED=false` | −1 Qwen 2.5 call (heuristic grading stays) |
| `LEGAL_AI_ANSWER_MAX_EXCERPT_CHARS=1000`, `LEGAL_AI_ANSWER_MAX_EVIDENCE_PER_SUBQUESTION=3`, `LEGAL_AI_REASONING_MAX_EXCERPT_CHARS=1200` | ~2–3× smaller reasoning/synthesis prompts |
| `LEGAL_AI_LLM_MAX_TOKENS=1500` | caps worst-case generation time |
| `LEGAL_AI_RETRIEVAL_FETCH_K=10` | halves uncached rerank embedding batch |
| FAQ warm-up script hitting `/api/v1/chat` at boot | populates the exact answer cache; zero code |

Expected: 160 s → 30–60 s. Structural follow-ups (code): token-level streaming
from `response_generator` (SSE plumbing already exists), short-circuit
reasoning/reflection for simple FACTUAL/DEFINITION questions (the `direct`
route pattern at `graph.py:94-96` shows how).

### 2. Corpus: the product is only as good as the data

Confirmed problems:

- **~79% of chunks have no dates** (manifest covers 22/105 docs; no date is ever
  extracted from text) → conflicts can't resolve (the 60%-confidence issue we
  dampened last week is a symptom of this).
- **Duplicates indexed twice** (2024 mining code, 2024 constitutional revision,
  defense law — byte-identical SHA256s in `data/versions.json`).
- **Nothing is ever marked `repealed`** (`temporal.py` handles the status; no
  code sets it) — old versions stay "active" forever.
- **Zero case law, zero Journal Officiel, almost no décrets d'application**;
  advertised domains (tax, roads, customs, environment) have no indexed codes.
- The `case_law`/`news`/`government` search workers exist but have **no data
  behind them** (`backend/search/sources.py:89-102` — no endpoints configured).
- **`FreshnessMonitor` and the crawler are instantiated nowhere** — the freshness
  loop documented in `administrator.md` is aspirational; re-ingestion is manual.
- Evaluation runs on **synthetic seed data** — the golden set's "Code général
  des impôts" and Cour suprême case don't exist in the real corpus; 3 tax cases
  pass against phantom documents. Not wired into CI.
- 31/105 PDFs are fully scanned; OCR output is trusted blindly (no quality gate).

Fix plan (this is the real "reference product" work):

1. **Date extraction at ingestion**: parse "Loi n°XXX-YYYY du <date>" patterns
   from the first page text + filename → `publication_date`. ~80% of your PDFs
   are amenable. Immediately makes "latest law wins" fire.
2. **Dedupe + repeal**: drop duplicate versions, mark superseded versions
   `repealed` when a newer same-code version exists (data/versions.json already
   tracks per-article hashes).
3. **Fill the corpus**: Journal Officiel feed (jo.gouv.bf is reachable per the
   crawler config), décrets d'application, Conseil constitutionnel decisions
   (needs a scraper — the source entry has no endpoint).
4. **Wire the freshness loop**: instantiate `FreshnessMonitor` on a schedule
   (nightly cron hitting an admin endpoint, or the crawler daemonized) →
   "nouveautés juridiques" becomes possible.
5. **Eval against the real corpus**: run the golden set against the production
   index weekly; publish the score in the admin dashboard (endpoint exists).

### 3. Concurrency & single-point-of-failure

One Mac Mini behind the Afrikia provider still has a
concurrency ceiling: simultaneous users queue behind provider-side generation
and can blow the
280 s run cap. Before any growth push, set
`web_workers=3` (docs suggest it), confirm the Afrikia deployment's parallel
capacity,
and measure latency. Do not rely on external chat failover or per-tier model
selection. Also: the Milvus fallback never reconnects without a restart
(observed in production) — add a periodic reconnect probe. Backups + alerting
(Prometheus `/metrics` exists; nothing watches it).

## P1 — The habit loop (your "Facebook", translated to legal)

Ranked by impact/effort — most are "one step away" because the backend is built:

1. **Corpus browser + search UI** — `GET /search`, `/articles/{doc}/{article}`,
   `/sources/{doc}` are complete and unused. Students want to *read the code*,
   not only chat. Web page + mobile tab. (Endpoints: `search.py`, `articles.py`,
   `sources.py`.)
2. **"Nouveautés" feed + push notifications** — once the freshness loop runs:
   "loi X modifiée", "nouveau décret". For practicing lawyers this IS the daily
   open. Mobile: `expo-notifications`; backend: freshness state → notification
   table → push tokens per device.
3. **Scenario date picker** — "law in force on <date>" is plumbed end-to-end
   (`ChatRequest.scenario_date` in both clients) with zero UI. A date picker in
   the chat composer = killer practitioner feature, near-zero cost.
4. **Bookmarks + shareable answers** — save answers; public read-only share
   pages (`/citations/{chunk_id}` already resolves citations). Shared pages are
   also your free SEO/acquisition channel.
5. **Onboarding by persona** — student / lawyer / citizen → tailored suggested
   questions (currently 3 hardcoded prompts). First-run retention lever.
6. **Memory/preferences UI** — the memory store exists and shapes answers, but
   `set_preferences` is never called; users can't see or erase what's stored.
   Small screen, big trust win (and a GDPR-ish hygiene point).

## P2 — Free-access cleanup (DONE)

- Removed pricing/subscription pages, billing routes, model locks, and paid
  feature gates on web and mobile. Legacy tier fields no longer affect access.
- Every user can use every drafting template and export format. The server
  assigns fixed local role models; users cannot select models. Authentication,
  admin RBAC, and safety limits remain.
- One shared daily budget (30 requests / 5 000 000 tokens by default) is
  enforced and editable globally in the admin Quotas tab.

## P3 — Later, deliberately

Real social/community (comments on articles, Q&A, expert-verified answers,
cabinet workspaces). Valuable only after the research habit exists; building it
first adds moderation/legal-liability surface with no retention engine beneath.

## Suggested sequence

1. **This week** (ops only): latency env knobs + FAQ cache warm-up. Measure
   before/after on 5 standard questions.
2. **Next 2 weeks**: date extraction + dedupe/repeal pass + re-ingest; eval
   against the real corpus; corpus browser + scenario-date picker UI.
3. **Month 2**: freshness loop live → nouveautés feed + push; shareable answers;
   onboarding; memory UI. Mobile IAP decision.
4. **Month 3+**: case law ingestion, team workspaces, community layer.

## What NOT to spend time on

- More pipeline nodes/verification passes (quality ceiling is data-bound, and
  each node is +latency).
- WebSocket chat (the WS endpoint is built and unused — SSE covers it).
- Social features before P1 lands.
