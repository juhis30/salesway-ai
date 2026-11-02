# SaleSway AI

AI-powered sales intelligence for Indian sales teams: upload your sales data, see real
analytics, and ask questions in plain language on the web or WhatsApp.

> **Status: Phases 1–3 complete: analytics, web dashboard and the AI assistant (web + WhatsApp).** See [docs/plan.md](docs/plan.md) and [docs/assistant.md](docs/assistant.md).

## Run it locally (Windows, no Docker)

Requires Python 3.12+ and Node.js 18+. Run the backend and frontend in two terminals.

### 1. Backend (API on port 8000)

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
.\.venv\Scripts\alembic upgrade head          # create the database (SQLite file salesway.db)
.\.venv\Scripts\python -m app.seed            # load the demo company (~12 months of data)
.\.venv\Scripts\uvicorn app.main:app --reload
```

### 2. Frontend (web app on port 3000)

```powershell
cd frontend
npm install
npm run dev            # or: npm run build; npm start
```

Open **http://localhost:3000** and use the demo-account buttons on the sign-in page. Try **Ask SaleSway** in the sidebar, or (as the admin) the WhatsApp page's simulator.

API docs are at **http://localhost:8000/docs**. To call protected endpoints there, run
`POST /api/v1/auth/login`, copy the `access_token`, click **Authorize** and paste it in.

### Demo logins (password `Demo@12345`)

| Email                     | Role             | Sees                          |
| ------------------------- | ---------------- | ----------------------------- |
| ananya@shakti-demo.in     | org_admin        | everything, can manage users  |
| vikram@shakti-demo.in     | sales_head       | whole organisation            |
| neha@shakti-demo.in       | analyst          | whole organisation, imports   |
| sanjay@shakti-demo.in     | regional_manager | North region (Delhi + Punjab) |
| rahul@shakti-demo.in      | sales_manager    | Delhi Sales team              |
| amit@shakti-demo.in       | sales_executive  | only his own customers/sales  |

`python -m app.seed --reset` rebuilds the demo company with data up to today.

### Tests

```powershell
cd backend
.\.venv\Scripts\python -m pytest -q
```

## Try your own data

Sample files the seed generated are in `data/sample/`. Import order matters:
**customers → products → orders → targets** (orders reference customers and products by
code/SKU or name, and salespeople by email). Each upload is a two-step flow:

1. `POST /api/v1/imports/{entity}/preview` with the file: columns are auto-mapped
   (e.g. "Invoice No", "Qty", "Rate", "GST" are recognised), every row is validated, and
   nothing is written yet.
2. `POST /api/v1/imports/{job_id}/confirm` writes the valid rows.

## Layout

```
frontend/src/
  app/             Next.js pages: login, register, dashboard, customers, products, imports, users
  components/      app shell, filters, charts (Recharts), shared UI states
  lib/             API client (token refresh), auth context, Indian number/date formatting
backend/
  app/
    core/          config, database, security (JWT, bcrypt), auth dependencies
    models/        SQLAlchemy models (tenant-scoped via organization_id)
    schemas/       request/response models
    api/v1/        routes: auth, organization/users, customers/products, imports, analytics
    services/      importer (CSV/Excel pipeline), access (data-level permissions), audit
    analytics/     metric definitions, period logic, analytics queries
    tests/         pytest suite
    seed.py        demo data generator
  migrations/      Alembic
data/sample/       generated sample CSVs
docs/              plan and metric definitions
```
