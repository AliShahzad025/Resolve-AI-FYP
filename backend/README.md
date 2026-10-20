# Resolve AI backend — Stages 1–2

FastAPI + SQLAlchemy + Alembic API for your existing PostgreSQL schema in database `ResolveAI`. Stage 1 implements account registration/sign-in/sign-out, owner store setup, store memberships, and owner-only member administration. Stage 2 adds Shopify OAuth, encrypted/refreshable offline credentials, Admin GraphQL order import, verified/idempotent order/refund webhooks, normalized PostgreSQL order/customer/event records, and store-scoped order reads. Risk scoring, complaint handling, policy retrieval, and WhatsApp delivery remain later stages.

## Configure PostgreSQL

The database and full application schema must already exist. Set `DATABASE_URL` in `.env` to the exact PostgreSQL database name and credentials. The example uses `resolveAi` as provided in the original request. URL-encode special characters in the password. Set `JWT_SECRET` to a private random value of at least 32 characters. Do not commit `.env`.

This project does not recreate the schema you already installed. Additive migrations make Shopify connection fields nullable, add `auth_sessions`, and add the private Shopify credential and one-time OAuth state tables. Existing business data is preserved. Stage 2 uses the existing `customers`, `orders`, and `source_events` tables.

## Configure a Shopify app for Stage 2

Create a Shopify app in the Dev Dashboard and configure its OAuth callback to exactly match `https://YOUR_PUBLIC_BACKEND/api/shopify/callback`. Set `SHOPIFY_API_KEY`, `SHOPIFY_API_SECRET`, and the same public HTTPS backend address in `SHOPIFY_APP_BASE_URL` and `SHOPIFY_WEBHOOK_BASE_URL`. For local development, expose the backend with a stable HTTPS tunnel and use that address. A localhost callback cannot receive Shopify redirects or webhooks.

Generate a Fernet encryption key once and store it as `SHOPIFY_ENCRYPTION_KEY`. For example, with the project Python environment: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`. Keep it private and persistent; rotating it without re-encrypting stored credentials requires every store to reconnect. The app requests `read_orders` and `read_customers`. Shopify may require protected customer-data approval to return names, email, phone, or shipping location. Initial import is limited to Shopify's default recent-order window (60 days); older records require Shopify to grant `read_all_orders` and an explicit scope change.

After the Shopify app values are set in `.env`, apply the additive migration with `alembic upgrade head`. In the UI, sign in as the owner, open **Settings → Integrations**, enter the `*.myshopify.com` domain, and select **Connect Shopify**. The owner approves the requested scopes in Shopify. Use **Sync orders now** for an initial paginated import. Shopify sends subsequent order/refund events to the configured HTTPS webhook URL. Webhook signatures are verified before data is accepted; repeated deliveries are ignored by their Shopify delivery ID.

## Install and run

From `backend/` in PowerShell:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
# Adopt the existing schema as the migration baseline (run once per database)
alembic stamp 0001_existing_schema
# Apply additive Stage 1 and 2 migrations
alembic upgrade head
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

`GET /api/health` checks the live database connection. Open `http://127.0.0.1:8000/docs` for the API.

## Stage 1 API

- `POST /api/auth/register` creates an owner, store, and owner membership, then signs the owner in.
- `POST /api/auth/login`, `GET /api/auth/me`, and `POST /api/auth/logout` manage database-backed revocable bearer sessions. If an account has memberships in several stores, sign-in asks the user to choose one; each token is bound to that store.
- `GET /api/store` returns only the authenticated user's active store.
- `GET /api/members` lists memberships in that store.
- `POST /api/members` adds a manager/agent membership; owner permission required. For a new email, the owner sets an initial password; an existing account can be added without resetting its password.
- `PATCH /api/members/{membership_id}?active=false` enables/disables a membership; owner only.
- `PATCH /api/members/{membership_id}/role?role=Agent` changes a manager/agent role; owner only.

All membership reads and writes are scoped to the store in the caller's session on the server. For a new account, share the initial password with the teammate through a secure channel.

## Stage 2 Shopify API

- `GET /api/shopify/status` returns the current store's connection and webhook state.
- `POST /api/shopify/connect` (owner) begins Shopify's authorization-code grant.
- `GET /api/shopify/callback` validates Shopify's HMAC and one-time state before saving encrypted offline credentials.
- `POST /api/shopify/sync?limit=250` (owner, maximum 250 per request) imports recent orders from the Shopify Admin GraphQL API.
- `GET /api/orders` returns only the authenticated store's saved order records. Scores are explicitly unavailable until the risk-scoring stage.
- `DELETE /api/shopify/connect` (owner) removes local credentials and disconnects the store.
- `POST /api/webhooks/shopify` verifies Shopify HMAC and idempotently processes order, refund, and uninstall events.

Shopify API calls target the pinned stable `2026-10` GraphQL version. Webhook delivery requires a public HTTPS backend. No financial/return action is performed by this integration.
