import warnings

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import SAWarning
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.database import Base, get_db, make_engine
from app.main import app

# SQLite stores Numeric as float internally; harmless for tests, noisy in output.
warnings.filterwarnings("ignore", category=SAWarning, message=".*Decimal objects natively.*")

PASSWORD = "Secret@123"


@pytest.fixture
def db_session():
    engine = make_engine("sqlite://", poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    session = Session()
    yield session
    session.close()
    engine.dispose()


@pytest.fixture
def client(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def auth_headers(client: TestClient, email: str, password: str = PASSWORD) -> dict[str, str]:
    response = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def register_org(client: TestClient, org_name: str, admin_email: str) -> dict[str, str]:
    response = client.post(
        "/api/v1/auth/register",
        json={
            "organization_name": org_name,
            "full_name": "Admin User",
            "email": admin_email,
            "password": PASSWORD,
        },
    )
    assert response.status_code == 201, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def create_user(client: TestClient, admin: dict, email: str, role: str, **extra) -> dict:
    response = client.post(
        "/api/v1/users",
        headers=admin,
        json={"email": email, "full_name": email.split("@")[0].title(), "password": PASSWORD, "role": role, **extra},
    )
    assert response.status_code == 201, response.text
    return response.json()


def upload(client: TestClient, headers: dict, entity: str, csv_text: str, confirm: bool = True) -> dict:
    response = client.post(
        f"/api/v1/imports/{entity}/preview",
        headers=headers,
        files={"file": (f"{entity}.csv", csv_text.encode(), "text/csv")},
    )
    assert response.status_code == 201, response.text
    preview = response.json()
    if not confirm:
        return preview
    response = client.post(f"/api/v1/imports/{preview['job']['id']}/confirm", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


@pytest.fixture
def sales_org(client):
    """An org with two reps, two customers, two products, a handful of orders and targets.

    Expected October 2026 revenue (confirmed/completed only, net of tax):
      ravi:  SO-1 (2x1000 + 1x500 = 2500) + SO-2 (3x500 = 1500)   = 4000
      sita:  SO-3 (5x1000 = 5000)                                  = 5000
      excluded: SO-4 cancelled (9999), SO-5 pending (7777)
    September: ravi SO-6 = 2000.
    """
    admin = register_org(client, "Acme Traders", "admin@acme.in")
    ravi = create_user(client, admin, "ravi@acme.in", "sales_executive")
    sita = create_user(client, admin, "sita@acme.in", "sales_executive")

    upload(client, admin, "customers",
           "customer_code,customer_name,segment,territory,salesperson_email\n"
           "C1,Gupta Stores,Kirana,North,ravi@acme.in\n"
           "C2,Mehta Mart,Modern Trade,South,sita@acme.in\n")
    upload(client, admin, "products",
           "sku,product_name,category,price\n"
           "P1,Atta 10kg,Staples,1000\n"
           "P2,Oil 1L,Edible Oil,500\n")
    upload(client, admin, "orders",
           "order_no,order_date,customer_code,salesperson_email,sku,qty,rate,gst_amount,status\n"
           "SO-1,2026-10-05,C1,ravi@acme.in,P1,2,1000,100,completed\n"
           "SO-1,2026-10-05,C1,ravi@acme.in,P2,1,500,25,completed\n"
           "SO-2,15/10/2026,C1,ravi@acme.in,P2,3,500,75,delivered\n"
           "SO-3,2026-10-20,C2,sita@acme.in,P1,5,1000,250,confirmed\n"
           "SO-4,2026-10-21,C2,sita@acme.in,P1,1,9999,0,cancelled\n"
           "SO-5,2026-10-22,C2,sita@acme.in,P1,1,7777,0,pending\n"
           "SO-6,2026-09-10,C1,ravi@acme.in,P1,2,1000,0,completed\n")
    upload(client, admin, "targets",
           "salesperson_email,month,target\n"
           "ravi@acme.in,2026-10,8000\n"
           "sita@acme.in,2026-10,5000\n")
    return {"admin": admin, "ravi": ravi, "sita": sita}
