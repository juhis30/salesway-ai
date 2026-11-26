# API reference

Every endpoint of the SaleSway API, generated from the app's OpenAPI schema
(`python -m app.tools.api_reference > ../docs/api.md` in `backend`). Paths start with `/api/v1`
except the health checks.

- Except sign-in, registration and refresh, every request needs `Authorization: Bearer <access token>`.
  What comes back is limited to the caller's organisation and role
  ([architecture.md](architecture.md#4-who-sees-what)).
- Slow work (CRM sync, retraining) returns `202` with a job; poll `GET /api/v1/jobs/{job_id}` until
  its status is `succeeded` or `failed` ([architecture.md](architecture.md#7-background-jobs)).
- Errors are JSON: `{"detail": "..."}`. Unexpected ones say only "Something went wrong on our side.
  Reference: <id>"; the id is also in the `X-Request-ID` header.
- Interactive docs (`/docs`) are available locally; in production they are off unless `API_DOCS=true`.
- Not listed (hidden from the schema): the webhooks `GET`/`POST /api/v1/webhooks/whatsapp` (Meta) and
  `POST /api/v1/webhooks/hubspot`, the OAuth callback `GET /api/v1/integrations/{provider}/callback`,
  and `POST /api/v1/integrations/sync-due` (the hourly scheduler, needs `X-Sync-Token`).

## Health

| Method | Path | What it does |
| --- | --- | --- |
| GET | `/health` | Liveness: the process is up. /health/ready also checks the database. |
| GET | `/health/ready` | Ready |

## Auth

| Method | Path | What it does |
| --- | --- | --- |
| POST | `/api/v1/auth/change-password` | Change your own password. Every other session is signed out; this one gets new tokens. |
| POST | `/api/v1/auth/login` | Login |
| POST | `/api/v1/auth/logout` | Sign this device out: its refresh token (and any it was swapped for) stops working. |
| GET | `/api/v1/auth/me` | Me |
| POST | `/api/v1/auth/refresh` | Swap a refresh token for new tokens. Each refresh token works once. |
| POST | `/api/v1/auth/register` | Create a new organisation with its first user as organisation admin. |

## Organization

| Method | Path | What it does |
| --- | --- | --- |
| GET | `/api/v1/organization` | Get Organization |
| PATCH | `/api/v1/organization` | Update Organization |
| GET | `/api/v1/teams` | List Teams |
| POST | `/api/v1/teams` | Create Team |
| GET | `/api/v1/territories` | List Territories |
| POST | `/api/v1/territories` | Create Territory |
| GET | `/api/v1/users` | List Users |
| POST | `/api/v1/users` | Create User |
| PATCH | `/api/v1/users/{user_id}` | Update User |
| POST | `/api/v1/users/{user_id}/reset-password` | Set a temporary password for someone (also unlocks the account and signs them out everywhere). |

## Customers & products

| Method | Path | What it does |
| --- | --- | --- |
| GET | `/api/v1/customers` | List Customers |
| GET | `/api/v1/customers/segments` | Customer Segments |
| GET | `/api/v1/customers/{customer_id}` | Get Customer |
| GET | `/api/v1/customers/{customer_id}/orders` | Every order for the customer, newest first, including ones that don't count as revenue. |
| GET | `/api/v1/products` | List Products |

## Data import

| Method | Path | What it does |
| --- | --- | --- |
| GET | `/api/v1/imports` | List Imports |
| GET | `/api/v1/imports/fields/{entity}` | The fields a file for this entity can contain, for building a mapping screen. |
| POST | `/api/v1/imports/{entity}/preview` | Preview Import |
| POST | `/api/v1/imports/{job_id}/confirm` | Confirm Import |
| GET | `/api/v1/imports/{job_id}/errors` | Import Errors |

## Analytics

| Method | Path | What it does |
| --- | --- | --- |
| GET | `/api/v1/analytics/leaderboard` | Leaderboard |
| GET | `/api/v1/analytics/overview` | Executive KPIs: revenue, orders, AOV, target achievement and growth. |
| GET | `/api/v1/analytics/top-customers` | Top Customers |
| GET | `/api/v1/analytics/top-products` | Top Products |
| GET | `/api/v1/analytics/trend` | Trend |

## Insights

| Method | Path | What it does |
| --- | --- | --- |
| GET | `/api/v1/activities/follow-ups` | Follow Ups |
| PATCH | `/api/v1/activities/{activity_id}` | Mark a follow-up done (or re-open it). Allowed for its owner and anyone whose scope includes them. |
| GET | `/api/v1/analytics/customer-health` | Active / at-risk / dormant customers as of the period end, plus new vs returning buyers in the period. |
| GET | `/api/v1/analytics/pipeline` | Open deals as of today; won/lost and win rate for deals closed in the period. |
| GET | `/api/v1/analytics/product-trends` | Product Trends |
| GET | `/api/v1/analytics/team-activity` | Team Activity |

## Predictions

| Method | Path | What it does |
| --- | --- | --- |
| GET | `/api/v1/ml/models` | The latest version of each model, with how it scored when tested on past data. |
| POST | `/api/v1/ml/train` | Retrain every model on the latest data and refresh predictions and suggested actions. |
| GET | `/api/v1/predictions/churn` | Churn Scores |
| GET | `/api/v1/predictions/deals` | Deal Scores |
| GET | `/api/v1/predictions/month-end` | This month's expected total for the caller's scope (or a salesperson / team filter). |
| GET | `/api/v1/predictions/next-month` | Next Month |
| GET | `/api/v1/recommendations` | Suggested actions for you (managers also see their team's). |
| PATCH | `/api/v1/recommendations/{recommendation_id}` | Update Recommendation |

## Assistant

| Method | Path | What it does |
| --- | --- | --- |
| GET | `/api/v1/assistant/conversations` | Conversations |
| GET | `/api/v1/assistant/conversations/{conversation_id}` | Conversation Messages |
| POST | `/api/v1/assistant/messages` | Ask a question. The answer is computed from your data, within what your role may see. |

## Whatsapp

| Method | Path | What it does |
| --- | --- | --- |
| POST | `/api/v1/whatsapp/accounts` | Add Account |
| DELETE | `/api/v1/whatsapp/accounts/{account_id}` | Remove Account |
| GET | `/api/v1/whatsapp/settings` | Whatsapp Settings |
| POST | `/api/v1/whatsapp/simulate` | Send a message *as* one of your users through the full WhatsApp path, without Meta. |

## Integrations

| Method | Path | What it does |
| --- | --- | --- |
| GET | `/api/v1/integrations` | List Integrations |
| DELETE | `/api/v1/integrations/{integration_id}` | Forget the credentials. Data already synced stays in SaleSway. |
| GET | `/api/v1/integrations/{integration_id}/jobs` | Jobs |
| POST | `/api/v1/integrations/{integration_id}/sync` | Start a sync in the background. Poll GET /jobs/{id}; the result names the sync job. |
| POST | `/api/v1/integrations/{provider}/connect` | Start connecting: returns the CRM's sign-in page (OAuth), or connects the sandbox directly. |

## Jobs

| Method | Path | What it does |
| --- | --- | --- |
| GET | `/api/v1/jobs/{job_id}` | Get Job |
