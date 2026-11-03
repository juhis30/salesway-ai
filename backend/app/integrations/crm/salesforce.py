"""Salesforce (read-only): Accounts, Products, Orders, Opportunities, Tasks and Events, via SOQL.

Salesforce has no read-only OAuth scope; connect with an integration user whose profile can only
read these objects. Changes are found with SystemModstamp; results page via nextRecordsUrl.
"""

from collections.abc import Iterator
from datetime import datetime, timezone
from urllib.parse import urlencode

from app.core.config import get_settings
from app.integrations.crm.base import ApiClient, Connection, Connector, as_date, open_stage, row, token_response


def customer_code(account_id: str) -> str:
    return f"SF-{account_id}"


def _since(field: str, since: datetime | None) -> str:
    if since is None:
        return ""
    stamp = since.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return f" WHERE {field} > {stamp}"


def _owner(record: dict) -> str:
    return (record.get("Owner") or {}).get("Email") or ""


QUERIES = {
    "customers": "SELECT Id, Name, AccountNumber, Type, Industry, BillingCity, Phone, Owner.Email FROM Account",
    "products": "SELECT Id, Name, ProductCode, Family FROM Product2",
    "orders": ("SELECT Id, OrderId, Order.EffectiveDate, Order.Status, Order.AccountId, Order.Owner.Email, "
               "Product2Id, Product2.ProductCode, Quantity, UnitPrice FROM OrderItem"),
    "opportunities": ("SELECT Id, Name, AccountId, StageName, IsClosed, IsWon, Amount, CloseDate, CreatedDate, "
                      "LeadSource, Probability, Owner.Email FROM Opportunity"),
    "tasks": "SELECT Id, Subject, ActivityDate, IsClosed, TaskSubtype, AccountId, WhatId, Owner.Email FROM Task",
    "events": "SELECT Id, Subject, ActivityDate, AccountId, WhatId, Owner.Email FROM Event",
}
MODIFIED = {"orders": "Order.SystemModstamp"}


class SalesforceConnector(Connector):
    provider = "salesforce"
    label = "Salesforce"
    description = "Accounts, products, orders, opportunities and activities (tasks, events)."
    entities = ("customers", "products", "orders", "opportunities", "activities")
    required_settings = ("salesforce_client_id", "salesforce_client_secret")

    @property
    def login_url(self) -> str:
        return get_settings().salesforce_login_url.rstrip("/")

    def authorize_url(self, state, redirect_uri):
        return f"{self.login_url}/services/oauth2/authorize?" + urlencode({
            "response_type": "code", "client_id": get_settings().salesforce_client_id,
            "redirect_uri": redirect_uri, "scope": "api refresh_token", "state": state,
        })

    def _token(self, http, **fields):
        s = get_settings()
        return http.post(f"{self.login_url}/services/oauth2/token", data={
            "client_id": s.salesforce_client_id, "client_secret": s.salesforce_client_secret, **fields,
        }).json()

    def exchange_code(self, http, code, redirect_uri):
        data = self._token(http, grant_type="authorization_code", code=code, redirect_uri=redirect_uri)
        tokens = token_response(data)
        instance = data.get("instance_url", "")
        return Connection(tokens=tokens, settings={"instance_url": instance},
                          account_label=instance.removeprefix("https://") or None)

    def refresh(self, http, tokens):
        # Salesforce access tokens carry no expiry; a 401 triggers this refresh.
        return token_response(self._token(http, grant_type="refresh_token", refresh_token=tokens.refresh_token), previous=tokens)

    def _query(self, api: ApiClient, name: str, since: datetime | None) -> Iterator[dict]:
        base = api.settings["instance_url"].rstrip("/")
        soql = QUERIES[name] + _since(MODIFIED.get(name, "SystemModstamp"), since)
        url = f"{base}/services/data/{get_settings().salesforce_api_version}/query"
        body = api.request("GET", url, params={"q": soql}).json()
        while True:
            yield from body.get("records", [])
            if body.get("done", True) or not body.get("nextRecordsUrl"):
                return
            body = api.request("GET", base + body["nextRecordsUrl"]).json()

    def fetch(self, api, entity, since):
        if entity == "customers":
            for r in self._query(api, "customers", since):
                yield row("customers", code=customer_code(r["Id"]), name=r.get("Name"), segment=r.get("Type") or r.get("Industry"),
                          city=r.get("BillingCity"), phone=r.get("Phone"), owner_email=_owner(r))
        elif entity == "products":
            for r in self._query(api, "products", since):
                yield row("products", sku=r.get("ProductCode") or f"SF-{r['Id']}", name=r.get("Name"), category=r.get("Family"))
        elif entity == "orders":
            for r in self._query(api, "orders", since):
                order = r.get("Order") or {}
                product = r.get("Product2") or {}
                yield row("orders", order_number=f"SF-{r['OrderId']}", order_date=as_date(order.get("EffectiveDate")),
                          customer=customer_code(order.get("AccountId") or ""), salesperson_email=_owner(order),
                          product=product.get("ProductCode") or f"SF-{r.get('Product2Id')}", quantity=r.get("Quantity"),
                          unit_price=r.get("UnitPrice"), status=order.get("Status"))
        elif entity == "opportunities":
            for r in self._query(api, "opportunities", since):
                closed = bool(r.get("IsClosed"))
                stage = ("won" if r.get("IsWon") else "lost") if closed else open_stage(r.get("Probability"))
                yield row("opportunities", code=f"SF-OPP-{r['Id']}", name=r.get("Name"),
                          customer=customer_code(r.get("AccountId") or ""), owner_email=_owner(r), stage=stage,
                          amount=r.get("Amount") or 0, opened_on=as_date(r.get("CreatedDate")),
                          expected_close=None if closed else as_date(r.get("CloseDate")),
                          closed_on=as_date(r.get("CloseDate")) if closed else None, source=r.get("LeadSource"))
        elif entity == "activities":
            today = datetime.now().date().isoformat()
            for name in ("tasks", "events"):
                for r in self._query(api, name, since):
                    day = as_date(r.get("ActivityDate"))
                    if not day:
                        continue
                    if name == "tasks":
                        status, kind = ("done" if r.get("IsClosed") else "planned"), "call"
                    else:
                        status, kind = ("done" if day <= today else "planned"), "meeting"
                    what = r.get("WhatId") or ""
                    yield row("activities", activity_date=day, salesperson_email=_owner(r), activity_type=kind,
                              customer=customer_code(r["AccountId"]) if r.get("AccountId") else None,
                              opportunity=f"SF-OPP-{what}" if what.startswith("006") else None,
                              status=status, notes=r.get("Subject"), external_id=f"salesforce:{name}:{r['Id']}")
