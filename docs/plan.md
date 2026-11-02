# SaleSway AI: architecture and build plan

## Decisions for the demo build

| Topic         | Blueprint                       | Demo choice (and why)                                                              |
| ------------- | ------------------------------- | ---------------------------------------------------------------------------------- |
| Database      | PostgreSQL + RLS + pgvector     | SQLite. Postgres isn't installed; one env var switches later.                      |
| Tenant safety | Postgres Row Level Security     | Every query filters `organization_id` in the app layer, covered by tests. Add RLS on Postgres. |
| Runtime       | Docker Compose                  | Plain Python (venv). Dockerfiles come in the production phase.                     |
| LLM           | Bedrock / open / Indian models  | Provider interface with a **mock** adapter until an API key is available.          |
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

### Phase 3: AI copilot
- Provider interface (mock → Claude/Bedrock/Sarvam), intent taxonomy, entity and
  time-period resolution ("this month", "last month", "my team")
- Conversation memory, grounding checks, audit trail of questions and answers
- Web chat first, then WhatsApp Cloud API webhook (signature check, idempotency, opt-in)

### Phase 4: Predictive intelligence
- Month-end forecast, target-achievement probability, churn scoring, next best action
- Model versioning, stored predictions, evaluation metrics (no invented confidence)

### Phase 5: Integrations: Zoho / HubSpot / Salesforce connectors mapping to the same tables
### Phase 6: Production: Postgres + RLS, Docker, AWS, Redis/Celery, observability, billing
