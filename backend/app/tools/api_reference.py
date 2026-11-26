"""Print docs/api.md: every endpoint, generated from the app's OpenAPI schema.

    python -m app.tools.api_reference > ../docs/api.md
"""

import os
import sys

os.environ.setdefault("RUN_WORKER", "false")

from app.main import app  # noqa: E402

ORDER = ["health", "auth", "organization", "customers & products", "data import", "analytics", "insights",
         "predictions", "assistant", "whatsapp", "integrations", "jobs"]

HEADER = """# API reference

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
"""


def main() -> None:
    spec = app.openapi()
    groups: dict[str, list[tuple[str, str, str]]] = {}
    for path, operations in spec["paths"].items():
        for method, op in operations.items():
            tag = (op.get("tags") or ["other"])[0]
            text = (op.get("description") or op.get("summary") or "").strip().split("\n")[0]
            groups.setdefault(tag, []).append((path, method.upper(), text.replace("|", "/")))

    out = [HEADER]
    for tag in sorted(groups, key=lambda t: ORDER.index(t) if t in ORDER else len(ORDER)):
        out += [f"## {tag[0].upper() + tag[1:]}", "", "| Method | Path | What it does |", "| --- | --- | --- |"]
        out += [f"| {method} | `{path}` | {text} |" for path, method, text in sorted(groups[tag])]
        out.append("")
    sys.stdout.write("\n".join(out))


if __name__ == "__main__":
    main()
