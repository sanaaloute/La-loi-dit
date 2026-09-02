# Mobile App Strategy (Android & iOS)

Assessment of how native mobile apps can connect to this codebase, what already works, what blocks, and a recommended path. Based on a full inspection of `backend/`, `frontend/`, `docker/`, and `docker-compose.yml` (September 2026).

## TL;DR

The backend **is already an API gateway in every meaningful sense**: a standalone FastAPI app exposing the entire product under a versioned `/api/v1` prefix, with stateless JWT Bearer auth (no cookies, no CSRF), JSON contracts, and mobile-viable streaming. A mobile app can talk to it **without any backend rewrite**. What is missing is (1) a stable public route that reaches the API directly instead of through the Next.js proxy, (2) fixes for two mobile-hostile auth behaviors, and (3) a payments story that complies with App Store / Play rules.

> **Implementation status (September 2026) — Phase 0 backend: DONE.** `X-Device-Id` mobile session binding + per-device-class sessions, `POST /auth/logout`, `DELETE /auth/me`, token-based password reset with optional SMTP, production-hiding of `/docs`, and auth on `/export/*` are implemented and tested (`tests/test_mobile_sessions.py`). The Expo app lives in `mobile/`. Remaining: the deploy-time `api.` subdomain ingress (see [deployment-macmini.md](deployment-macmini.md#public-api-endpoint-for-mobile-apps)) and Phase 2 (IAP, push).

## 1. Current state

### API surface (`backend/api/main.py`)

- `create_app()` at `backend/api/main.py:132`, served by uvicorn (`docker-compose.yml:39`).
- 14 routers, all mounted under `/api/v1` (`backend/api/main.py:238-253`): `auth`, `chat`, `search`, `documents`, `draft`, `export`, `billing`, `models`, `usage`, `articles`, `citations`, `sources`, `legal`, `admin`.
- Root probes outside the prefix: `GET /health`, `GET /ready`, `GET /metrics`.
- JSON everywhere; multipart only for uploads (`POST /documents`, `POST /chat/transcribe`, admin upload). Errors are uniformly `{"detail": "..."}`.

### Auth — already mobile-shaped

- **Pure JWT Bearer**, HS256, no cookies anywhere (`backend/security/jwt.py:32-56`). Header: `Authorization: Bearer <token>`.
- `POST /api/v1/auth/register` (email or phone + password), `POST /api/v1/auth/token` (login), `POST /api/v1/auth/refresh` (sliding renewal of the same token), `GET /api/v1/auth/me` (profile + tier + features).
- Token TTL 60 min default (`LEGAL_AI_JWT_EXPIRE_MINUTES`); the web client already implements proactive refresh at T-5 min (`frontend/lib/api.ts:430-448`) — the same logic ports 1:1 to mobile.
- RBAC: VIEWER < USER < LEGAL_EXPERT < ADMIN (`backend/security/rbac.py:17-22`).

### Streaming — usable natively, with caveats

- Primary: `GET /api/v1/chat/stream` (SSE, `backend/api/routers/chat.py:728-790`). Auth via header (EventSource can't send headers — the web app uses `fetch` + `ReadableStream` for this reason; native apps use OkHttp/URLSession and parse `data:` frames manually).
- Frame protocol: `node_start` / `update` / `delta` (typewriter) / `final` (authoritative `ChatResponse`) / `cancelled` / `error`, plus `: hb` comment heartbeats every 10 s. Hard run cap 280 s.
- Disconnect resilience is built in: the server finishes the run and persists it; the client recovers via `GET /chat/sessions/{id}/run` + `GET /chat/sessions/{id}` (`chat.py:578-640`).
- Fallback: `WS /api/v1/ws/chat` (`?token=` auth) exists but is **not persisted to history** — prefer SSE.
- Non-streaming `POST /chat` can take up to 280 s — see the Cloudflare hazard below.

### How the API is exposed today

```
internet → https://yawoto.neobytech.net (Cloudflare → cloudflared tunnel)
        → 127.0.0.1:3100 → Next.js frontend container
        → same-origin rewrite /backend-api/:path* → http://api:8000/:path*  (frontend/next.config.ts:13-19)
```

- The API container binds only `127.0.0.1:8000` (`docker-compose.yml:47-48`). **There is no direct public route to the API** — everything goes through the Next.js server-side rewrite, which buffers SSE and delivers frames in bursts (`backend/api/main.py:154-156`; the web client works around this with a 15 s silence watchdog + history polling, `frontend/lib/api.ts:696-756`).
- A ready-made direct-exposure config already exists but is dormant: `docker/nginx/nginx.conf` has `proxy_buffering off` and 600 s timeouts for `/api/v1/chat/stream` and WS upgrade handling (the nginx service is not in the current `docker-compose.yml`).

## 2. What blocks or complicates a mobile app

Ordered by severity:

1. **Device fingerprint bound to client IP** (`backend/security/sessions.py:35-46`). The fingerprint `sha256(user-agent|accept-language|client IP)` is checked on every authenticated request (`backend/api/middleware.py:131-141`). Mobile networks change IPs constantly (CGNAT, Wi-Fi↔cellular handover) → the app would get randomly logged out with 401 "session invalidated by another login". **This must be relaxed before launch** (e.g. skip the IP component for tokens issued with a `client=mobile` claim, or fingerprint on a stable device ID instead).
2. **Single session per user** (`LEGAL_AI_SINGLE_SESSION_PER_USER=true` default, `backend/core/config.py:133`; enforced via Redis `active_session:{user_id}`). Logging in on the phone kills the web session and vice versa. Either allow N concurrent sessions, or scope the single-session rule per device class.
3. **No direct API route.** Depending on the Next.js rewrite means mobile streaming inherits the buffering problem and an unnecessary hop. Expose `api-yawoto.neobytech.net` (cloudflared ingress rule; first-level subdomain because Universal SSL covers `*.neobytech.net` only) so the app hits the API container directly.
4. **Payments vs. store policies.** Tiers pro/cabinet unlock digital features via a Paddle **web checkout** (`POST /billing/checkout` → hosted URL). Selling digital goods inside an iOS/Android app through a web checkout conflicts with Apple 3.1.1 / Google Play payments policy. Options: (a) implement StoreKit / Play Billing and map entitlements onto the existing `tier` field via new backend verification endpoints; (b) make the apps "login only" (no purchase path in-app) — allowed but hurts conversion; (c) external-link entitlement flows where eligible. Note prices/tier features are currently hardcoded client-side in `frontend/app/tarifs/page.tsx:24-74` — move them behind an API when adding IAP.
5. **Cloudflare 100 s TTFB limit.** Runs capped at 280 s die with error 524 on non-streaming endpoints (`docs/deployment-macmini.md:153-160`). Mobile must use SSE (heartbeat keeps it safe) or POST + `/run` polling. Never rely on long synchronous responses.
6. **App Store account requirements.** Apple 5.1.1(v) requires **in-app account deletion** if the app offers account creation. The backend has no delete-account, password-reset, email/phone-verification, or logout/revoke endpoints. Minimum to add: `DELETE /auth/me` (or equivalent) and password reset. (No social login exists, so Sign-in-with-Apple is not triggered.)
7. **Minor hardening before opening the API to the public internet directly:** `/docs`, `/redoc`, `/openapi.json` are exposed in production (FastAPI defaults — disable via env), and `POST /export/{pdf|word|csv|md}` currently has **no auth dependency** (`backend/api/routers/export.py:464-469`).
8. **CORS**: `allow_credentials=True` with origins from `LEGAL_AI_CORS_ORIGINS` (`backend/api/main.py:157-163`). Irrelevant for pure native clients (no Origin header); only matters if the app embeds WebViews (Capacitor/Ionic), in which case the webview origin must be added.

## 3. What works as-is (no backend changes)

- Register / login / refresh / `GET /auth/me`.
- Full chat: SSE stream with heartbeats, cancel (`POST /chat/cancel`), feedback, voice transcription (`POST /chat/transcribe`, multipart audio, 30 s client-side cap), sessions list/get/delete, run-status recovery polling.
- `GET /models` (per-tier `allowed` flags), `GET /usage/me` (budget + 30-day history), `GET /billing/config` + `/billing/subscription` (read-only status).
- Drafting (`/draft/templates`, `POST /draft`) and export (`/export/*`) — mobile replaces the browser download with a share-sheet/save-file.
- Search/citation endpoints the web UI doesn't even use yet (`/search`, `/articles`, `/citations/{chunk_id}`, `/sources`) — free features for the app.
- Rate limiting is per-user once authenticated; fine for mobile.

## 4. Client logic the mobile app must reimplement

The backend does not provide these; today they live in `frontend/lib/api.ts` (1255 lines, the full API surface in one file — use it as the porting spec) and `frontend/components/ChatWindow.tsx`:

- **Chat resilience state machine** (`ChatWindow.tsx:584-723`): client-generated `session_id` (UUID) before sending; 15 s silence watchdog; on stream failure → check `/chat/sessions/{id}/run`, poll history up to ~10 min, match answer to the exact sent query; 3 consecutive "not running" → fail; suspend/resume + network-change recovery. This is the single most complex port.
- SSE frame parser (split on `\n\n`, JSON-parse `data:` lines, ignore `: hb` comments).
- Token lifecycle: parse `exp`, refresh at T-5 min with a single in-flight refresh, broadcast auth changes; secure storage (Keychain / Keystore instead of localStorage).
- Pipeline timeline: 18 hardcoded nodes with French labels (`api.ts:264-283`) + step descriptions.
- Markdown rendering of answers (react-markdown on web), confidence badge thresholds (0.55 / 0.4), citation/evidence panels (citations are a structured list, not inline links).
- French-only UI, `language: "fr"` in chat requests, `fr-FR` formatting.

## 5. Recommended architecture

```
                 ┌───────────────────────────────┐
                 │  api-yawoto.neobytech.net     │  cloudflared ingress rule
 mobile app ────▶│  straight to the API —        │  (first-level subdomain:
 (React Native)  │  no Next.js proxy             │  Universal SSL covers only
                 │                               │  *.neobytech.net)
                 └───────────────┬───────────────┘
                                 ▼
                        api container :8000   ← also serves web via Next rewrite
```

- **Do not add a new gateway service.** The FastAPI app already is the gateway. The only new infra is a DNS name + ingress rule + (optionally) the existing nginx config for clean SSE/WS handling. Keep the web app on its current path untouched.
- **App stack: React Native (Expo)** is the natural fit — the team already owns React 19 + TypeScript, and `frontend/lib/api.ts` ports almost mechanically into a shared TS API module (swap localStorage → `expo-secure-store`, `fetch` stream → `expo-fetch`/RN networking SSE or a small native SSE lib, blob download → share sheet). Flutter is equally viable if the team prefers it; the API imposes no constraint either way. Avoid Capacitor/WebView wrappers — the product is chat-streaming-heavy and would inherit the CORS/EventSource limitations above.
- **IAP (if selling in-app)**: add backend endpoints `POST /billing/iap/verify` (App Store / Play receipt → set tier) and keep Paddle for web only. Tier resolution already flows through `GET /auth/me` → `features`, so the app needs no separate entitlement logic.

## 6. Suggested phases

**Phase 0 — backend readiness (small, no app yet):**
1. Relax device fingerprint for mobile clients (drop IP component or use a client-supplied device ID) — `backend/security/sessions.py`, `backend/api/middleware.py`.
2. Decide session policy: per-device-class single session, or raise the limit — `LEGAL_AI_SINGLE_SESSION_PER_USER` + `backend/security/sessions.py`.
3. Expose `api.` subdomain via cloudflared ingress (+ optionally the dormant nginx config); verify SSE unbuffered end-to-end through Cloudflare.
4. Harden: hide `/docs`/`/openapi.json` in production; add auth to `/export/*`.
5. Add `DELETE /auth/me` and password-reset endpoints (store compliance + baseline product gap).

**Phase 1 — MVP app:** auth screens, chat with SSE streaming + full recovery state machine, history, model picker, usage/account screens, voice input. Hardcode French. Feature-gate via `GET /auth/me` features exactly as the web does.

**Phase 2 — parity + monetization:** drafting + export (share sheet), IAP for pro/cabinet (or web-checkout link-out where policy permits), push notifications if desired (needs new backend endpoints), possibly document search/viewer using the unused endpoints.

## 7. Doc drift noticed while inspecting (worth fixing)

- `docs/api.md` still claims "no public registration endpoint" — stale; `/auth/register` exists.
- `frontend/next.config.ts:5` comment says "the backend does not enable CORS" — stale; CORS middleware is active (`backend/api/main.py:157-163`).
- The legacy `docker/host-nginx/yawoto.neobytech.net.conf` proxies `/api/v1/*` to the **frontend** port 3000 — do not copy that pattern; use `docker/nginx/nginx.conf` instead.
