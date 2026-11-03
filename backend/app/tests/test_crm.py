"""CRM connectors against simulated APIs shaped like Zoho, HubSpot and Salesforce, plus the sync engine and API."""

import json
import time
from datetime import date
from decimal import Decimal
from urllib.parse import parse_qs, urlparse

import httpx
import jwt
import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.integrations.crm import base
from app.integrations.crm.hubspot import verify_signature
from app.integrations.crm.sync import run_sync
from app.models import Customer, Integration, Opportunity, Product, SalesActivity, SalesOrder, SyncJob
from app.tests.conftest import auth_headers, register_org


def make_integration(db, org_id, provider, settings, access="at1", refresh="rt1"):
    integration = Integration(organization_id=org_id, provider=provider, status="connected", settings=settings, cursors={},
                              credentials=base.encrypt(base.Tokens(access, refresh, time.time() + 3600)))
    db.add(integration)
    db.commit()
    return integration


def org_id(client, headers):
    import uuid

    return uuid.UUID(client.get("/api/v1/auth/me", headers=headers).json()["organization"]["id"])


def no_sleep(_):
    pass


# --- Zoho ---------------------------------------------------------------------------------------


class FakeZoho:
    """Enough of Zoho CRM's REST API: paging, If-Modified-Since, token refresh and throttling."""

    def __init__(self):
        self.calls, self.throttled = [], False
        self.changed = False  # what a sync with If-Modified-Since sees

    def __call__(self, request: httpx.Request) -> httpx.Response:
        url = urlparse(str(request.url))
        self.calls.append((request.method, url.path, request.headers.get("If-Modified-Since")))
        if url.path == "/oauth/v2/token":
            form = parse_qs(request.content.decode())
            if form["grant_type"] == ["refresh_token"]:
                assert form["refresh_token"] == ["rt1"]
                return httpx.Response(200, json={"access_token": "at2", "expires_in": 3600})
            return httpx.Response(200, json={"access_token": "at1", "refresh_token": "rt1",
                                             "api_domain": "https://www.zohoapis.in", "expires_in": 3600})
        if request.headers.get("Authorization") == "Zoho-oauthtoken expired":
            return httpx.Response(401, json={"code": "INVALID_TOKEN"})
        module = url.path.rsplit("/", 1)[-1]
        params = parse_qs(url.query)
        if module == "org":
            return httpx.Response(200, json={"org": [{"company_name": "Acme on Zoho"}]})
        assert "fields" in params  # Zoho requires a field list when listing records
        if module == "Products" and not self.throttled:
            self.throttled = True
            return httpx.Response(429, headers={"Retry-After": "2"})
        incremental = request.headers.get("If-Modified-Since") is not None
        if incremental and not self.changed:
            return httpx.Response(204)
        data = (CHANGED if incremental else ZOHO).get(module, [])
        if not data:
            return httpx.Response(204)
        if module == "Accounts" and not incremental:  # two pages
            page = int(params["page"][0])
            return httpx.Response(200, json={"data": data[:2] if page == 1 else data[2:],
                                             "info": {"page": page, "more_records": page == 1}})
        return httpx.Response(200, json={"data": data, "info": {"page": 1, "more_records": False}})


RAVI = {"name": "Ravi", "id": "u1", "email": "ravi@acme.in"}
SITA = {"name": "Sita", "id": "u2", "email": "sita@acme.in"}
ORDER = {"id": "9001", "Subject": "Oct order", "Account_Name": {"name": "Zoho Traders", "id": "111"}, "Status": "Delivered",
         "Created_Time": "2026-10-10T10:00:00+05:30", "Owner": RAVI,
         "Ordered_Items": [{"Product_Name": {"name": "Zoho Atta", "Product_Code": "ZA-1", "id": "p1"},
                            "Quantity": 4, "List_Price": 500, "Discount": 200, "Tax": 90}]}
TASK = {"id": "T1", "Subject": "Follow up", "Due_Date": "2026-11-20", "Status": "Not Started",
        "What_Id": {"name": "Zoho Traders", "id": "111"}, "$se_module": "Accounts", "Owner": RAVI}
ZOHO = {
    "Accounts": [
        {"id": "111", "Account_Name": "Zoho Traders", "Account_Type": "Distributor", "Billing_City": "Pune", "Owner": RAVI},
        {"id": "222", "Account_Name": "Zoho Mart", "Industry": "Retail", "Owner": SITA},
        {"id": "333", "Account_Name": "Ghost Stores", "Owner": {"email": "ghost@elsewhere.in"}},
    ],
    "Products": [{"id": "p1", "Product_Name": "Zoho Atta", "Product_Code": "ZA-1", "Product_Category": "Staples", "Unit_Price": 500}],
    "Sales_Orders": [ORDER],
    "Deals": [
        {"id": "D1", "Deal_Name": "Big win", "Account_Name": {"id": "111"}, "Stage": "Closed Won", "Amount": 100000,
         "Closing_Date": "2026-10-01", "Created_Time": "2026-09-01T09:00:00+05:30", "Owner": RAVI},
        {"id": "D2", "Deal_Name": "In talks", "Account_Name": {"id": "222"}, "Stage": "Negotiation/Review", "Probability": 75,
         "Amount": 40000, "Closing_Date": "2026-12-01", "Created_Time": "2026-10-20T09:00:00+05:30", "Owner": SITA},
    ],
    "Tasks": [TASK],
}
CHANGED = {"Sales_Orders": [{**ORDER, "Status": "Cancelled"}], "Tasks": [{**TASK, "Status": "Completed"}]}


def test_zoho_sync_end_to_end(client, db_session, sales_org):
    zoho = FakeZoho()
    oid = org_id(client, sales_org["admin"])
    integration = make_integration(db_session, oid, "zoho", {"api_domain": "https://www.zohoapis.in"}, access="expired")
    http = httpx.Client(transport=httpx.MockTransport(zoho))

    job = run_sync(db_session, integration, http=http, sleep=no_sleep)
    assert job.status == "partial"  # one account's owner isn't a SaleSway user
    assert job.counts["customers"] == {"fetched": 3, "created": 2, "updated": 0, "territories_created": 0, "errors": 1}
    assert job.errors[0]["entity"] == "customers" and "ghost@elsewhere.in" in job.errors[0]["message"]

    traders = db_session.scalar(select(Customer).where(Customer.code == "ZOHO-111"))
    assert (traders.name, traders.segment, traders.city) == ("Zoho Traders", "Distributor", "Pune")
    order = db_session.scalar(select(SalesOrder).where(SalesOrder.order_number == "ZOHO-SO-9001"))
    assert (order.net_amount, order.tax_amount, order.status.value, order.source) == (Decimal("1800.00"), Decimal("90.00"), "completed", "sync:zoho")
    deals = {d.code: d for d in db_session.scalars(select(Opportunity).where(Opportunity.code.like("ZOHO-DEAL-%")))}
    assert deals["ZOHO-DEAL-D1"].stage.value == "won" and deals["ZOHO-DEAL-D1"].closed_on == date(2026, 10, 1)
    assert deals["ZOHO-DEAL-D2"].stage.value == "negotiation" and deals["ZOHO-DEAL-D2"].closed_on is None
    task = db_session.scalar(select(SalesActivity).where(SalesActivity.external_id == "zoho:Tasks:T1"))
    assert task.status.value == "planned"

    # The expired token was refreshed once and the new one saved, encrypted.
    assert base.decrypt(integration.credentials).access_token == "at2"
    assert "at2" not in integration.credentials and "rt1" not in integration.credentials
    # Throttled once: retried after the Retry-After wait.
    assert zoho.throttled
    assert set(integration.cursors) == {"customers", "products", "orders", "opportunities", "activities"}

    # Second sync asks only for changes: the order was cancelled and the task completed in Zoho.
    zoho.changed = True
    again = run_sync(db_session, integration, http=http, sleep=no_sleep)
    assert all(since for method, path, since in zoho.calls[-7:] if "/crm/" in path)
    assert again.counts["orders"]["orders_status_updated"] == 1
    db_session.refresh(order)
    assert order.status.value == "cancelled"
    assert again.counts["activities"] == {"fetched": 1, "created": 0, "updated": 1, "skipped_existing": 0, "errors": 0}
    db_session.refresh(task)
    assert task.status.value == "done"
    assert db_session.scalar(select(SalesActivity).where(SalesActivity.external_id.like("zoho:%")).with_only_columns(SalesActivity.id).limit(2)) is not None
    assert len(db_session.scalars(select(SalesActivity).where(SalesActivity.external_id == "zoho:Tasks:T1")).all()) == 1


def test_revoked_access_asks_for_reconnect(client, db_session, sales_org):
    def handler(request):
        return httpx.Response(401, json={"code": "INVALID_TOKEN"})

    integration = make_integration(db_session, org_id(client, sales_org["admin"]), "zoho",
                                   {"api_domain": "https://www.zohoapis.in"}, refresh="")
    job = run_sync(db_session, integration, http=httpx.Client(transport=httpx.MockTransport(handler)), sleep=no_sleep)
    db_session.refresh(integration)
    assert (job.status, integration.status) == ("failed", "needs_reconnect")
    assert db_session.scalar(select(Customer).where(Customer.code.like("ZOHO-%"))) is None  # nothing half-written


# --- HubSpot ------------------------------------------------------------------------------------


def test_hubspot_sync(client, db_session, sales_org):
    searches = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/crm/v3/owners":
            return httpx.Response(200, json={"results": [{"id": "7", "email": "ravi@acme.in"}, {"id": "8", "email": "sita@acme.in"}]})
        if path.endswith("/search"):
            body = json.loads(request.content)
            obj = path.split("/")[-2]
            searches.append((obj, "filterGroups" in body, body.get("after")))
            pages = {
                "companies": [[{"id": "501", "properties": {"name": "HS Retail", "city": "Delhi", "type": "Customer", "hubspot_owner_id": "7"}}],
                              [{"id": "502", "properties": {"name": "HS Foods", "hubspot_owner_id": "8"}}]],
                "products": [[{"id": "601", "properties": {"name": "HS Rice", "hs_sku": "HSR-1", "price": "300"}}]],
                "deals": [[{"id": "701", "properties": {"dealname": "HS deal", "amount": "25000", "createdate": "2026-10-02T00:00:00Z",
                                                        "closedate": "2026-11-30T00:00:00Z", "hubspot_owner_id": "8",
                                                        "hs_is_closed": "false", "hs_deal_stage_probability": "0.3"}}]],
                "calls": [[{"id": "801", "properties": {"hs_timestamp": "2026-10-15T08:00:00Z", "hs_call_title": "Intro", "hubspot_owner_id": "7"}}]],
                "meetings": [[]], "tasks": [[]],
            }[obj]
            index = int(body.get("after") or 0)
            more = index + 1 < len(pages)
            return httpx.Response(200, json={"results": pages[index], **({"paging": {"next": {"after": str(index + 1)}}} if more else {})})
        if "/associations/" in path:
            ids = [i["id"] for i in json.loads(request.content)["inputs"]]
            target = {"701": "502", "801": "501"}
            return httpx.Response(200, json={"results": [{"from": {"id": i}, "to": [{"toObjectId": int(target[i])}]} for i in ids if i in target]})
        return httpx.Response(404)

    integration = make_integration(db_session, org_id(client, sales_org["admin"]), "hubspot", {"portal_id": "42"})
    job = run_sync(db_session, integration, http=httpx.Client(transport=httpx.MockTransport(handler)), sleep=no_sleep)
    assert job.status == "succeeded", job.errors
    assert ("companies", False, "1") in searches  # second page requested
    assert "orders" not in job.counts  # HubSpot has no standard orders
    deal = db_session.scalar(select(Opportunity).where(Opportunity.code == "HS-DEAL-701"))
    customer = db_session.get(Customer, deal.customer_id)
    assert (customer.name, deal.stage.value, deal.amount) == ("HS Foods", "qualification", Decimal("25000.00"))
    call = db_session.scalar(select(SalesActivity).where(SalesActivity.external_id == "hubspot:calls:801"))
    assert db_session.get(Customer, call.customer_id).name == "HS Retail"
    assert db_session.scalar(select(Product).where(Product.sku == "HSR-1")).unit_price == Decimal("300.00")

    run_sync(db_session, integration, http=httpx.Client(transport=httpx.MockTransport(handler)), sleep=no_sleep)
    assert ("companies", True, None) in searches  # the second sync filters on last-modified


def test_hubspot_webhook_signature():
    body, now = b'[{"portalId": 42}]', time.time()
    ts = str(int(now * 1000))
    import base64
    import hashlib
    import hmac

    good = base64.b64encode(hmac.new(b"s3cret", b"POSThttps://api.example/webhooks/hubspot" + body + ts.encode(), hashlib.sha256).digest()).decode()
    assert verify_signature("s3cret", "POST", "https://api.example/webhooks/hubspot", body, ts, good, now)
    assert not verify_signature("other", "POST", "https://api.example/webhooks/hubspot", body, ts, good, now)
    assert not verify_signature("s3cret", "POST", "https://api.example/webhooks/hubspot", body, ts, good, now + 600)  # too old


# --- Salesforce ---------------------------------------------------------------------------------


def test_salesforce_sync(client, db_session, sales_org):
    queries = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/query/next-page"):
            return httpx.Response(200, json={"done": True, "records": [
                {"Id": "001B", "Name": "SF Second", "Owner": {"Email": "sita@acme.in"}}]})
        soql = request.url.params["q"]
        queries.append(soql)
        table = soql.split(" FROM ")[1].split(" ")[0]
        records = {
            "Account": [{"Id": "001A", "Name": "SF First", "Type": "Customer - Direct", "BillingCity": "Mumbai", "Owner": {"Email": "ravi@acme.in"}}],
            "Product2": [{"Id": "01tX", "Name": "SF Oil", "ProductCode": "SFO-1", "Family": "Oils"}],
            "OrderItem": [{"Id": "802A", "OrderId": "801A", "Order": {"EffectiveDate": "2026-10-12", "Status": "Activated", "AccountId": "001A",
                                                                        "Owner": {"Email": "ravi@acme.in"}},
                           "Product2Id": "01tX", "Product2": {"ProductCode": "SFO-1"}, "Quantity": 3.0, "UnitPrice": 150.0}],
            "Opportunity": [{"Id": "006A", "Name": "SF opp", "AccountId": "001B", "IsClosed": True, "IsWon": False, "Amount": 9000,
                             "CloseDate": "2026-10-20", "CreatedDate": "2026-09-15T10:00:00.000+0000", "Owner": {"Email": "sita@acme.in"}}],
            "Task": [{"Id": "00TA", "Subject": "Call back", "ActivityDate": "2026-10-13", "IsClosed": True, "AccountId": "001A",
                      "WhatId": "006A", "Owner": {"Email": "ravi@acme.in"}}],
            "Event": [],
        }[table]
        if table == "Account" and " WHERE " not in soql:
            return httpx.Response(200, json={"done": False, "records": records,
                                             "nextRecordsUrl": "/services/data/v62.0/query/next-page"})
        return httpx.Response(200, json={"done": True, "records": records})

    integration = make_integration(db_session, org_id(client, sales_org["admin"]), "salesforce",
                                   {"instance_url": "https://acme.my.salesforce.com"})
    job = run_sync(db_session, integration, http=httpx.Client(transport=httpx.MockTransport(handler)), sleep=no_sleep)
    assert job.status == "succeeded", job.errors
    assert job.counts["customers"]["created"] == 2  # both pages
    order = db_session.scalar(select(SalesOrder).where(SalesOrder.order_number == "SF-801A"))
    assert (order.status.value, order.net_amount) == ("confirmed", Decimal("450.00"))
    assert db_session.scalar(select(Opportunity).where(Opportunity.code == "SF-OPP-006A")).stage.value == "lost"
    task = db_session.scalar(select(SalesActivity).where(SalesActivity.external_id == "salesforce:tasks:00TA"))
    assert task.opportunity_id is not None and task.status.value == "done"

    run_sync(db_session, integration, http=httpx.Client(transport=httpx.MockTransport(handler)), sleep=no_sleep)
    assert any("WHERE SystemModstamp > 20" in q for q in queries)
    assert any("WHERE Order.SystemModstamp > 20" in q for q in queries)


# --- API: connecting, sandbox, scope -------------------------------------------------------------


@pytest.fixture
def zoho_configured(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "zoho_client_id", "zid")
    monkeypatch.setattr(s, "zoho_client_secret", "zsecret")


def test_zoho_oauth_flow(client, db_session, sales_org, zoho_configured, monkeypatch):
    admin = sales_org["admin"]
    started = client.post("/api/v1/integrations/zoho/connect", headers=admin).json()
    url = urlparse(started["authorize_url"])
    query = parse_qs(url.query)
    assert url.netloc == "accounts.zoho.in" and query["access_type"] == ["offline"]
    assert query["scope"] == ["ZohoCRM.modules.READ,ZohoCRM.users.READ,ZohoCRM.org.READ"]  # read-only
    assert query["redirect_uri"] == ["http://localhost:8000/api/v1/integrations/zoho/callback"]

    bad = client.get("/api/v1/integrations/zoho/callback", params={"state": "forged", "code": "c"}, follow_redirects=False)
    assert "error=" in bad.headers["location"]

    monkeypatch.setattr("app.api.v1.integrations.default_http", lambda: httpx.Client(transport=httpx.MockTransport(FakeZoho())))
    done = client.get("/api/v1/integrations/zoho/callback", params={"state": query["state"][0], "code": "c"}, follow_redirects=False)
    assert done.headers["location"] == "http://localhost:3000/integrations?connected=zoho"
    listed = {p["provider"]: p for p in client.get("/api/v1/integrations", headers=admin).json()}
    assert listed["zoho"]["integration"]["status"] == "connected"
    assert listed["zoho"]["integration"]["account_label"] == "Acme on Zoho"
    assert "credentials" not in json.dumps(listed)

    # A state from one provider can't complete another.
    other = client.get("/api/v1/integrations/hubspot/callback", params={"state": query["state"][0], "code": "c"}, follow_redirects=False)
    assert "error=" in other.headers["location"]


def test_unconfigured_provider_says_what_is_missing(client, sales_org):
    response = client.post("/api/v1/integrations/salesforce/connect", headers=sales_org["admin"])
    assert response.status_code == 422 and "SALESFORCE_CLIENT_ID" in response.json()["detail"]


def test_sandbox_connect_sync_and_scope(client, db_session, sales_org, monkeypatch):
    admin = sales_org["admin"]
    assert client.post("/api/v1/integrations/sandbox/connect", headers=admin).json() == {"connected": True}
    sandbox = next(p for p in client.get("/api/v1/integrations", headers=admin).json() if p["provider"] == "sandbox")
    iid = sandbox["integration"]["id"]

    first = client.post(f"/api/v1/integrations/{iid}/sync", headers=admin).json()
    assert first["status"] == "succeeded", first["errors"]
    assert first["counts"]["customers"]["created"] == 3 and first["counts"]["orders"]["orders_created"] == 8
    second = client.post(f"/api/v1/integrations/{iid}/sync", headers=admin).json()
    assert second["counts"]["orders"]["orders_created"] == 0  # today's order already synced
    assert "customers" in second["counts"] and second["counts"]["customers"]["fetched"] == 0
    assert len(client.get(f"/api/v1/integrations/{iid}/jobs", headers=admin).json()) == 2

    ravi = auth_headers(client, "ravi@acme.in")
    assert client.get("/api/v1/integrations", headers=ravi).status_code == 403
    rival = register_org(client, "Rival Co", "admin@rival.in")
    assert client.post(f"/api/v1/integrations/{iid}/sync", headers=rival).status_code == 404
    assert client.delete(f"/api/v1/integrations/{iid}", headers=rival).status_code == 404

    # Scheduled syncs need the shared token.
    assert client.post("/api/v1/integrations/sync-due").status_code == 403
    monkeypatch.setattr(get_settings(), "sync_cron_token", "cron-secret")
    assert client.post("/api/v1/integrations/sync-due", headers={"X-Sync-Token": "wrong"}).status_code == 403
    assert client.post("/api/v1/integrations/sync-due", headers={"X-Sync-Token": "cron-secret"}).json()["synced"] == 0  # just synced

    assert client.delete(f"/api/v1/integrations/{iid}", headers=admin).status_code == 204
    assert db_session.scalar(select(Customer).where(Customer.code == "SBX-A1")) is not None  # synced data stays
    assert db_session.scalar(select(SyncJob)) is None  # history goes with the connection


def test_hubspot_webhook_rejects_bad_signatures(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "hubspot_client_secret", "s3cret")
    response = client.post("/api/v1/webhooks/hubspot", content=b"[]", headers={"X-HubSpot-Signature-v3": "nope",
                                                                              "X-HubSpot-Request-Timestamp": str(int(time.time() * 1000))})
    assert response.status_code == 403
