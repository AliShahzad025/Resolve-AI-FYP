# Resolve AI

The existing React + TypeScript frontend is in `frontend/`, copied from `D:\Resolve AI\FYP DL2\resolve-ui` with its design and interactions preserved.

## Stage 1: foundation

The `backend/` folder contains the FastAPI service, PostgreSQL schema and Alembic migration for users, stores, memberships, and revocable sign-in sessions. Follow [backend/README.md](backend/README.md) to create the `resolveAi` database, configure `backend/.env`, run the migration, and start the API.

Start the React app separately:

```powershell
cd frontend
npm install
npm run dev
```

Open the address Vite prints. Run `npm run build` for the production build.

## Current implementation

The UI includes account screens, analytics, orders and risk details, bilingual complaint handling, policy versions, and integration/team/routing settings. Stage 1 wires registration, sign-in/out, initial store setup, and team membership to FastAPI/PostgreSQL. The other workspace features remain fictional previews and are scheduled for later stages.

## Implementation sequence

1. Validate the imported frontend and retain it as the starting interface.
2. Implement backend authentication, store membership, and persistent storage.
3. Connect frontend screens to typed API endpoints with loading, empty, and error states.
4. Add Shopify event ingestion and order synchronization.
5. Add complaint intake, analysis, policy retrieval, staff review, and WhatsApp delivery.
6. Add risk scoring and analytics backed by recorded outcomes.

The project report is a requirements reference. Its proposed features are not evidence that live services have been implemented.
