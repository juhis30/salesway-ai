import io

import pandas as pd

from app.tests.conftest import register_org, upload


def test_preview_reports_row_errors_and_confirm_imports_valid_rows(client):
    admin = register_org(client, "Acme", "admin@acme.in")
    preview = upload(client, admin, "products",
                     "Item Code,Item Name,Rate\n"
                     "P1,Atta,100\n"
                     "P2,,50\n"          # missing name
                     "P3,Oil,abc\n"      # bad price
                     "P1,Atta again,90\n"  # duplicate SKU in file
                     ",,\n", confirm=False)  # blank row ignored

    job = preview["job"]
    assert job["column_mapping"] == {"sku": "Item Code", "name": "Item Name", "unit_price": "Rate"}
    assert (job["total_rows"], job["valid_rows"], job["error_rows"]) == (4, 1, 3)
    errors = {(e["row"], e["field"]) for e in preview["errors"]}
    assert errors == {(3, "name"), (4, "unit_price"), (5, "sku")}

    # Nothing is written until confirmation.
    assert client.get("/api/v1/products", headers=admin).json()["total"] == 0
    done = client.post(f"/api/v1/imports/{job['id']}/confirm", headers=admin).json()
    assert done["status"] == "completed"
    assert done["result"] == {"created": 1, "updated": 0}
    assert client.get("/api/v1/products", headers=admin).json()["items"][0]["name"] == "Atta"

    # A completed import can't be confirmed twice.
    assert client.post(f"/api/v1/imports/{job['id']}/confirm", headers=admin).status_code == 409


def test_reimport_updates_existing_records(client):
    admin = register_org(client, "Acme", "admin@acme.in")
    upload(client, admin, "products", "sku,name,price\nP1,Atta,100\n")
    result = upload(client, admin, "products", "sku,name,price\np1,Atta 10kg,120\nP2,Oil,80\n")
    assert result["result"] == {"created": 1, "updated": 1}
    names = sorted(p["name"] for p in client.get("/api/v1/products", headers=admin).json()["items"])
    assert names == ["Atta 10kg", "Oil"]


def test_customer_import_creates_missing_territories(client):
    admin = register_org(client, "Acme", "admin@acme.in")
    result = upload(client, admin, "customers", "name,territory\nA,North\nB,North\nC,South\n")
    assert result["result"]["territories_created"] == 2
    assert sorted(t["name"] for t in client.get("/api/v1/territories", headers=admin).json()) == ["North", "South"]


def test_missing_required_column_rejects_file(client):
    admin = register_org(client, "Acme", "admin@acme.in")
    response = client.post(
        "/api/v1/imports/orders/preview",
        headers=admin,
        files={"file": ("o.csv", b"order_no,qty\n1,2\n", "text/csv")},
    )
    assert response.status_code == 422
    assert "order_date" in response.json()["detail"]


def test_explicit_column_mapping(client):
    admin = register_org(client, "Acme", "admin@acme.in")
    response = client.post(
        "/api/v1/imports/products/preview",
        headers=admin,
        files={"file": ("p.csv", b"Article,Title\nP1,Atta\n", "text/csv")},
        data={"mapping": '{"sku": "Article", "name": "Title"}'},
    )
    assert response.status_code == 201
    assert response.json()["job"]["valid_rows"] == 1


def test_unsupported_file_type(client):
    admin = register_org(client, "Acme", "admin@acme.in")
    response = client.post(
        "/api/v1/imports/products/preview", headers=admin,
        files={"file": ("p.txt", b"sku,name\nP1,A\n", "text/plain")},
    )
    assert response.status_code == 422


def test_excel_upload(client):
    admin = register_org(client, "Acme", "admin@acme.in")
    buffer = io.BytesIO()
    pd.DataFrame({"SKU": ["P1", "P2"], "Product Name": ["Atta", "Oil"], "Price": [100, 55.5]}).to_excel(buffer, index=False)
    response = client.post(
        "/api/v1/imports/products/preview", headers=admin,
        files={"file": ("p.xlsx", buffer.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert response.status_code == 201, response.text
    assert response.json()["job"]["valid_rows"] == 2


def test_order_with_one_bad_line_is_skipped_entirely(client, sales_org):
    admin = sales_org["admin"]
    preview = upload(client, admin, "orders",
                     "order_no,order_date,customer_code,salesperson_email,sku,qty,rate\n"
                     "SO-9,2026-10-01,C1,ravi@acme.in,P1,1,1000\n"
                     "SO-9,2026-10-01,C1,ravi@acme.in,NOPE,1,1000\n"
                     "SO-10,2026-10-01,C1,ravi@acme.in,P1,1,1000\n"
                     "SO-1,2026-10-05,C1,ravi@acme.in,P1,1,1000\n", confirm=False)
    assert preview["job"]["valid_rows"] == 2  # SO-10 and the duplicate SO-1
    assert any("already exist" in w for w in preview["job"]["warnings"])

    done = client.post(f"/api/v1/imports/{preview['job']['id']}/confirm", headers=admin).json()
    assert done["result"] == {"orders_created": 1, "lines_created": 1, "orders_skipped_existing": 1}


def test_order_lines_must_agree_on_header(client, sales_org):
    preview = upload(client, sales_org["admin"], "orders",
                     "order_no,order_date,customer_code,salesperson_email,sku,qty,rate\n"
                     "SO-20,2026-10-01,C1,ravi@acme.in,P1,1,1000\n"
                     "SO-20,2026-10-02,C1,ravi@acme.in,P2,1,500\n", confirm=False)
    assert preview["job"]["valid_rows"] == 0
