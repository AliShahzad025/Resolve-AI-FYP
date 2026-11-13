# Resolve AI backend — Stages 1–4

FastAPI + SQLAlchemy + Alembic API for your existing PostgreSQL schema in database `ResolveAI`. Stage 1 implements account registration/sign-in/sign-out, owner store setup, store memberships, and owner-only member administration. Stage 2 implements manual case handling: store-scoped case lists, assignment, validated status transitions, staff replies, and append-only audit history. Shopify is tracked separately as Stage 4; its backend integration foundation exists, while live app authorization and end-to-end testing require provider credentials and a public HTTPS callback/webhook address.

## Configure PostgreSQL

The database and full application schema must already exist. Set `DATABASE_URL` in `.env` to the exact PostgreSQL database name and credentials. The example uses `resolveAi` as provided in the original request. URL-encode special characters in the password. Set `JWT_SECRET` to a private random value of at least 32 characters. Do not commit `.env`.

This project does not recreate the schema you already installed. Additive migrations make Shopify connection fields nullable, add `auth_sessions`, and add the private Shopify credential and one-time OAuth state tables. Existing business data is preserved. Manual case handling reuses the existing `customers`, `complaint_cases`, `messages`, `complaint_analyses`, and `audit_entries` tables; it does not require a new migration.

## PostgreSQL setup and GUI

Install PostgreSQL using one of these options:

- macOS: [Postgres.app](https://postgresapp.com/) or Homebrew PostgreSQL.
- Windows: [PostgreSQL installer](https://www.postgresql.org/download/windows/).

Start the PostgreSQL server, then create the project database:

```bash
createdb -h 127.0.0.1 -p 5432 -U postgres resolveAi
```

If `createdb` is unavailable, run this in `psql` or a PostgreSQL GUI:

```sql
CREATE DATABASE "resolveAi";
```

For a graphical database browser, use [DBeaver](https://dbeaver.io/download/), [Postico](https://eggerapps.at/postico/), or [pgAdmin](https://www.pgadmin.org/download/). Use these connection values:

```text
Host: 127.0.0.1
Port: 5432
Database: resolveAi
User: the PostgreSQL user created during installation
Password: that user's password
```

The most useful manual case tables are `complaint_cases`, `messages`, `audit_entries`, `users`, `memberships`, and `customers`. To check the server from a terminal:

```bash
pg_isready -h 127.0.0.1 -p 5432
```

Do not commit `backend/.env`, and do not use `DROP DATABASE` to stop PostgreSQL. Stopping the server preserves all data.

## Stage 4 — Shopify integration

Shopify integration is a separate workstream from Stage 2 manual case handling. The backend currently includes the foundation for OAuth, encrypted offline credentials, Admin GraphQL order synchronization, customer/order records, and verified/idempotent order, refund, and uninstall webhooks.

Live Shopify connection and end-to-end verification are still in progress. They require:

- A Shopify app with the required API credentials.
- A public HTTPS backend URL for OAuth callbacks and webhooks.
- Shopify approval for any protected customer-data scopes.
- A development shop for testing order, return, and refund events.

### Configure a Shopify app

Create a Shopify app in the Dev Dashboard and configure its OAuth callback to exactly match `https://YOUR_PUBLIC_BACKEND/api/shopify/callback`. Set `SHOPIFY_API_KEY`, `SHOPIFY_API_SECRET`, and the same public HTTPS backend address in `SHOPIFY_APP_BASE_URL` and `SHOPIFY_WEBHOOK_BASE_URL`. For local development, expose the backend with a stable HTTPS tunnel and use that address. A localhost callback cannot receive Shopify redirects or webhooks.

Generate a Fernet encryption key once and store it as `SHOPIFY_ENCRYPTION_KEY`. For example, with the project Python environment: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`. Keep it private and persistent; rotating it without re-encrypting stored credentials requires every store to reconnect. The app requests `read_orders` and `read_customers`. Shopify may require protected customer-data approval to return names, email, phone, or shipping location. Initial import is limited to Shopify's default recent-order window (60 days); older records require Shopify to grant `read_all_orders` and an explicit scope change.

After the Shopify app values are set in `.env`, apply the additive migration with `alembic upgrade head`. In the UI, sign in as the owner, open **Settings → Integrations**, enter the `*.myshopify.com` domain, and select **Connect Shopify**. The owner approves the requested scopes in Shopify. Use **Sync orders now** for an initial paginated import. Shopify sends subsequent order/refund events to the configured HTTPS webhook URL. Webhook signatures are verified before data is accepted; repeated deliveries are ignored by their Shopify delivery ID. This flow should be treated as development/integration testing until a real Shopify app and shop have been configured.

## Install and run

From `backend/` on macOS/Linux:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
alembic upgrade head
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

If the database is already at `0003_shopify_order_sync`, do not run `alembic stamp` again. That command is only for a fresh database where the existing schema has not yet been adopted as the migration baseline.

`GET /api/health` checks the live database connection. Open `http://127.0.0.1:8000/docs` for the API.

### Windows PowerShell

```powershell
cd backend
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m alembic upgrade head
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

If PowerShell blocks activation, run PowerShell as the current user and execute `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once, then retry activation. Keep the backend terminal open while using the frontend.

### macOS/Linux

```bash
cd backend
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m alembic upgrade head
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Open a second terminal for the frontend:

```bash
cd frontend
npm install
npm run dev
```

On Windows PowerShell, use the same frontend commands after `cd frontend`. Stop FastAPI with `Ctrl+C`; stop PostgreSQL from Postgres.app, Windows Services, or the PostgreSQL installer tools. Starting it again does not remove the database.

## Stage 1 API

- `POST /api/auth/register` creates an owner, store, and owner membership, then signs the owner in.
- `POST /api/auth/login`, `GET /api/auth/me`, and `POST /api/auth/logout` manage database-backed revocable bearer sessions. If an account has memberships in several stores, sign-in asks the user to choose one; each token is bound to that store.
- `GET /api/store` returns only the authenticated user's active store.
- `GET /api/members` lists memberships in that store.
- `POST /api/members` adds a manager/agent membership; owner permission required. For a new email, the owner sets an initial password; an existing account can be added without resetting its password.
- `PATCH /api/members/{membership_id}?active=false` enables/disables a membership; owner only.
- `PATCH /api/members/{membership_id}/role?role=Agent` changes a manager/agent role; owner only.

All membership reads and writes are scoped to the store in the caller's session on the server. For a new account, share the initial password with the teammate through a secure channel.

### Stage 4 Shopify API

- `GET /api/shopify/status` returns the current store's connection and webhook state.
- `POST /api/shopify/connect` (owner) begins Shopify's authorization-code grant.
- `GET /api/shopify/callback` validates Shopify's HMAC and one-time state before saving encrypted offline credentials.
- `POST /api/shopify/sync?limit=250` (owner, maximum 250 per request) imports recent orders from the Shopify Admin GraphQL API.
- `GET /api/orders` returns only the authenticated store's saved order records. Scores are explicitly unavailable until the risk-scoring stage.
- `DELETE /api/shopify/connect` (owner) removes local credentials and disconnects the store.
- `POST /api/webhooks/shopify` verifies Shopify HMAC and idempotently processes order, refund, and uninstall events.

Shopify API calls target the pinned stable `2026-10` GraphQL version. Webhook delivery requires a public HTTPS backend. No financial/return action is performed by this integration.

## Stage 3 — WhatsApp connection

Customers message the store's WhatsApp Business number; each message is verified, saved, and attached to that customer's open case (or opens a new one). Staff replies are queued, delivered through the Meta WhatsApp Cloud API, and their delivery status (sent → delivered → read, or failed) is tracked from Meta's callbacks.

### How it works

- **Incoming:** `POST /api/webhooks/whatsapp` checks the `X-Hub-Signature-256` HMAC with `WHATSAPP_APP_SECRET`, then saves a `source_events` row and the `messages` row in one transaction **before** returning 200. Meta's message ID (`wamid`) deduplicates repeated deliveries (`source_events` unique key + `ux_message_provider`). The customer is matched by phone number (a Shopify customer with the same phone is reused); a customer's next message joins their latest unresolved case, and an `Awaiting customer` case moves back to `In progress`.
- **Outgoing:** `POST /api/cases/{id}/messages` saves the reply as `queued` and adds a `message_outbox` row in the same transaction. A background worker (started with the API) claims due jobs with `FOR UPDATE SKIP LOCKED`, sends them, stores the returned `wamid`, and marks the reply `submitted`. Temporary errors (network, 5xx, throttling) retry up to 5 times with backoff; permanent errors (e.g. the 24-hour customer service window, a recipient not on the test number's allowed list, an invalid token) mark the reply `failed` with a readable reason. Failed replies can be resent from the conversation.
- **Delivery callbacks:** status webhooks update `messages.delivery_status` and only move it forward (a late `delivered` never overwrites `read`). Callbacks that arrive before the send call has saved the `wamid` are stored as pending events and applied right after.
- **Audit:** case creation from WhatsApp, reopen-on-reply, delivery changes, failures, and resends are written to `audit_entries` (actor = System).
- Cases for customers without a phone number (Stage 2 test cases) never call WhatsApp; their replies are saved only.

The queue is PostgreSQL-backed rather than Redis so local development needs no extra service; the worker can run separately with `WHATSAPP_WORKER_ENABLED=false` on the API and `python -m app.whatsapp_service`.

### Set up a Meta test number

1. At [developers.facebook.com](https://developers.facebook.com/apps) create an app (type **Business**) and add the **WhatsApp** product. Meta creates a test business number.
2. **WhatsApp → API Setup:** note the **Phone number ID** and **WhatsApp Business Account ID**, generate a temporary access token (valid 24 h; use a System User token for longer demos), and add your own phone under **To** as an allowed recipient.
3. **App settings → Basic:** copy the **App secret** into `WHATSAPP_APP_SECRET` in `backend/.env`. `WHATSAPP_VERIFY_TOKEN` can be any random string. Run `python -m alembic upgrade head` and restart the backend.
4. Start the ngrok tunnel. **WhatsApp → Configuration → Webhook:** callback URL `https://YOUR_PUBLIC_BACKEND/api/webhooks/whatsapp` (shown in Resolve Settings), verify token = `WHATSAPP_VERIFY_TOKEN`, then **Verify and save** and **subscribe to the `messages` field**.
5. In Resolve: **Settings → Integrations → WhatsApp Business**, enter the phone number ID, WABA ID and token, and select **Connect WhatsApp** (the server validates them with Meta before saving them encrypted).
6. Demo: send a WhatsApp message from your phone to the test number → the case appears in **Complaints** within a few seconds → reply with **Approve & send** → the message arrives on your phone and its status changes to Sent / Delivered / Read.

WhatsApp only allows free-form replies within 24 hours of the customer's last message; outside that window Meta requires an approved template (not part of this stage), and the reply is marked failed with that reason.

### Stage 3 API

- `GET /api/whatsapp/status` — connection, webhook URL, configuration warnings, last webhook time, queue counts.
- `POST /api/whatsapp/connect` (owner) — `{phone_number_id, access_token, waba_id?}`; validated against the Graph API, token stored Fernet-encrypted (`WHATSAPP_ENCRYPTION_KEY`, falling back to `SHOPIFY_ENCRYPTION_KEY`). One number can belong to only one store.
- `DELETE /api/whatsapp/connect` (owner) — removes the credentials.
- `GET /api/webhooks/whatsapp` — Meta's subscription challenge (`hub.verify_token`).
- `POST /api/webhooks/whatsapp` — signed message and status webhooks.
- `POST /api/cases/{case_id}/messages/{message_id}/retry` — resend a failed WhatsApp reply.

## Stage 2 manual case handling API

- `GET /api/cases` returns cases scoped to the active store. Owners and managers see all cases; agents see only cases assigned to them.
- `GET /api/cases/{case_id}` returns the conversation, customer context, messages, and audit timeline. Store isolation and agent assignment access are enforced server-side.
- `POST /api/cases` creates a persisted test intake case. Owner or manager permission is required.
- `PATCH /api/cases/{case_id}` updates assignment, takeover, or status. Only owners/managers can assign; status changes must follow the workflow: `Open → In progress → Awaiting customer/Escalated → In progress/Resolved`, with resolved cases reopenable to `Open`.
- `POST /api/cases/{case_id}/messages` persists an outgoing staff reply with queued delivery status and records an audit event. Replies to resolved cases require reopening first.

The UI demo is: owner creates a test case, assigns it to an active agent, agent signs in and replies, agent moves it through the workflow, and the owner verifies the persisted messages and audit timeline. The test intake is database-backed; live customer messages still require a future WhatsApp/webhook integration.
