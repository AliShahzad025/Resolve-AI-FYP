# Resolve AI — React workspace

A responsive React + TypeScript interface covering the six GUI groups in the Deliverable II report. The visual system uses quiet green accents, warm neutral surfaces, compact tables and a persistent workspace navigation.

## Run locally

Requires Node.js 20.19+ or 22.12+.

```powershell
cd "D:\Resolve AI\Project\frontend"
npm install
npm run dev
```

Open the localhost address printed in the terminal. Vite chooses the next available port if 5173 is occupied. Use the address printed by Vite when starting the server.

```powershell
npm run build
npm run preview
```

The production output is in `dist/`.

## Screens

| Route | Screen and interactions |
|---|---|
| `#/login` | Sign in / create account, validation, password visibility, sample workspace entry |
| `#/overview` | Date range, order activity, outcome counts, risk tiers, complaint categories, courier coverage, CSV export |
| `#/orders` | Search, date / risk filters, CSV export, order-detail drawer with history and prediction metadata |
| `#/complaints` | Queue filters, English / Roman Urdu conversations, editable response, assignment, takeover, escalation and resolution |
| `#/policies` | Policy selection, text import, new documents, version history, save and activate versions |
| `#/settings` | Shopify / WhatsApp connection UI, staff roles and access, routing controls |

The account screen is available through the sign-out button beside the profile. The default route opens the overview for convenient previewing. Global search supports Ctrl+K / Command+K. The layout adapts to desktop, tablet and mobile; on mobile, select a complaint to open its conversation.

## What works now

- Filters and charts use the supplied example order records.
- Complaint replies, assignments, status changes and staff changes update the interface.
- Policy versions can be saved, inspected and activated.
- Workspace settings persist after refreshing.
- CSV downloads contain the currently selected records.
- All persistent changes are kept in browser localStorage under `resolve:v1:*`.

## Integration boundaries

This is a frontend prototype, not a live Shopify or WhatsApp integration. Account submission does not authenticate or create an account; passwords are never stored. Connections, indexing, AI results and message delivery are simulated. The fixed sample period ends on 5 October 2026. Complaint policy evidence is a sample snapshot and is not regenerated when a policy is edited. No refund, return, exchange or pickup execution workflow is included.

Before production use:

1. Replace `usePersisted` and seed-data imports with your authenticated API layer. Do not store real customer data, credentials or access tokens in localStorage.
2. Add server-side sessions, registration, authorization and store-scoped access checks. The preview shows the owner workspace; role descriptions are not security enforcement.
3. Load orders, prediction status, model versions and feature history from the backend. Replace the fixed reporting date with the reporting period returned by the API.
4. Bind conversations and notifications to WhatsApp events, including retries, delivery states, deduplication and customer consent checks.
5. Connect classifier and RAG outputs to the reply workflow. Include policy-version evidence and stale-draft handling. Do not send a draft until all server-side eligibility checks pass.
6. Replace simulated policy readiness with ingestion/indexing job status. Enforce version activation on the backend.
7. Implement Shopify authorization and webhook processing on the backend; the browser should never receive provider secrets.

## Source organization

- `src/App.tsx`: shell, hash navigation, global search and screen composition.
- `src/Auth.tsx`: sign-in and registration views.
- `src/Overview.tsx`, `Orders.tsx`, `Complaints.tsx`, `Policies.tsx`, `Settings.tsx`: screen components.
- `src/components.tsx`: reusable controls, badges, dialogs, formatting and export.
- `src/types.ts`: shared TypeScript models.
- `src/data.ts`: replaceable, fictional example records.
- `src/store.ts`: local demo state boundary.
- `src/styles.css`: design tokens, screen styles and responsive breakpoints.

Fonts are DM Sans and Manrope, loaded from Google Fonts with system fallbacks. Self-host them for offline deployment if desired. Icons come from lucide-react.

## Verification

The production build and browser smoke checks were run. `screenshots/` contains desktop and mobile previews of all screen groups.

To repeat the browser check, install Playwright as a development dependency and run the preview server first:

```powershell
npm install --save-dev playwright
$env:APP_URL = "http://127.0.0.1:5174/"
node verify.cjs
```

The script uses the installed Microsoft Edge browser. Override `BROWSER_CHANNEL` if needed. It verifies key UI actions and saves screenshots. It uses an isolated browser context and clears its sample state after checking.
