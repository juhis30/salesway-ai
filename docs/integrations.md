# CRM connections (Phase 5)

SaleSway connects to **Zoho CRM, HubSpot and Salesforce, read-only**: records are copied in and
nothing is ever written back. Each company connects the system it uses, from **Connections**
(admins only). A built-in **Sandbox CRM** lets anyone try the flow without an account.

Code: `backend/app/integrations/crm/`.

## How a sync works

1. The connector pages through records **changed since the last successful sync** (everything on
   the first sync): Zoho with `If-Modified-Since`, HubSpot with a search on `hs_lastmodifieddate`,
   Salesforce with `SystemModstamp`. It asks for 5 minutes of overlap so nothing saved mid-sync is missed.
2. Each record becomes an import row (the same columns as a CSV upload) and goes through the
   **standard importer**: salespeople matched by email, customers/products/deals updated by their
   CRM id, orders and activities de-duplicated, problems reported per row.
3. Entities run in order (customers → products → orders → deals → activities) because later ones
   refer to earlier ones.
4. The job is recorded under **Recent syncs**: counts per entity and every skipped row with its reason.
   A failed call (network, permissions) writes nothing from that run.

| | Zoho CRM | HubSpot | Salesforce |
| --- | --- | --- | --- |
| Customers | Accounts | Companies | Account |
| Products | Products | Products | Product2 |
| Orders | Sales Orders (after discount, before tax) | not synced (no standard object) | OrderItem / Order |
| Deals | Deals | Deals | Opportunity |
| Activities | Tasks, Calls, Events | Calls, Meetings, Tasks | Task, Event |
| Change detection | `If-Modified-Since` | search on `hs_lastmodifieddate` | `SystemModstamp` |
| Instant updates | scheduled only | webhooks (signed) | scheduled only |

**Deal stages:** won/lost come from the CRM's closed flags or "Closed Won/Lost" names; open deals
are placed by win probability (<20% prospecting, <40% qualification, <70% proposal, else
negotiation), so custom pipelines work without setup.

**Reliability:** expired access tokens are refreshed automatically (and once more on a 401);
throttling (429) and server errors are retried with back-off, honouring `Retry-After`. If the CRM
rejects the connection, it is marked **Needs reconnecting**.

**Security:** sign-in uses OAuth with a signed, 10-minute state value tied to the admin and the
company, so a sign-in can't be redirected into another organisation. Tokens are stored encrypted
(`CREDENTIALS_KEY`) and never returned by the API. Disconnecting deletes them; synced data stays.

## Setting up each CRM

Each system needs a SaleSway app registered once by whoever runs the server. The redirect / callback
URL is `https://salesway-api.onrender.com/api/v1/integrations/<provider>/callback`
(`http://localhost:8000/...` locally). Put the keys in Render → salesway-api → Environment.

* **Zoho CRM** — [api-console.zoho.in](https://api-console.zoho.in) → Add client → *Server-based
  Application*. Set `ZOHO_CLIENT_ID`, `ZOHO_CLIENT_SECRET`. Accounts outside India: set
  `ZOHO_ACCOUNTS_URL` (e.g. `https://accounts.zoho.com`). Scopes requested:
  `ZohoCRM.modules.READ, ZohoCRM.users.READ, ZohoCRM.org.READ`.
* **HubSpot** — a public app in a HubSpot developer account; scopes
  `crm.objects.companies.read crm.objects.deals.read crm.objects.contacts.read crm.objects.owners.read e-commerce`.
  Set `HUBSPOT_CLIENT_ID`, `HUBSPOT_CLIENT_SECRET`. Optional instant updates: webhook URL
  `https://salesway-api.onrender.com/api/v1/webhooks/hubspot` (signature v3 is verified).
* **Salesforce** — Setup → App Manager → New Connected App, OAuth scopes `api` and `refresh_token`.
  Set `SALESFORCE_CLIENT_ID`, `SALESFORCE_CLIENT_SECRET` (`SALESFORCE_LOGIN_URL=https://test.salesforce.com`
  for sandboxes). Salesforce has no read-only scope: connect as an **integration user whose profile
  can only read** these objects.

## Scheduled syncs

Render's free plan has no scheduler, so `.github/workflows/crm-sync.yml` calls
`POST /api/v1/integrations/sync-due` every hour. Turn it on by copying Render's generated
`SYNC_CRON_TOKEN` into the GitHub repository secret of the same name. Connections synced within
`SYNC_INTERVAL_MINUTES` (default 60) are skipped. Locally: run `sync_due` from Python, or use **Sync now**.

## Not verified yet

The connectors are written to each CRM's published REST API and tested against simulated responses
(paging, change detection, token refresh, throttling, stage mapping), but **none has been run
against a real Zoho, HubSpot or Salesforce account yet**. Expect small field-name adjustments on
first real use; the sync history shows exactly which rows were skipped and why.

After a sync, retrain the models (Forecast & actions → Retrain now) for predictions to include the new data.
