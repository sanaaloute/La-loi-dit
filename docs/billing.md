# Billing (Paddle)

Subscription billing is handled by **Paddle Billing**. It is **disabled by
default** — with `LEGAL_AI_PADDLE_ENABLED=false` the platform is fully
functional (tiers can still be granted manually via the user store) and no
network call to Paddle ever happens.

Architecture: `backend/billing/base.py` defines the `BillingProvider`
protocol (checkout creation + webhook normalization into a provider-neutral
`BillingEvent`). `paddle.py` is the first implementation; **CinetPay** (or
any other provider) plugs in as a second file implementing the same
protocol — nothing downstream changes.

## Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `LEGAL_AI_PADDLE_ENABLED` | `false` | Master switch (billing API + webhook active only when `true` and the config below is complete) |
| `LEGAL_AI_PADDLE_ENV` | `sandbox` | `sandbox` → `sandbox-api.paddle.com`, `production` → `api.paddle.com` |
| `LEGAL_AI_PADDLE_API_KEY` | — | API key (Paddle dashboard → Developer tools → Authentication) |
| `LEGAL_AI_PADDLE_WEBHOOK_SECRET` | — | Webhook signing secret (Developer tools → Notifications) |
| `LEGAL_AI_PADDLE_PRICE_PRO` | — | Price id (`pri_...`) for the Pro monthly/annual price |
| `LEGAL_AI_PADDLE_PRICE_CABINET` | — | Price id for the Cabinet price |
| `LEGAL_AI_PADDLE_CHECKOUT_SUCCESS_URL` | `http://localhost:3000/tarifs?success=1` | Checkout completion redirect |
| `LEGAL_AI_PADDLE_CHECKOUT_CANCEL_URL` | `http://localhost:3000/tarifs?canceled=1` | Checkout cancel redirect |

`settings.billing_enabled` is only true when the switch is on AND the API
key and both price ids are set — partial config behaves as disabled.

## Sandbox setup

1. Create a sandbox account at `sandbox-vendors.paddle.com`.
2. Create two products/prices (Pro, Cabinet) and copy the `pri_...` ids.
3. Generate an API key and set `LEGAL_AI_PADDLE_API_KEY`.
4. Register a notification destination (Developer tools → Notifications)
   pointing at `https://<your-host>/api/v1/billing/webhook`, subscribing at
   minimum to `transaction.completed`, `subscription.activated`,
   `subscription.updated`, `subscription.canceled`, `subscription.past_due`.
   Copy the webhook secret into `LEGAL_AI_PADDLE_WEBHOOK_SECRET`.
5. For local testing, expose your dev server with
   `ngrok http 8000` and register the `https://<id>.ngrok.io/api/v1/billing/webhook`
   URL as the notification destination.

## How it works

- `POST /api/v1/billing/checkout` creates a Paddle **transaction** for the
  requested tier price, tagged with `custom_data.user_id`, and returns the
  hosted checkout URL (`data.checkout.url`). The user completes payment on
  Paddle's hosted page.
- Paddle then calls `POST /api/v1/billing/webhook` (unauthenticated — the
  HMAC `Paddle-Signature` header over the raw body is the credential; stale
  timestamps > 5 min are rejected).
- Events are normalized and applied idempotently:
  - `transaction.completed` / `subscription.activated` → tier upgraded,
    status `active`, Paddle customer/subscription ids stored;
  - `subscription.updated` → renewed (carries `cancel_at_period_end` when a
    cancellation is scheduled);
  - `subscription.canceled` → tier kept until `current_period_end` when it
    lies in the future, otherwise immediate downgrade to `gratuit`;
  - `subscription.past_due` → tier kept, status `past_due`.
- `GET /api/v1/billing/subscription` exposes `{tier, status,
  current_period_end, cancel_at_period_end}` for the account page; it works
  with billing disabled (status `none`).

The subscription state lives on the `users` row (`paddle_customer_id`,
`paddle_subscription_id`, `subscription_status`,
`subscription_period_end`, `subscription_cancel_at_period_end`). The columns
are added by best-effort idempotent `ALTER TABLE` statements in the user
store bootstrap — no manual migration needed on SQLite or Postgres.

## Going to production

Switch `LEGAL_AI_PADDLE_ENV=production`, replace the API key, price ids and
webhook secret with the live dashboard values, and register the production
webhook URL. Sandbox and live credentials are entirely separate — never
reuse sandbox price ids in production.
