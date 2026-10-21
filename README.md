# Resolve AI

The existing React + TypeScript frontend is in `frontend/`, copied from `D:\Resolve AI\FYP DL2\resolve-ui` with its design and interactions preserved.

## Requirements

- Python 3.12
- Node.js 20.19+ or 22.12+
- PostgreSQL 14 or newer
- npm

## Quick start

### 1. Clone and configure the backend

```bash
git clone https://github.com/AliShahzad025/Resolve-AI-FYP.git
cd Resolve-AI-FYP/backend
```

Create a PostgreSQL database named `resolveAi`, copy `.env.example` to `.env`, and set the local database URL and JWT secret. Full database setup, macOS, Windows, and GUI instructions are in [backend/README.md](backend/README.md).

### 2. Start the backend

macOS/Linux:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m alembic upgrade head
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Windows PowerShell:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m alembic upgrade head
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

### 3. Start the frontend

Open a second terminal:

```bash
cd Resolve-AI-FYP/frontend
npm install
npm run dev
```

Open the address Vite prints, normally `http://localhost:5173`.

Check the backend at `http://127.0.0.1:8000/api/health` and the API documentation at `http://127.0.0.1:8000/docs`.

## PostgreSQL GUI

You can inspect the database with DBeaver, Postico, or pgAdmin. Connect with:

```text
Host: 127.0.0.1
Port: 5432
Database: resolveAi
User: your PostgreSQL user
Password: your PostgreSQL password
```

The most useful Stage 2 tables are `complaint_cases`, `messages`, `audit_entries`, `users`, `memberships`, and `customers`.

## Current implementation

The UI includes account screens, analytics, orders and risk details, bilingual complaint handling, policy versions, and integration/team/routing settings. Registration, sign-in/out, store setup, team membership, Shopify order synchronization, and Stage 2 manual case handling are connected to FastAPI/PostgreSQL. The remaining AI analysis, risk scoring, and live WhatsApp features are scheduled for later stages.

## Implementation sequence

1. Validate the imported frontend and retain it as the starting interface.
2. Implement backend authentication, store membership, and persistent storage.
3. Connect frontend screens to typed API endpoints with loading, empty, and error states.
4. **Stage 2:** Manual case handling is implemented: case creation, assignment, validated statuses, staff replies, and audit history.
5. **Stage 3:** Add complaint intake, analysis, policy retrieval, staff review, and WhatsApp delivery.
6. **Stage 4 — Shopify integration:** Shopify authorization, customer/order synchronization, return/refund event ingestion, and verified webhooks are implemented in the backend foundation. Live connection and end-to-end testing are still in progress and require Shopify app credentials plus a public HTTPS callback/webhook address.
7. Add risk scoring and analytics backed by recorded outcomes.

The project report is a requirements reference. Its proposed features are not evidence that live services have been implemented.
