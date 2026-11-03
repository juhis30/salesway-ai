"""Sandbox CRM: a built-in pretend CRM for trying the sync without any account.

It "contains" a small, fixed set of accounts, products, deals and activities owned by the
organisation's own salespeople, plus one new order per day, so a second sync shows only what
changed. Nothing leaves SaleSway.
"""

from datetime import date, datetime, timedelta

from app.integrations.crm.base import Connection, Connector, Tokens, row

ACCOUNTS = [
    ("SBX-A1", "Sandbox Traders", "Wholesaler", "Pune"),
    ("SBX-A2", "Sandbox Supermart", "Modern Trade", "Mumbai"),
    ("SBX-A3", "Sandbox Kirana", "Kirana", "Delhi"),
]
PRODUCTS = [("SBX-P1", "Sandbox Atta 10kg", "Staples", 520), ("SBX-P2", "Sandbox Oil 1L", "Edible Oil", 160)]


class SandboxConnector(Connector):
    provider = "sandbox"
    label = "Sandbox CRM"
    description = "A built-in pretend CRM with a few records, for trying the sync without a real account."
    entities = ("customers", "products", "orders", "opportunities", "activities")
    uses_oauth = False

    def exchange_code(self, http, code, redirect_uri):
        return Connection(tokens=Tokens(), settings={}, account_label="Built-in sample data")

    def auth_headers(self, tokens):
        return {}

    def fetch(self, api, entity, since):
        owners = api.context.get("owner_emails") or []
        if not owners:
            return
        owner = lambda i: owners[i % len(owners)]  # noqa: E731
        fixed = since is None  # the fixed records count as changed only on the first sync
        today = api.context.get("today") or date.today()
        if entity == "customers" and fixed:
            for i, (code, name, segment, city) in enumerate(ACCOUNTS):
                yield row("customers", code=code, name=name, segment=segment, city=city, owner_email=owner(i))
        elif entity == "products" and fixed:
            for sku, name, category, price in PRODUCTS:
                yield row("products", sku=sku, name=name, category=category, unit_price=price)
        elif entity == "orders":
            days = [today - timedelta(days=d) for d in range(7, -1, -1)] if fixed else [today]
            for day in days:
                account = day.toordinal() % len(ACCOUNTS)  # each day's order goes to the next account in turn
                for sku, _, _, price in PRODUCTS:
                    yield row("orders", order_number=f"SBX-{day:%Y%m%d}", order_date=day.isoformat(),
                              customer=ACCOUNTS[account][0], salesperson_email=owner(account), product=sku,
                              quantity=10 + day.toordinal() % 7, unit_price=price, status="Delivered")
        elif entity == "opportunities" and fixed:
            yield row("opportunities", code="SBX-D1", name="Sandbox Traders – Annual contract", customer="SBX-A1",
                      owner_email=owner(0), stage="Negotiation/Review", amount=500000,
                      opened_on=(today - timedelta(days=20)).isoformat(), expected_close=(today + timedelta(days=25)).isoformat())
        elif entity == "activities" and fixed:
            yield row("activities", activity_date=(today - timedelta(days=2)).isoformat(), salesperson_email=owner(0),
                      activity_type="meeting", customer="SBX-A1", opportunity="SBX-D1", status="done",
                      notes="Pricing discussion", external_id="sandbox:1")
            yield row("activities", activity_date=(today + timedelta(days=3)).isoformat(), salesperson_email=owner(1),
                      activity_type="visit", customer="SBX-A2", status="planned", notes="Shelf review",
                      external_id="sandbox:2")
