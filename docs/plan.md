# SaleSway AI: architecture and build plan

## Decisions for the demo build

| Topic         | Blueprint                       | Demo choice (and why)                                                              |
| ------------- | ------------------------------- | ---------------------------------------------------------------------------------- |
| Database      | PostgreSQL + RLS + pgvector     | SQLite. Postgres isn't installed; one env var switches later.                      |
| Tenant safety | Postgres Row Level Security     | Every query filters `organization_id` in the app layer, covered by tests. Add RLS on Postgres. |
| Runtime       | Docker Compose                  | Plain Python (venv). Dockerfiles come in the production phase.                     |
| LLM           | Bedrock / open / Indian models  | Provider interface: rule-based **mock** by default; Claude adapter ready (needs a key). |
| Jobs / cache  | Celery + Redis                  | Not needed yet; added with scheduled briefings/alerts.                             |
| Frontend      | Next.js + Tailwind + Recharts   | Phase 2. **Requires Node 18+** (machine currently has Node 10).                    |

## Architecture (modular monolith)

```
 CSV/Excel ─► Import pipeline ─► Canonical tables ─► Analytics service ─► REST API ─► Web dashboard
 (later: CRMs)  (map, validate,    (customers,         (metrics.py:          │
                 preview, confirm)  products, orders,    one definition        └──► AI orchestrator ─► WhatsApp
                                    targets ...)         per KPI)                    (intent → approved metric
                                                                                     query → LLM phrases result)
```

Key rules carried from the blueprint:

* The LLM never calculates numbers or writes SQL. It picks an intent; the analytics
  service computes; the LLM only phrases the result.
* Every metric is defined once in `app/analytics/metrics.py` (see `docs/metrics.md`).
* Data access is enforced server-side: organisation first, then the role's salesperson
  scope (`app/services/access.py`).

## Phases

### Phase 1: Foundation ✅ (this delivery)
- FastAPI app, SQLAlchemy 2 models, Alembic migration
- Register organisation, login, refresh tokens, `/me`
- 7 roles with data-level scope (executive → self, manager → team, regional manager →
  region, head/admin/analyst → organisation)
- Users, teams, territories management (admin only), audit log
- CSV/Excel import for customers, products, orders, targets: auto column mapping,
  validation, preview, confirm, duplicate handling, import history
- Analytics core: overview KPIs, monthly/daily trend, leaderboard, top customers/products,
  filters (date, salesperson, team, territory, product, segment)
- Demo company seed (80 customers, 28 products, ~4,600 orders, 12 months)
- 32 automated tests

### Phase 2: Web dashboard + deeper analytics ✅
- ✅ Next.js 15 + Tailwind + Recharts app (Node 26), light/dark themes, mobile layout
- ✅ Screens: login, register, executive dashboard (KPIs, revenue-vs-target trend,
  leaderboard, top customers/products, filters), customers, products, data import
  (upload → preview → fix mapping → confirm, history), users & teams management
- ✅ Analytics fixes found by visual review: growth hidden when the comparison window
  predates the data; run rate only for a single in-progress month; multi-month presets
  use complete months
- ✅ Customer detail page: products bought, monthly trend, full order history
- ✅ Customer health (active / at risk / dormant vs each customer's own rhythm, revenue at stake)
- ✅ Product trends (rising / declining / new / stopped vs previous period)
- ✅ Opportunities + activities tables, CSV import (CRM stage names recognised), pipeline screen
  (weighted value, win rate, stalled & overdue deals), team activity screen (visits, calls,
  visit-to-order rate, follow-ups with "mark done"), dashboard "needs attention" strip

### Phase 3: AI copilot ✅ (see docs/assistant.md)
- ✅ Provider interface: rule-based mock (default, no key) and Claude (`LLM_PROVIDER=anthropic`)
- ✅ 15 intents, period resolution (incl. Indian FY, Hinglish), fuzzy name matching with
  "which one did you mean?", follow-ups ("and last month?", "that customer"), 30-min context
- ✅ Executor runs only approved analytics functions within the user's scope; grounding check
  discards model replies containing numbers the engine didn't produce; full audit trail
- ✅ Web chat ("Ask SaleSway") with "based on" transparency and suggestions
- ✅ WhatsApp Cloud API webhook: verification, HMAC signature, tenant routing by business
  number, user by phone, idempotency, STOP/START opt-out, delivery receipts, admin simulator
- ⏭ Later: scheduled daily briefings & alerts over approved WhatsApp templates (needs a job
  scheduler), voice notes, Hindi replies from the rule-based path

### Phase 4: Predictive intelligence ✅ (see docs/predictions.md)
- ✅ Month-end forecast chosen by back-testing 3 methods (demo: 6.4% error vs 8.2% for pace), range
  and chance of target from past errors; next-month forecast (rough guide)
- ✅ Churn and deal-win models with an accuracy gate: used only if they beat the existing rule on
  unseen data (demo: both correctly kept off — rule AUC 0.867 vs model 0.878; deals no signal)
- ✅ Next best actions (follow-ups, win-back, stalled deals, cross-sell) with Done/Dismiss
- ✅ Model versions, metrics and stored predictions; Forecast & actions page; assistant uses the forecast

### Phase 5: Integrations ✅ (see docs/integrations.md)
- ✅ Read-only connectors for Zoho CRM, HubSpot and Salesforce, plus a built-in Sandbox CRM
- ✅ OAuth with signed state, encrypted credentials, token refresh, retries with back-off
- ✅ Incremental sync through the standard importer (owner matching, de-duplication, per-row errors),
  sync history, reconnect handling, HubSpot webhooks, hourly scheduled sync via GitHub Actions
- ⏭ Not yet run against real CRM accounts (needs developer apps registered with each CRM)
### Phase 6: Production readiness (in progress; see docs/security.md)
- ✅ Security and reliability: Postgres row-level security on every tenant table (tested as a
  non-bypass role), login lockout, password rules, change/reset password with sign-out everywhere,
  security headers, request ids and safe error pages, JSON logs, optional Sentry, `/health/ready`,
  CI on SQLite and Postgres with dependency audits, deploy only after checks pass, Dependabot
- ⏭ httpOnly cookie sessions, a background job queue (instead of syncs and training inside
  requests), backups with a tested restore, scheduled WhatsApp briefings, billing
