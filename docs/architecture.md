# SaleSway AI: architecture

How the system fits together, with diagrams of the main flows. The diagrams are Mermaid, which
GitHub draws on the page. Details live in the topic docs linked from each section.

- [1. System context](#1-system-context)
- [2. Inside the backend](#2-inside-the-backend)
- [3. Life of a request](#3-life-of-a-request)
- [4. Who sees what](#4-who-sees-what)
- [5. How the assistant answers](#5-how-the-assistant-answers)
- [6. Getting data in](#6-getting-data-in)
- [7. Background jobs](#7-background-jobs)
- [8. Predictions](#8-predictions)
- [9. Sign-in and sessions](#9-sign-in-and-sessions)
- [10. Data model](#10-data-model)
- [11. Build and deploy](#11-build-and-deploy)
- [12. Key decisions](#12-key-decisions)
- [13. Where the code is](#13-where-the-code-is)

## 1. System context

```mermaid
flowchart LR
    subgraph People
        WebUser["Sales team<br/>(browser)"]
        WAUser["Salesperson<br/>(WhatsApp)"]
    end

    subgraph Render["Render (Singapore)"]
        Web["Web app<br/>Next.js"]
        API["API<br/>FastAPI + job worker"]
    end

    DB[("Neon Postgres<br/>(Singapore)")]

    subgraph External["Outside services"]
        Meta["Meta WhatsApp<br/>Cloud API"]
        CRM["Zoho / HubSpot /<br/>Salesforce"]
        LLM["Claude<br/>(optional)"]
        Sentry["Sentry<br/>(optional)"]
        GH["GitHub Actions<br/>CI + hourly sync"]
    end

    WebUser --> Web
    Web -- "JSON over HTTPS" --> API
    WAUser <--> Meta
    Meta -- "webhook" --> API
    API -- "replies" --> Meta
    API -- "read-only sync" --> CRM
    CRM -- "HubSpot webhook" --> API
    API --> DB
    API -. "understand / rephrase" .-> LLM
    API -. "errors" .-> Sentry
    GH -- "sync-due every hour" --> API
```

Two services and one database. The web app is a Next.js front end that renders in the browser and calls the API; everything else
(analytics, predictions, the assistant, imports, CRM sync and the background worker) is one
Python service, a *modular monolith*. Optional services are dotted: the app works without them
(the assistant falls back to rule-based understanding; errors go to the logs).

## 2. Inside the backend

```mermaid
flowchart TB
    subgraph Edge["Edge (app/core)"]
        MW["RequestContext middleware<br/>request id, security headers,<br/>safe errors, JSON logs"]
        Auth["Auth dependency<br/>token check, set_tenant"]
        RL["Rate limits<br/>(database counters)"]
    end

    subgraph Routes["API routes (app/api/v1)"]
        R1["auth, users, catalog"]
        R2["analytics, insights"]
        R3["predictions, ml"]
        R4["assistant, whatsapp"]
        R5["imports, integrations, jobs"]
    end

    subgraph Domain["Domain logic"]
        AN["analytics<br/>metric definitions, queries"]
        AI["ai<br/>understand, execute, reply, guard"]
        ML["ml<br/>forecast, churn, deals, actions"]
        IMP["services/importer<br/>map, validate, apply"]
        CRMC["integrations/crm<br/>connectors, sync"]
        WA["integrations/whatsapp"]
        JQ["services/jobs + worker"]
        ACC["services/access<br/>role scope"]
    end

    DB[("Postgres<br/>row-level security")]

    MW --> Auth --> Routes
    RL -.-> R1 & R4
    R2 --> AN
    R3 --> ML
    R4 --> AI --> AN
    R5 --> IMP
    R5 --> JQ
    JQ --> CRMC --> IMP
    JQ --> ML
    JQ --> WA --> AI
    AN & ML & AI --> ACC
    Domain --> DB
```

Routes stay thin; the logic sits in the domain modules, which the routes, the assistant and the
worker all share. Two examples of that sharing: the assistant answers with the same `analytics`
functions as the dashboard, and CRM sync feeds the same `importer` as file uploads.

## 3. Life of a request

```mermaid
sequenceDiagram
    autonumber
    participant B as Browser
    participant M as Middleware
    participant D as Auth dependency
    participant H as Route handler
    participant P as Postgres

    B->>M: GET /api/v1/analytics/overview (Bearer token)
    M->>M: assign X-Request-ID, start timer
    M->>D: pass on
    D->>D: verify JWT signature, type, expiry
    D->>P: set app.current_org for this transaction
    D->>P: load user (role re-read, token version checked)
    D->>H: current user
    H->>H: work out the user's scope (team, territory, own)
    H->>P: queries filtered by organisation and scope
    Note over P: Row-level security adds its own<br/>organisation filter to every query
    P-->>H: rows
    H-->>M: JSON
    M-->>B: response + security headers + X-Request-ID
    M->>M: one JSON log line (id, path, status, ms)
```

Role and organisation are always re-read from the database, never trusted from the token. If
anything unexpected fails, the user gets "Something went wrong on our side. Reference: <id>" and the
full error is logged under that id.

## 4. Who sees what

Every query is limited twice: by **organisation** (the application filter plus Postgres row-level
security) and by **role**.

```mermaid
flowchart TD
    Q["A query for sales data"] --> O{"Same organisation?"}
    O -- "no" --> X["Nothing<br/>(filter + row-level security)"]
    O -- "yes" --> R{"Role"}
    R -- "org admin, sales head,<br/>analyst, super admin" --> All["Whole organisation"]
    R -- "regional manager" --> Reg["Everyone in territories<br/>of the same region"]
    R -- "sales manager" --> Team["Their team"]
    R -- "sales executive" --> Own["Only their own<br/>customers and sales"]
```

See [security.md](security.md#keeping-each-organisations-data-separate) for how row-level security
is set per transaction, and for the explicit cross-organisation lookups (sign-in, webhooks).

## 5. How the assistant answers

The language model **never writes SQL and never calculates numbers**. It only turns the question
into a fixed schema; code computes the facts; and any model-written reply is checked against those
facts before it is sent.

```mermaid
flowchart LR
    Q["Question<br/>web or WhatsApp"] --> I{"Interpret"}
    I -- "rules (default)" --> S["Interpretation<br/>1 of 15 intents,<br/>periods, names"]
    I -- "Claude (optional),<br/>falls back to rules" --> S
    S --> F["Merge with previous<br/>question (30 min)"]
    F --> C["Resolve names against<br/>what the user may see"]
    C --> E["Execute<br/>same analytics as dashboard,<br/>scope enforced again"]
    E --> FA["Facts"]
    FA --> T["Template reply"]
    T --> P{"Model enabled?"}
    P -- "no" --> OUT["Reply"]
    P -- "yes" --> RW["Claude rewrites<br/>tone, Hinglish"]
    RW --> G{"Every number<br/>in the facts?"}
    G -- "yes" --> OUT
    G -- "no" --> T2["Discard, send template"] --> OUT
    OUT --> REC["Store question, answer, facts,<br/>provider, grounded flag + audit"]
```

More in [assistant.md](assistant.md).

## 6. Getting data in

Two routes in, one pipeline: files and CRM records both go through the same mapping, validation,
de-duplication and owner matching.

```mermaid
flowchart TB
    AP["Apply<br/>insert / update, skip duplicates,<br/>match owners by email"]
    DB[("Postgres")]
    H["History: import job / sync job<br/>with counts and row errors"]

    subgraph Files["File upload (CSV / Excel)"]
        U["Upload"] --> PV["Preview<br/>auto-map columns,<br/>validate every row,<br/>nothing written"]
        PV --> CF{"Confirm?"}
        CF -- "not within 7 days" --> EXP["Expired:<br/>uploaded rows deleted"]
    end

    subgraph CRMs["CRM sync (read-only)"]
        T1["Sync now"] --> Q
        T2["Hourly schedule"] --> Q
        T3["HubSpot webhook"] --> Q
        Q["Queue job<br/>(one per connection)"] --> W["Worker"]
        W --> FE["Fetch changed records<br/>since last cursor"]
        FE --> VA["Validate"]
    end

    CF -- "yes" --> AP
    VA --> AP
    AP --> DB
    AP --> H
```

Order matters for files: customers, then products, then orders, then targets. Nothing is ever
written back to a CRM. More in [integrations.md](integrations.md).

## 7. Background jobs

Slow work (CRM syncs, model retraining, WhatsApp replies) runs in a queue kept in Postgres, so
web requests return at once and no Redis or Celery is needed.

```mermaid
sequenceDiagram
    autonumber
    participant B as Browser
    participant A as API
    participant J as jobs table
    participant W as Worker thread
    participant X as CRM / models / WhatsApp

    B->>A: POST /integrations/{id}/sync
    A->>J: insert job (or return the one already queued)
    A-->>B: 202 job {id, status: queued}
    A->>W: wake up
    W->>J: claim oldest due job<br/>FOR UPDATE SKIP LOCKED
    W->>X: run the handler for its organisation
    alt finished
        W->>J: succeeded + result
    else unexpected crash
        W->>J: queued again later, then failed<br/>with a reference number
    end
    loop every 1.5 s until finished
        B->>A: GET /jobs/{id}
        A-->>B: status (and result when done)
    end
```

```mermaid
stateDiagram-v2
    [*] --> queued: enqueue
    queued --> running: worker claims it
    running --> succeeded
    running --> queued: crash, attempts left
    running --> failed: crash, no attempts left<br/>or an expected failure
    running --> queued: cut off by a restart<br/>(found after 30 min)
    succeeded --> [*]: deleted after 30 days
    failed --> [*]: deleted after 30 days
```

Every 15 minutes the worker also does housekeeping: picking up jobs cut off by a restart, and
deleting expired import previews, old jobs, expired refresh tokens and old rate-limit counts.
More in [security.md](security.md#background-jobs).

## 8. Predictions

```mermaid
flowchart LR
    TR["Retrain now<br/>(admin or analyst)"] --> JOB["Background job"]
    JOB --> HIST["Order history<br/>up to today"]
    HIST --> F1["Month-end and next-month<br/>forecasts"]
    HIST --> F2["Churn model"]
    HIST --> F3["Deal-win model"]
    F1 --> BT{"Back-test on past months:<br/>beats the simple rule?"}
    F2 --> G1{"AUC at least 0.65<br/>and clearly better<br/>than the rule?"}
    F3 --> G2{"AUC at least 0.65<br/>and clearly better than<br/>stage weights?"}
    BT -- "best method wins" --> ACT["Active model version<br/>+ predictions"]
    G1 -- "yes" --> ACT
    G1 -- "no" --> REJ["Rejected: the simple rule<br/>or stage weights stay in use"]
    G2 -- "yes" --> ACT
    G2 -- "no" --> REJ
    ACT --> REC["Next best actions<br/>per salesperson"]
    REJ --> REC
```

Every model version is kept with how it scored on data it didn't learn from, and the page says
plainly when a model isn't good enough to use. More in [predictions.md](predictions.md).

## 9. Sign-in and sessions

```mermaid
sequenceDiagram
    autonumber
    participant B as Browser
    participant A as API
    participant P as Postgres

    B->>A: POST /auth/login
    A->>P: count attempt for this IP (rate limit)
    A->>P: check password (bcrypt), lockout after 5 wrong
    A->>P: record refresh token (new family)
    A-->>B: access token (30 min) + refresh token (7 days)

    Note over B,A: Later, when the access token expires
    B->>A: POST /auth/refresh
    alt token never used
        A->>P: mark used, record a new one in the same family
        A-->>B: new pair
    else used again within 30 s (two tabs)
        A-->>B: new pair
    else used again later (someone kept a copy)
        A->>P: raise token version: every session of this user ends
        A-->>B: 401, sign in again
    end

    B->>A: POST /auth/logout
    A->>P: revoke this family only (other devices stay signed in)
```

Changing a password, an admin reset, deactivation or a role change also raises the token version,
which ends every session at once. More in [security.md](security.md#sign-in).

## 10. Data model

Every table except `organizations` and `rate_limit_counters` has an `organization_id` and is
protected by row-level security (the relation to `organizations` is left out below for clarity).

**Sales data**: people, customers, what they bought, deals and visits.

```mermaid
erDiagram
    organizations ||--o{ users : has
    teams ||--o{ users : "member of"
    territories ||--o{ users : "works in"
    users ||--o{ customers : owns
    territories ||--o{ customers : "located in"
    customers ||--o{ sales_orders : places
    users ||--o{ sales_orders : "sold by"
    sales_orders ||--|{ sales_order_items : contains
    products ||--o{ sales_order_items : "sold as"
    users ||--o{ sales_targets : "monthly target"
    customers ||--o{ opportunities : "has deals"
    users ||--o{ opportunities : "owns deal"
    customers ||--o{ sales_activities : "visit or call"
    opportunities ||--o{ sales_activities : about
    users ||--o{ sales_activities : logs
```

**The app's own records**: predictions, the assistant, connections, background work, security.

```mermaid
erDiagram
    ml_models ||--o{ predictions : produces
    users ||--o{ recommendations : "suggested to"
    users ||--o{ conversations : has
    conversations ||--o{ messages : contains
    integrations ||--o{ sync_jobs : "sync history"
    users ||--o{ import_jobs : uploads
    users ||--o{ jobs : starts
    users ||--o{ refresh_tokens : "signed in with"
    users ||--o{ audit_logs : "acted in"
```

Other tables: `whatsapp_accounts` (which business number belongs to which organisation) and
`rate_limit_counters` (not per organisation; keys are users and IP addresses). Predictions and
recommendations point at customers or deals by id. Revenue is the sum of
`sales_order_items.line_amount`, which excludes tax. Metric definitions are in
[metrics.md](metrics.md).

## 11. Build and deploy

```mermaid
flowchart LR
    Dev["Push to main"] --> CI{"GitHub Actions CI"}
    CI --> T1["Backend tests<br/>on SQLite"]
    CI --> T2["Backend tests on Postgres 16<br/>as a role without RLS bypass"]
    CI --> T3["Migrations from<br/>an empty database"]
    CI --> T4["Frontend type check<br/>and build"]
    CI --> T5["pip-audit + npm audit<br/>(high and critical)"]
    T1 & T2 & T3 & T4 & T5 --> OK{"All passed?"}
    OK -- "no" --> STOP["Nothing deployed"]
    OK -- "yes" --> R["Render deploys<br/>both services"]
    R --> MIG["API start:<br/>alembic upgrade head"]
    MIG --> UP["uvicorn<br/>(+ worker thread)"]
    DB2[("Neon")] --- MIG
    DEP["Dependabot<br/>weekly update PRs"] --> CI
```

The free plan stops the API after 15 idle minutes; the first request then waits about a minute.
The login page wakes the API as soon as it opens. Setup steps are in
[deployment.md](deployment.md).

## 12. Key decisions

| Decision | Chosen | Instead of | Why |
| --- | --- | --- | --- |
| Shape of the backend | One modular Python service | Microservices | One team, one deploy, shared domain logic; modules can split later along existing boundaries |
| Tenant isolation | Shared tables + application filters + Postgres row-level security | Database or schema per customer | Cheap to run, simple migrations; RLS fails closed if a filter is forgotten |
| AI and numbers | Model interprets and rephrases; code computes; replies checked for invented numbers | Text-to-SQL, or letting the model answer | Sales decisions need numbers that are always right and within the user's permissions |
| Machine learning | Back-tested methods and an accuracy gate; simple rules as the fallback | Always using the most complex model | Small companies have little history; a wrong prediction is worse than an honest "not enough data" |
| Slow work | Job queue in Postgres (`SKIP LOCKED`) with an in-process worker | Redis + Celery | No new infrastructure on the free plan; can move to a separate worker process unchanged |
| CRM access | Read-only, through the same importer as files | Two-way sync | No risk of damaging customers' CRM data; one validation path |
| Sessions | Short access tokens + single-use refresh tokens with reuse detection | Long-lived tokens | A stolen refresh token is detected and shuts every session |
| Money | `Numeric` columns, `Decimal` in Python, revenue excluding tax | Floats | No rounding drift in totals |
| Hosting | Render + Neon free plans (Singapore) | A cloud account to manage | Zero cost for the demo; India hosting is a later step for customers that need it |

## 13. Where the code is

```
backend/app/
  main.py                 app setup, lifespan (starts the worker)
  worker.py               background job runner (thread, or `python -m app.worker`)
  core/                   config, database + row-level security, auth, passwords, rate limits, observability
  api/v1/                 HTTP routes (thin)
  models/                 SQLAlchemy tables
  schemas/                request and response shapes
  services/               importer, access (role scope), audit, jobs queue + job handlers
  analytics/              metric definitions, periods, dashboard and insight queries
  ai/                     assistant: nlu, catalog, executor, responder, guardrails, providers, orchestrator
  ml/                     forecast, churn, deals, recommendations, training, serving
  integrations/crm/       Zoho, HubSpot, Salesforce, Sandbox connectors + sync
  integrations/whatsapp/  webhook parsing, signatures, sending, message handling
  tests/                  pytest suite (runs on SQLite and on Postgres with RLS)
backend/migrations/       Alembic migrations
frontend/src/
  app/                    Next.js pages
  components/             app shell, charts, filters, shared UI
  lib/                    API client (token refresh, background jobs), auth, formatting
docs/                     this file and the topic docs
```
