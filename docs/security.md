# Security and reliability

What protects the app today, how to check it is working, and what is still missing.

## Keeping each organisation's data separate

Two layers, so one mistake doesn't leak data:

1. **Application filters.** Every query is scoped to the signed-in user's organisation (and, below
   admins, to their team, territory or own customers; see `services/access.py`).
2. **Postgres row-level security (RLS)** (migration `1aa74961f6b0`). All 20 tenant tables, plus
   `organizations`, have a `tenant_isolation` policy that only shows and accepts rows where
   `organization_id` matches `app.current_org`. Without it, a query that forgot its filter returns
   nothing rather than another company's data.
   - The organisation is set once per transaction from the access token (`set_tenant`, in
     `core/database.py`). The few places that must look across organisations do so explicitly in
     a `rls_bypass(...)` block: login and register (checking an email), WhatsApp and CRM webhooks
     (finding which organisation a message belongs to), the scheduled sync and the seed script.
   - RLS guards against missing filters in our own code. It is not a defence against SQL
     injection, since the bypass switch is a session setting. All queries go through SQLAlchemy
     with bound parameters, which is what prevents injection.
   - SQLite (local development) has no RLS; only the application filters apply there.

### Check that RLS is really on in production

Postgres skips RLS for superusers and roles with `BYPASSRLS`, even on forced tables. Open
**https://salesway-api.onrender.com/health/ready**:

- `"row_level_security": "enforced"` means you're done.
- `"NOT ENFORCED: this database user bypasses it"` means the Neon login in `DATABASE_URL` skips
  RLS. The app is still protected by layer 1, but layer 2 is off. To turn it on, give the app its
  own restricted login:

  1. In the Neon console, open **SQL Editor** and run the following (choose your own password):
     ```sql
     CREATE ROLE salesway_app LOGIN PASSWORD 'choose-a-long-random-password' NOSUPERUSER NOBYPASSRLS;
     GRANT USAGE ON SCHEMA public TO salesway_app;
     GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO salesway_app;
     GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO salesway_app;
     -- tables created by future migrations
     ALTER DEFAULT PRIVILEGES FOR ROLE neondb_owner IN SCHEMA public
       GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO salesway_app;
     ALTER DEFAULT PRIVILEGES FOR ROLE neondb_owner IN SCHEMA public
       GRANT USAGE, SELECT ON SEQUENCES TO salesway_app;
     ```
  2. In Render → **salesway-api** → **Environment**:
     - add `MIGRATION_DATABASE_URL` = the current connection string (the owner, which migrations need);
     - change `DATABASE_URL` to the same string with `neondb_owner:<password>` replaced by
       `salesway_app:<your password>`.
  3. Save, wait for the redeploy, and check `/health/ready` again.

The test suite runs every test on Postgres as a role without `BYPASSRLS` (see `tests/conftest.py`),
and `tests/test_rls.py` checks that each tenant table is protected and that reads and writes across
organisations are refused.

## Sign-in

- **Passwords** are hashed with bcrypt. New passwords need at least 10 characters with letters and
  numbers, mustn't be a common password and mustn't contain the email name (`core/passwords.py`).
- **Lockout:** 5 wrong passwords in a row lock the account for 15 minutes. An admin can unlock it at
  once with **Users → Reset password**. Unknown emails take as long to reject as wrong passwords, so
  the response doesn't reveal which emails have accounts.
- **Signing out everywhere:** tokens carry a version number. Changing your password (**Account**
  page), an admin reset, deactivation or a role change bumps it, which invalidates every existing
  access and refresh token for that person.
- **The shared demo** (organisation plan `demo`) has public passwords, so its passwords can't be
  changed and it has no lockout. Otherwise one visitor could lock everyone else out.
- **Still open:** tokens are stored in the browser's `localStorage`, which page scripts can read. The
  next step is an httpOnly cookie through a same-origin proxy.

## Responses and headers

- Every response carries an `X-Request-ID`. Unexpected errors return only "Something went wrong on
  our side. Reference: <id>", never a stack trace; the full error is in the logs under that id.
- **API:** `nosniff`, `X-Frame-Options: DENY`, a `default-src 'none'` content security policy (except
  on `/docs`), no-referrer, and HSTS outside development.
- **Web app** (`next.config.mjs`): `nosniff`, `DENY` framing, a strict referrer policy, no
  camera/microphone/location, HSTS, and a CSP that blocks framing, plugins, `<base>` tricks and
  forms posting elsewhere. The `X-Powered-By` header is off.
- **Logs** are one JSON line per request (request id, method, path, status, duration), without
  query strings, request bodies, tokens or passwords.
- **Error tracking:** set `SENTRY_DSN` to send errors to Sentry. Personal data (emails, IP
  addresses) is not sent.

## Health checks

| URL             | Answers                                                                   |
| --------------- | ------------------------------------------------------------------------- |
| `/health`       | Is the process up? (Render's health check; doesn't touch the database)    |
| `/health/ready` | Can it reach the database, and is row-level security enforced?            |

## Before anything goes live

GitHub Actions (`.github/workflows/ci.yml`) runs on every push and pull request:

- the backend tests twice, on SQLite and on Postgres 16 with RLS enforced;
- every migration from an empty database;
- the frontend type check and production build;
- `pip-audit` and `npm audit` (high and critical), so a dependency with a known vulnerability
  fails the build.

Render deploys only after these checks pass (`autoDeployTrigger: checksPass` in `render.yaml`).
Dependabot opens weekly pull requests for outdated or vulnerable packages, and CI checks each one.

## Secrets

- These are set in Render and never committed: `DATABASE_URL`, `JWT_SECRET`, `CREDENTIALS_KEY`,
  `SYNC_CRON_TOKEN`, and CRM and WhatsApp keys. `.env*` files are in `.gitignore`.
- CRM tokens are encrypted in the database (Fernet, keyed by `CREDENTIALS_KEY`).
- If the API starts in production with the default `JWT_SECRET`, it refuses to start.

## Not done yet

- Session tokens in httpOnly cookies (see above).
- Rate limits on login and register beyond the per-account lockout (an IP-based limit needs a
  shared store such as Redis once there is more than one server).
- Database backups beyond Neon's built-in point-in-time restore window, and a tested restore.
- A background job queue, so long syncs and model training don't run inside web requests.
