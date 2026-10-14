# Resolve AI

The existing React + TypeScript frontend is in `frontend/`, copied from `D:\Resolve AI\FYP DL2\resolve-ui` with its design and interactions preserved.

## Start the frontend

```powershell
cd "D:\Resolve AI\Project\frontend"
npm install
npm run dev
```

Open the address Vite prints. Run `npm run build` for the production build.

## Current implementation

The UI includes account screens, analytics, orders and risk details, bilingual complaint handling, policy versions, and integration/team/routing settings. It currently uses fictional sample data and browser storage. Authentication, AI predictions, provider connections, and message delivery are simulated.

## Implementation sequence

1. Validate the imported frontend and retain it as the starting interface.
2. Implement backend authentication, store membership, and persistent storage.
3. Connect frontend screens to typed API endpoints with loading, empty, and error states.
4. Add Shopify event ingestion and order synchronization.
5. Add complaint intake, analysis, policy retrieval, staff review, and WhatsApp delivery.
6. Add risk scoring and analytics backed by recorded outcomes.

The project report is a requirements reference. Its proposed features are not evidence that live services have been implemented.
