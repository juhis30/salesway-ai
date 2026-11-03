"""HubSpot (read-only): companies, products, deals, and calls / meetings / tasks.

Changed records come from the CRM search API filtered on hs_lastmodifieddate; owners are looked
up once per sync to turn owner ids into emails; deal and activity links to companies come from the
associations API. HubSpot has no standard sales-order object, so orders aren't synced from it.
"""

import base64
import hashlib
import hmac
import time
from collections.abc import Iterator
from datetime import datetime
from urllib.parse import urlencode

from app.core.config import get_settings
from app.integrations.crm.base import ApiClient, Connection, Connector, as_date, open_stage, row, token_response

API = "https://api.hubapi.com"
SCOPES = " ".join([
    "crm.objects.companies.read", "crm.objects.deals.read", "crm.objects.contacts.read",
    "crm.objects.owners.read", "e-commerce",
])
PAGE = 100

PROPERTIES = {
    "companies": ["name", "city", "phone", "industry", "type", "hubspot_owner_id"],
    "products": ["name", "hs_sku", "price"],
    "deals": ["dealname", "amount", "closedate", "createdate", "hubspot_owner_id", "hs_is_closed_won",
              "hs_is_closed", "hs_deal_stage_probability", "closed_lost_reason", "hs_analytics_source"],
    "calls": ["hs_timestamp", "hs_call_title", "hubspot_owner_id"],
    "meetings": ["hs_timestamp", "hs_meeting_title", "hubspot_owner_id"],
    "tasks": ["hs_timestamp", "hs_task_subject", "hs_task_status", "hubspot_owner_id"],
}


def customer_code(company_id: str) -> str:
    return f"HS-{company_id}"


def verify_signature(secret: str, method: str, uri: str, body: bytes, timestamp: str, signature: str, now: float | None = None) -> bool:
    """HubSpot webhook signature v3: base64(HMAC-SHA256(secret, method + uri + body + timestamp)), < 5 minutes old."""
    try:
        age = (now or time.time()) * 1000 - int(timestamp)
    except (TypeError, ValueError):
        return False
    if age > 5 * 60 * 1000:
        return False
    message = method.encode() + uri.encode() + body + timestamp.encode()
    expected = base64.b64encode(hmac.new(secret.encode(), message, hashlib.sha256).digest()).decode()
    return hmac.compare_digest(expected, signature or "")


class HubSpotConnector(Connector):
    provider = "hubspot"
    label = "HubSpot"
    description = "Companies, products, deals and activities (calls, meetings, tasks)."
    entities = ("customers", "products", "opportunities", "activities")
    not_synced = "HubSpot has no standard sales-order object, so orders aren't synced; upload them as CSV."
    required_settings = ("hubspot_client_id", "hubspot_client_secret")

    def authorize_url(self, state, redirect_uri):
        return "https://app.hubspot.com/oauth/authorize?" + urlencode({
            "client_id": get_settings().hubspot_client_id, "redirect_uri": redirect_uri, "scope": SCOPES, "state": state,
        })

    def _token(self, http, **fields):
        s = get_settings()
        return http.post(f"{API}/oauth/v1/token", data={
            "client_id": s.hubspot_client_id, "client_secret": s.hubspot_client_secret, **fields,
        }).json()

    def exchange_code(self, http, code, redirect_uri):
        tokens = token_response(self._token(http, grant_type="authorization_code", code=code, redirect_uri=redirect_uri))
        info = http.get(f"{API}/oauth/v1/access-tokens/{tokens.access_token}").json()
        return Connection(tokens=tokens, settings={"portal_id": str(info.get("hub_id", ""))},
                          account_label=info.get("hub_domain"))

    def refresh(self, http, tokens):
        return token_response(self._token(http, grant_type="refresh_token", refresh_token=tokens.refresh_token), previous=tokens)

    # --- reading ---------------------------------------------------------------------------

    def _owners(self, api: ApiClient) -> dict[str, str]:
        if "hubspot_owners" not in api.context:
            owners, after = {}, None
            while True:
                params = {"limit": 500, **({"after": after} if after else {})}
                body = api.request("GET", f"{API}/crm/v3/owners", params=params).json()
                owners.update({str(o["id"]): o.get("email", "") for o in body.get("results", [])})
                after = ((body.get("paging") or {}).get("next") or {}).get("after")
                if not after:
                    break
            api.context["hubspot_owners"] = owners
        return api.context["hubspot_owners"]

    def _search(self, api: ApiClient, obj: str, since: datetime | None) -> Iterator[dict]:
        after = None
        while True:
            body = {"properties": PROPERTIES[obj], "limit": PAGE,
                    "sorts": [{"propertyName": "hs_lastmodifieddate", "direction": "ASCENDING"}]}
            if since:
                body["filterGroups"] = [{"filters": [{"propertyName": "hs_lastmodifieddate", "operator": "GTE",
                                                      "value": str(int(since.timestamp() * 1000))}]}]
            if after:
                body["after"] = after
            page = api.request("POST", f"{API}/crm/v3/objects/{obj}/search", json=body).json()
            yield from page.get("results", [])
            after = ((page.get("paging") or {}).get("next") or {}).get("after")
            if not after:
                return

    def _companies_of(self, api: ApiClient, obj: str, ids: list[str]) -> dict[str, str]:
        """First associated company per record, in batches of 100."""
        out = {}
        for i in range(0, len(ids), 100):
            body = {"inputs": [{"id": x} for x in ids[i:i + 100]]}
            result = api.request("POST", f"{API}/crm/v4/associations/{obj}/companies/batch/read", json=body).json()
            for item in result.get("results", []):
                to = item.get("to") or []
                if to:
                    out[str(item["from"]["id"])] = str(to[0]["toObjectId"])
        return out

    def fetch(self, api, entity, since):
        owners = self._owners(api)
        if entity == "customers":
            for r in self._search(api, "companies", since):
                p = r["properties"]
                yield row("customers", code=customer_code(r["id"]), name=p.get("name"), segment=p.get("type") or p.get("industry"),
                          city=p.get("city"), phone=p.get("phone"), owner_email=owners.get(p.get("hubspot_owner_id") or "", ""))
        elif entity == "products":
            for r in self._search(api, "products", since):
                p = r["properties"]
                yield row("products", sku=p.get("hs_sku") or f"HS-{r['id']}", name=p.get("name"), unit_price=p.get("price"))
        elif entity == "opportunities":
            deals = list(self._search(api, "deals", since))
            companies = self._companies_of(api, "deals", [d["id"] for d in deals])
            for r in deals:
                p = r["properties"]
                closed = p.get("hs_is_closed") == "true"
                stage = ("won" if p.get("hs_is_closed_won") == "true" else "lost") if closed else \
                    open_stage(float(p["hs_deal_stage_probability"]) if p.get("hs_deal_stage_probability") else None)
                yield row("opportunities", code=f"HS-DEAL-{r['id']}", name=p.get("dealname"),
                          customer=customer_code(companies[r["id"]]) if r["id"] in companies else None,
                          owner_email=owners.get(p.get("hubspot_owner_id") or "", ""), stage=stage, amount=p.get("amount") or 0,
                          opened_on=as_date(p.get("createdate")), expected_close=None if closed else as_date(p.get("closedate")),
                          closed_on=as_date(p.get("closedate")) if closed else None, source=p.get("hs_analytics_source"),
                          lost_reason=p.get("closed_lost_reason"))
        elif entity == "activities":
            today = datetime.now().date().isoformat()
            for obj, kind, title in (("calls", "call", "hs_call_title"), ("meetings", "meeting", "hs_meeting_title"),
                                     ("tasks", "call", "hs_task_subject")):
                records = list(self._search(api, obj, since))
                companies = self._companies_of(api, obj, [r["id"] for r in records])
                for r in records:
                    p = r["properties"]
                    day = as_date(p.get("hs_timestamp"))
                    if not day:
                        continue
                    if obj == "tasks":
                        status = "done" if p.get("hs_task_status") == "COMPLETED" else "planned"
                    else:
                        status = "done" if day <= today else "planned"
                    yield row("activities", activity_date=day, salesperson_email=owners.get(p.get("hubspot_owner_id") or "", ""),
                              activity_type=kind, customer=customer_code(companies[r["id"]]) if r["id"] in companies else None,
                              status=status, notes=p.get(title), external_id=f"hubspot:{obj}:{r['id']}")
