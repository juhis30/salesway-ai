"""Zoho CRM (read-only): Accounts, Products, Sales Orders, Deals, Tasks, Calls and Events.

OAuth goes through the account's data centre (accounts.zoho.in for India); the token response says
which API domain to use (e.g. www.zohoapis.in). Records are listed 200 a page; after the first
sync, the If-Modified-Since header returns only records changed since the last successful sync.
"""

from collections.abc import Iterator
from datetime import datetime
from urllib.parse import urlencode


from app.core.config import get_settings
from app.integrations.crm.base import (
    ApiClient,
    Connection,
    Connector,
    Tokens,
    as_date,
    open_stage,
    row,
    token_response,
)

SCOPES = "ZohoCRM.modules.READ,ZohoCRM.users.READ,ZohoCRM.org.READ"
PER_PAGE = 200

FIELDS = {
    "Accounts": "Account_Name,Account_Number,Account_Type,Industry,Billing_City,Phone,Owner",
    "Products": "Product_Name,Product_Code,Product_Category,Unit_Price",
    "Sales_Orders": "Subject,Account_Name,Status,Created_Time,Owner,Ordered_Items",
    "Deals": "Deal_Name,Account_Name,Stage,Amount,Closing_Date,Created_Time,Lead_Source,Owner,Probability,Reason_For_Loss__s",
    "Tasks": "Subject,Due_Date,Status,What_Id,Owner",
    "Calls": "Subject,Call_Start_Time,What_Id,Owner",
    "Events": "Event_Title,Start_DateTime,What_Id,Owner",
}
WON = {"closed won"}
LOST = {"closed lost", "closed-lost to competition", "closed lost to competition"}


def customer_code(record_id: str) -> str:
    return f"ZOHO-{record_id}"


def _owner(record: dict) -> str:
    return (record.get("Owner") or {}).get("email", "")


def _lookup(record: dict, name: str) -> dict:
    return record.get(name) or {}


class ZohoConnector(Connector):
    provider = "zoho"
    label = "Zoho CRM"
    description = "Accounts, products, sales orders, deals and activities (tasks, calls, meetings)."
    entities = ("customers", "products", "orders", "opportunities", "activities")
    required_settings = ("zoho_client_id", "zoho_client_secret")

    @property
    def accounts_url(self) -> str:
        return get_settings().zoho_accounts_url.rstrip("/")

    def authorize_url(self, state, redirect_uri):
        s = get_settings()
        return f"{self.accounts_url}/oauth/v2/auth?" + urlencode({
            "scope": SCOPES, "client_id": s.zoho_client_id, "response_type": "code",
            "access_type": "offline", "prompt": "consent", "redirect_uri": redirect_uri, "state": state,
        })

    def exchange_code(self, http, code, redirect_uri):
        s = get_settings()
        data = http.post(f"{self.accounts_url}/oauth/v2/token", data={
            "grant_type": "authorization_code", "code": code, "client_id": s.zoho_client_id,
            "client_secret": s.zoho_client_secret, "redirect_uri": redirect_uri,
        }).json()
        tokens = token_response(data)
        api_domain = data.get("api_domain") or "https://www.zohoapis.in"
        label = None
        org = http.get(f"{api_domain}/crm/{s.zoho_api_version}/org", headers=self.auth_headers(tokens))
        if org.status_code == 200:
            label = ((org.json().get("org") or [{}])[0]).get("company_name")
        return Connection(tokens=tokens, settings={"api_domain": api_domain}, account_label=label)

    def refresh(self, http, tokens):
        s = get_settings()
        data = http.post(f"{self.accounts_url}/oauth/v2/token", data={
            "grant_type": "refresh_token", "refresh_token": tokens.refresh_token,
            "client_id": s.zoho_client_id, "client_secret": s.zoho_client_secret,
        }).json()
        return token_response(data, previous=tokens)

    def auth_headers(self, tokens: Tokens):
        return {"Authorization": f"Zoho-oauthtoken {tokens.access_token}"}

    # --- reading ---------------------------------------------------------------------------

    def _records(self, api: ApiClient, module: str, since: datetime | None) -> Iterator[dict]:
        base = f"{api.settings['api_domain']}/crm/{get_settings().zoho_api_version}/{module}"
        headers = {"If-Modified-Since": since.isoformat(timespec="seconds")} if since else {}
        params: dict = {"fields": FIELDS[module], "per_page": PER_PAGE, "page": 1}
        while True:
            response = api.request("GET", base, params=params, headers=headers)
            if response.status_code == 204:  # nothing (changed)
                return
            body = response.json()
            yield from body.get("data") or []
            info = body.get("info") or {}
            if not info.get("more_records"):
                return
            # Zoho allows page numbers up to 2,000 records; beyond that it hands out a page token.
            if info.get("next_page_token"):
                params = {"fields": FIELDS[module], "per_page": PER_PAGE, "page_token": info["next_page_token"]}
            else:
                params = {**params, "page": int(info.get("page", params.get("page", 1))) + 1}

    def fetch(self, api, entity, since):
        if entity == "customers":
            for r in self._records(api, "Accounts", since):
                yield row("customers", code=customer_code(r["id"]), name=r.get("Account_Name"),
                          segment=r.get("Account_Type") or r.get("Industry"), city=r.get("Billing_City"),
                          phone=r.get("Phone"), owner_email=_owner(r))
        elif entity == "products":
            for r in self._records(api, "Products", since):
                yield row("products", sku=r.get("Product_Code") or f"ZOHO-{r['id']}", name=r.get("Product_Name"),
                          category=r.get("Product_Category"), unit_price=r.get("Unit_Price"))
        elif entity == "orders":
            for r in self._records(api, "Sales_Orders", since):
                account = _lookup(r, "Account_Name")
                for item in r.get("Ordered_Items") or []:
                    product = _lookup(item, "Product_Name")
                    quantity = float(item.get("Quantity") or 0)
                    if quantity <= 0:
                        continue
                    gross = float(item.get("List_Price") or 0) * quantity
                    net = gross - float(item.get("Discount") or 0)  # SaleSway revenue is after discount, before tax
                    yield row("orders", order_number=f"ZOHO-SO-{r['id']}", order_date=as_date(r.get("Created_Time")),
                              customer=customer_code(account.get("id", "")), salesperson_email=_owner(r),
                              product=product.get("Product_Code") or product.get("name"), quantity=quantity,
                              unit_price=round(net / quantity, 2), tax_amount=item.get("Tax") or 0, status=r.get("Status"))
        elif entity == "opportunities":
            for r in self._records(api, "Deals", since):
                stage_name = (r.get("Stage") or "").strip().lower()
                stage = "won" if stage_name in WON else "lost" if stage_name in LOST else open_stage(r.get("Probability"))
                closed = stage in ("won", "lost")
                yield row("opportunities", code=f"ZOHO-DEAL-{r['id']}", name=r.get("Deal_Name"),
                          customer=customer_code(_lookup(r, "Account_Name").get("id", "")), owner_email=_owner(r),
                          stage=stage, amount=r.get("Amount") or 0, opened_on=as_date(r.get("Created_Time")),
                          expected_close=None if closed else as_date(r.get("Closing_Date")),
                          closed_on=as_date(r.get("Closing_Date")) if closed else None,
                          source=r.get("Lead_Source"), lost_reason=r.get("Reason_For_Loss__s"))
        elif entity == "activities":
            for module, kind, date_field, title in (("Tasks", "call", "Due_Date", "Subject"),
                                                    ("Calls", "call", "Call_Start_Time", "Subject"),
                                                    ("Events", "meeting", "Start_DateTime", "Event_Title")):
                for r in self._records(api, module, since):
                    what = _lookup(r, "What_Id")
                    linked = r.get("$se_module")
                    day = as_date(r.get(date_field))
                    if not day:
                        continue
                    if module == "Tasks":
                        status = "done" if (r.get("Status") or "").lower() == "completed" else "planned"
                    else:
                        status = "done" if day <= datetime.now().date().isoformat() else "planned"
                    yield row("activities", activity_date=day, salesperson_email=_owner(r), activity_type=kind,
                              customer=customer_code(what["id"]) if linked == "Accounts" and what.get("id") else None,
                              opportunity=f"ZOHO-DEAL-{what['id']}" if linked == "Deals" and what.get("id") else None,
                              status=status, notes=r.get(title), external_id=f"zoho:{module}:{r['id']}")
