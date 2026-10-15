# Resolve AI backend — Stage 1

FastAPI + SQLAlchemy + Alembic API for your existing PostgreSQL schema in database `resolveAi`. Stage 1 implements account registration/sign-in/sign-out, owner store setup, store memberships, and owner-only member administration. The ORM uses the existing `users`, `stores`, and `memberships` tables and the schema's lowercase `member_role_t` enum and case-insensitive email index. No Shopify/WhatsApp/AI/complaint/order endpoints are included.

## Configure PostgreSQL

The database and full application schema must already exist. Set `DATABASE_URL` in `.env` to the exact PostgreSQL database name and credentials. The example uses `resolveAi` as provided in the original request. URL-encode special characters in the password. Set `JWT_SECRET` to a private random value of at least 32 characters. Do not commit `.env`.

This project does not recreate the schema you already installed. The additive Stage 1 migration only makes Shopify connection fields nullable until Shopify is connected and creates `auth_sessions`. Existing store rows and all other tables are preserved.

## Install and run

From `backend/` in PowerShell:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
# Adopt the existing schema as the migration baseline (run once per database)
alembic stamp 0001_existing_schema
# Apply only the additive Stage 1 migration
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
