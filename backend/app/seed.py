"""Create a demo organisation with ~12 months of synthetic FMCG distribution data.

    python -m app.seed            # create the demo org (skips if it already exists)
    python -m app.seed --reset    # delete the demo org and recreate it

Users, teams and territories are created directly. Customers, products, orders and targets
are written to data/sample/*.csv and loaded through the real import pipeline, so the seed
also exercises the same validation a customer upload goes through.
"""

import argparse
import csv
import io
import random
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.analytics.periods import add_months, month_start, org_today
from app.core.database import SessionLocal
from app.core.security import hash_password
from app.ml.train import train_organization
from app.models import ImportEntity, Organization, Role, Team, Territory, User
from app.services import importer

ORG_NAME = "Shakti Distributors Pvt Ltd (Demo)"
EMAIL_DOMAIN = "shakti-demo.in"
DEMO_PASSWORD = "Demo@12345"
SAMPLE_DIR = Path(__file__).resolve().parents[2] / "data" / "sample"

TERRITORIES = [("Delhi NCR", "North"), ("Punjab", "North"), ("Mumbai", "West"), ("Pune", "West")]

# (name, role, territory, team)
PEOPLE = [
    ("Ananya Rao", Role.ORG_ADMIN, None, None),
    ("Vikram Mehta", Role.SALES_HEAD, None, None),
    ("Neha Kapoor", Role.ANALYST, None, None),
    ("Sanjay Verma", Role.REGIONAL_MANAGER, "Delhi NCR", None),
    ("Priya Kulkarni", Role.REGIONAL_MANAGER, "Mumbai", None),
    ("Rahul Bhatia", Role.SALES_MANAGER, "Delhi NCR", "Delhi Sales"),
    ("Simran Sandhu", Role.SALES_MANAGER, "Punjab", "Punjab Sales"),
    ("Kunal Shah", Role.SALES_MANAGER, "Mumbai", "Mumbai Sales"),
    ("Meera Pawar", Role.SALES_MANAGER, "Pune", "Pune Sales"),
    ("Amit Sharma", Role.SALES_EXECUTIVE, "Delhi NCR", "Delhi Sales"),
    ("Pooja Singh", Role.SALES_EXECUTIVE, "Delhi NCR", "Delhi Sales"),
    ("Gurpreet Gill", Role.SALES_EXECUTIVE, "Punjab", "Punjab Sales"),
    ("Harleen Kaur", Role.SALES_EXECUTIVE, "Punjab", "Punjab Sales"),
    ("Rohan Desai", Role.SALES_EXECUTIVE, "Mumbai", "Mumbai Sales"),
    ("Sneha Patil", Role.SALES_EXECUTIVE, "Mumbai", "Mumbai Sales"),
    ("Aditya Joshi", Role.SALES_EXECUTIVE, "Pune", "Pune Sales"),
    ("Kavya Deshpande", Role.SALES_EXECUTIVE, "Pune", "Pune Sales"),
]

PRODUCTS = [
    ("Staples", "Chakki Atta 5kg", 265), ("Staples", "Chakki Atta 10kg", 510),
    ("Staples", "Basmati Rice 5kg", 620), ("Staples", "Sona Masoori Rice 5kg", 410),
    ("Staples", "Toor Dal 1kg", 165), ("Staples", "Moong Dal 1kg", 150),
    ("Staples", "Chana Dal 1kg", 110), ("Staples", "Sugar 5kg", 245),
    ("Edible Oil", "Mustard Oil 1L", 175), ("Edible Oil", "Sunflower Oil 1L", 155),
    ("Edible Oil", "Groundnut Oil 1L", 210), ("Edible Oil", "Soybean Oil 5L", 690),
    ("Spices", "Turmeric Powder 200g", 62), ("Spices", "Red Chilli Powder 200g", 78),
    ("Spices", "Garam Masala 100g", 85), ("Spices", "Coriander Powder 200g", 55),
    ("Beverages", "Assam Tea 500g", 260), ("Beverages", "Filter Coffee 200g", 340),
    ("Beverages", "Mango Drink 1.2L", 70), ("Snacks", "Bhujia Namkeen 400g", 95),
    ("Snacks", "Glucose Biscuits Family Pack", 30), ("Snacks", "Milk Rusk 300g", 60),
    ("Personal Care", "Neem Bath Soap 4-pack", 160), ("Personal Care", "Herbal Shampoo 340ml", 210),
    ("Personal Care", "Toothpaste 150g", 98), ("Household", "Detergent Powder 1kg", 120),
    ("Household", "Dishwash Bar 3-pack", 45), ("Household", "Floor Cleaner 1L", 170),
]

SURNAMES = ["Sharma", "Gupta", "Agarwal", "Bansal", "Jain", "Patel", "Shah", "Kulkarni", "Arora",
            "Malhotra", "Joshi", "Mehta", "Goyal", "Chopra", "Sethi", "Deshmukh", "Naik", "Mittal"]
STORE_TYPES = {
    "Kirana": ["General Store", "Kirana", "Provision Store", "Mart"],
    "Wholesaler": ["Traders", "Wholesale", "Enterprises", "Distributors"],
    "Modern Trade": ["Supermart", "Fresh Mart", "Hypermarket"],
    "HoReCa": ["Restaurant", "Caterers", "Dhaba", "Foods"],
}
# segment: (weight, orders per month, quantity range per line)
SEGMENTS = {
    "Kirana": (45, 4, (4, 24)),
    "Wholesaler": (20, 6, (30, 160)),
    "Modern Trade": (15, 8, (15, 80)),
    "HoReCa": (20, 5, (8, 45)),
}
# Each type of customer has a core range most of them stock, plus a few products of their own;
# the customers missing a core item are what the cross-sell suggestions find.
SEGMENT_CORE = {
    "Kirana": ["Chakki Atta 5kg", "Sugar 5kg", "Toor Dal 1kg", "Mustard Oil 1L", "Glucose Biscuits Family Pack", "Detergent Powder 1kg"],
    "Wholesaler": ["Chakki Atta 10kg", "Basmati Rice 5kg", "Sona Masoori Rice 5kg", "Sugar 5kg", "Soybean Oil 5L"],
    "Modern Trade": ["Basmati Rice 5kg", "Filter Coffee 200g", "Assam Tea 500g", "Herbal Shampoo 340ml", "Toothpaste 150g", "Bhujia Namkeen 400g"],
    "HoReCa": ["Basmati Rice 5kg", "Chakki Atta 10kg", "Soybean Oil 5L", "Garam Masala 100g", "Red Chilli Powder 200g", "Turmeric Powder 200g"],
}
CORE_STOCKED = 0.8  # chance a customer stocks each core product of their type

CITIES = {"Delhi NCR": ["Delhi", "Gurugram", "Noida"], "Punjab": ["Ludhiana", "Amritsar", "Jalandhar"],
          "Mumbai": ["Mumbai", "Thane", "Navi Mumbai"], "Pune": ["Pune", "Pimpri", "Hadapsar"]}
# Festive uplift in Oct/Nov, softer monsoon months.
SEASONALITY = {1: 0.95, 2: 0.95, 3: 1.1, 4: 1.0, 5: 1.0, 6: 0.95, 7: 0.85, 8: 0.9, 9: 1.0, 10: 1.25, 11: 1.2, 12: 1.05}


def email_for(name: str) -> str:
    return f"{name.split()[0].lower()}@{EMAIL_DOMAIN}"


def generate(rng: random.Random, today: date) -> dict[str, list[dict]]:
    start = month_start(add_months(today, -12))
    executives = [p for p in PEOPLE if p[1] == Role.SALES_EXECUTIVE]

    products = [
        {"sku": f"SKU-{i:03d}", "name": name, "category": cat, "unit_price": price}
        for i, (cat, name, price) in enumerate(PRODUCTS, start=1)
    ]

    customers, profiles = [], []
    used_names: set[str] = set()
    for territory, _ in TERRITORIES:
        reps = [p[0] for p in executives if p[2] == territory]
        for _ in range(20):
            segment = rng.choices(list(SEGMENTS), weights=[s[0] for s in SEGMENTS.values()])[0]
            while True:
                name = f"{rng.choice(SURNAMES)} {rng.choice(STORE_TYPES[segment])}"
                if name not in used_names:
                    used_names.add(name)
                    break
            code = f"C{len(customers) + 1:04d}"
            rep = rng.choice(reps)
            customers.append({
                "customer_code": code, "customer_name": name, "segment": segment,
                "city": rng.choice(CITIES[territory]), "territory": territory,
                "salesperson_email": email_for(rep),
                "mobile": f"+9198{rng.randint(10_000_000, 99_999_999)}",
            })
            # ~10% are recently acquired; customers who stop ordering are picked below.
            active_from = start + timedelta(days=rng.randint(200, 330)) if rng.random() > 0.9 else start
            active_to = today
            index = {p["name"]: i for i, p in enumerate(products)}
            core = [index[name] for name in SEGMENT_CORE[segment] if rng.random() < CORE_STOCKED]
            extras = rng.sample([i for i in range(len(products)) if i not in core], 8 - len(core))
            profiles.append({
                "code": code, "rep": rep, "segment": segment,
                "size": rng.uniform(0.6, 1.6), "active_from": active_from, "active_to": active_to,
                "favourites": core + extras,
            })

    # Exactly 15% of established customers stop ordering at some point in the last 30-200 days.
    established = [p for p in profiles if p["active_from"] == start]
    for p in rng.sample(established, round(len(profiles) * 0.15)):
        p["active_to"] = today - timedelta(days=rng.randint(30, 200))

    orders, seq = [], defaultdict(int)
    total_days = (today - start).days
    for day_offset in range(total_days + 1):
        day = start + timedelta(days=day_offset)
        if day.weekday() == 6:  # no Sunday orders
            continue
        growth = 0.9 + 0.2 * day_offset / total_days
        for prof in profiles:
            if not prof["active_from"] <= day <= prof["active_to"]:
                continue
            _, per_month, (qmin, qmax) = SEGMENTS[prof["segment"]]
            p = per_month / 26 * SEASONALITY[day.month] * growth
            # One rep has had a weak last quarter: a pattern later phases should detect.
            if prof["rep"] == "Harleen Kaur" and day >= add_months(today, -3):
                p *= 0.55
            if rng.random() >= p:
                continue

            seq[day.strftime("%Y%m")] += 1
            number = f"SO-{day:%Y%m}-{seq[day.strftime('%Y%m')]:05d}"
            roll = rng.random()
            if (today - day).days <= 2 and roll < 0.35:
                status = "pending"
            elif roll < 0.025:
                status = "cancelled"
            elif roll < 0.035:
                status = "returned"
            else:
                status = "completed"
            n_lines = rng.randint(1, 5)
            picks = rng.sample(prof["favourites"], min(n_lines, 8))
            for idx in picks:
                product = products[idx]
                qty = max(1, round(rng.randint(qmin, qmax) * prof["size"]))
                price = Decimal(product["unit_price"]) * Decimal(str(round(rng.uniform(0.95, 1.0), 3)))
                price = price.quantize(Decimal("0.01"))
                orders.append({
                    "order_no": number, "order_date": day.isoformat(), "customer_code": prof["code"],
                    "salesperson_email": email_for(prof["rep"]), "sku": product["sku"],
                    "qty": qty, "rate": str(price),
                    "gst_amount": str((price * qty * Decimal("0.05")).quantize(Decimal("0.01"))),
                    "status": status,
                })

    # Targets: each rep's average realised monthly revenue, stretched 8-20% and rounded.
    monthly: dict[str, dict[str, Decimal]] = defaultdict(lambda: defaultdict(Decimal))
    for line in orders:
        if line["status"] == "completed":
            monthly[line["salesperson_email"]][line["order_date"][:7]] += Decimal(line["rate"]) * line["qty"]
    targets = []
    for rep in executives:
        email = email_for(rep[0])
        history = list(monthly[email].values())
        base = sum(history) / max(len(history), 1)
        month = start
        while month <= month_start(add_months(today, 1)):
            stretch = Decimal(str(rng.uniform(1.08, 1.2))) * Decimal(str(SEASONALITY[month.month]))
            amount = (base * stretch / 10_000).quantize(Decimal("1")) * 10_000
            targets.append({"salesperson_email": email, "month": month.strftime("%Y-%m"), "target": str(amount)})
            month = add_months(month, 1)

    opportunities, activities = generate_pipeline(rng, today, start, customers, profiles, orders)
    return {
        "customers": customers,
        "products": [{"product_code": p["sku"], "product_name": p["name"], "category": p["category"], "price": p["unit_price"]} for p in products],
        "orders": orders,
        "targets": targets,
        "opportunities": opportunities,
        "activities": activities,
    }


DEAL_TYPES = ["Festive season stock-up", "New SKU listing", "Annual supply contract",
              "Shelf-space expansion", "Bulk edible oil order", "Private-label staples"]
LOST_REASONS = ["Price too high", "Chose competitor", "Budget frozen", "No response", "Credit terms"]
OPEN_STAGE_WEIGHTS = {"prospecting": 3, "qualification": 3, "proposal": 2, "negotiation": 2}


def generate_pipeline(rng, today, start, customers, profiles, orders):
    executives = [p[0] for p in PEOPLE if p[1] == Role.SALES_EXECUTIVE]
    weak = "Harleen Kaur"
    recent = add_months(today, -3)
    activities: dict[tuple, dict] = {}  # keyed like the importer's duplicate check

    def add(day, rep, kind, customer=None, status="done", opportunity=None, notes=None):
        if day.weekday() == 6:
            day -= timedelta(days=1)
        key = (day, rep, customer, kind, status)
        if key not in activities:
            activities[key] = {
                "date": day.isoformat(), "salesperson_email": email_for(rep), "type": kind,
                "customer_code": customer or "", "opportunity_id": opportunity or "",
                "status": status, "notes": notes or "",
            }

    # Prospects: customers with no orders yet, so the pipeline has new business too.
    by_territory = defaultdict(list)
    for c in customers:
        by_territory[c["territory"]].append(c)
    prospects = []
    for i in range(12):
        territory = TERRITORIES[i % 4][0]
        rep = rng.choice([p[0] for p in PEOPLE if p[1] == Role.SALES_EXECUTIVE and p[2] == territory])
        segment = rng.choice(["Modern Trade", "Wholesaler", "HoReCa"])
        name = f"{rng.choice(SURNAMES)} {rng.choice(STORE_TYPES[segment])} (New)"
        code = f"P{i + 1:03d}"
        prospects.append({
            "customer_code": code, "customer_name": name, "segment": segment,
            "city": rng.choice(CITIES[territory]), "territory": territory,
            "salesperson_email": email_for(rep), "mobile": f"+9197{rng.randint(10_000_000, 99_999_999)}",
        })
    customers.extend(prospects)
    owner = {c["customer_code"]: next(p[0] for p in PEOPLE if email_for(p[0]) == c["salesperson_email"]) for c in customers}

    # Visits lead most orders; the weak rep has visited far less in the last quarter.
    seen_orders = set()
    for line in orders:
        number = line["order_no"]
        if number in seen_orders:
            continue
        seen_orders.add(number)
        rep = owner[line["customer_code"]]
        day = date.fromisoformat(line["order_date"])
        chance = 0.35 if rep == weak and day >= recent else 0.65
        if rng.random() < chance:
            add(day - timedelta(days=rng.randint(0, 3)), rep, "visit", line["customer_code"], notes="Order discussed")

    # Everyday calls, WhatsApp messages and visits that didn't produce an order.
    customers_of = defaultdict(list)
    for c in customers:
        customers_of[owner[c["customer_code"]]].append(c["customer_code"])
    for offset in range((today - start).days + 1):
        day = start + timedelta(days=offset)
        if day.weekday() == 6:
            continue
        for rep in executives:
            slow = rep == weak and day >= recent
            for kind, count in (("call", rng.randint(0, 3)), ("whatsapp", rng.randint(0, 2)), ("visit", rng.randint(0, 1 if slow else 2))):
                for _ in range(count):
                    add(day, rep, kind, rng.choice(customers_of[rep]))

    # Deals: closed ones in the past, open ones at various stages; some left to go stale.
    opportunities = []
    deal_customers = [c for c in customers if c["segment"] in ("Wholesaler", "Modern Trade", "HoReCa")]
    for i in range(140):
        c = rng.choice(deal_customers)
        rep = owner[c["customer_code"]]
        code = f"OPP-{i + 1:04d}"
        opened = start + timedelta(days=rng.randint(0, (today - start).days - 5))
        expected = opened + timedelta(days=rng.randint(30, 90))
        amount = rng.choice([1.5, 2, 2.5, 3, 4, 5, 6, 8, 10, 12, 15, 20, 25]) * 100_000
        age = (today - opened).days
        closed_on, lost_reason = "", ""
        if age > 75 or (age > 25 and rng.random() < 0.4):
            won_chance = 0.25 if rep == weak else 0.45
            stage = "won" if rng.random() < won_chance else "lost"
            closed = min(opened + timedelta(days=rng.randint(15, 75)), today)
            closed_on = closed.isoformat()
            lost_reason = rng.choice(LOST_REASONS) if stage == "lost" else ""
            last_touch = closed
        else:
            stage = rng.choices(list(OPEN_STAGE_WEIGHTS), weights=list(OPEN_STAGE_WEIGHTS.values()))[0]
            # ~30% of open deals go quiet: their last touch is well in the past.
            stale = rng.random() < (0.6 if rep == weak else 0.3)
            last_touch = opened + timedelta(days=rng.randint(0, 5)) if stale else today - timedelta(days=rng.randint(0, 12))
        opportunities.append({
            "opportunity_id": code, "opportunity_name": f"{c['customer_name']} – {rng.choice(DEAL_TYPES)}",
            "customer_code": c["customer_code"], "owner_email": email_for(rep), "stage": stage,
            "deal_value": int(amount), "created_date": opened.isoformat(), "close_date": expected.isoformat(),
            "closed_date": closed_on, "lead_source": rng.choice(["Referral", "Field visit", "Inbound call", "Trade show"]),
            "lost_reason": lost_reason,
        })
        touch_days = sorted({opened, last_touch} | {opened + timedelta(days=rng.randint(0, max((last_touch - opened).days, 0))) for _ in range(rng.randint(0, 2))})
        for day in touch_days:
            add(day, rep, rng.choice(["meeting", "call"]), c["customer_code"], opportunity=code, notes="Deal follow-up")

    # Planned follow-ups: some upcoming, some already overdue (more for the weak rep).
    for rep in executives:
        for _ in range(rng.randint(3, 7)):
            add(today + timedelta(days=rng.randint(0, 14)), rep, rng.choice(["visit", "call"]), rng.choice(customers_of[rep]),
                status="planned", notes="Check stock and take next order")
        for _ in range(rng.randint(6, 10) if rep == weak else rng.randint(0, 3)):
            add(today - timedelta(days=rng.randint(1, 20)), rep, rng.choice(["visit", "call"]), rng.choice(customers_of[rep]),
                status="planned", notes="Promised a follow-up visit")

    rows = sorted(activities.values(), key=lambda a: (a["date"], a["salesperson_email"]))
    return opportunities, rows


def write_csv(path: Path, rows: list[dict]) -> bytes:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    return path.read_bytes()


def import_file(db: Session, org_id, entity: ImportEntity, path: Path, content: bytes) -> dict:
    rows = importer.read_table(content, path.name)
    result = importer.validate(db, org_id, entity, rows)
    if result.errors:
        raise RuntimeError(f"{path.name}: {result.errors[:5]}")
    counts = importer.apply(db, org_id, result)
    db.flush()
    return counts


def populate(db: Session, today: date, csv_dir: Path | None = None, log=lambda *_: None) -> tuple[Organization, dict]:
    """Create the demo organisation with data up to `today`, import it and train the models.

    Used by `python -m app.seed` and by tests (with a fixed `today` and no CSV output).
    """
    org = Organization(name=ORG_NAME, industry="FMCG Distribution", timezone="Asia/Kolkata", plan="demo")
    db.add(org)
    db.flush()

    territories = {name: Territory(organization_id=org.id, name=name, region=region) for name, region in TERRITORIES}
    teams = {name: Team(organization_id=org.id, name=name) for name in {p[3] for p in PEOPLE if p[3]}}
    db.add_all([*territories.values(), *teams.values()])
    db.flush()

    password_hash = hash_password(DEMO_PASSWORD)
    for i, (name, role, territory, team) in enumerate(PEOPLE):
        db.add(User(
            organization_id=org.id, email=email_for(name), full_name=name, role=role,
            hashed_password=password_hash, phone=f"+91900000{i:04d}",
            territory_id=territories[territory].id if territory else None,
            team_id=teams[team].id if team else None,
        ))
    db.flush()

    data = generate(random.Random(42), today)
    for entity in (ImportEntity.CUSTOMERS, ImportEntity.PRODUCTS, ImportEntity.ORDERS, ImportEntity.TARGETS,
                   ImportEntity.OPPORTUNITIES, ImportEntity.ACTIVITIES):
        name = f"{entity.value}.csv"
        content = write_csv(csv_dir / name, data[entity.value]) if csv_dir else _csv_bytes(data[entity.value])
        counts = import_file(db, org.id, entity, Path(name), content)
        log(f"  {entity.value:<10} {counts}")
    db.commit()
    trained = train_organization(db, org, today)
    db.commit()
    return org, trained


def _csv_bytes(rows: list[dict]) -> bytes:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue().encode()


def seed(reset: bool = False) -> None:
    db = SessionLocal()
    try:
        existing = db.scalar(select(Organization).where(Organization.name == ORG_NAME))
        if existing and not reset:
            print(f"'{ORG_NAME}' already exists. Use --reset to recreate it.")
            return
        if existing:
            db.execute(delete(Organization).where(Organization.id == existing.id))
            db.commit()
            print("Deleted existing demo organisation.")

        org, trained = populate(db, org_today("Asia/Kolkata"), csv_dir=SAMPLE_DIR, log=print)
        print(f"  models     forecast={trained['month_end_forecast']} churn={trained['churn']} "
              f"deal_win={trained['deal_win']} actions={trained['recommendations']}")
        print(f"\nSeeded '{ORG_NAME}'. Sample CSVs written to {SAMPLE_DIR}")
        print(f"Log in as any user with password {DEMO_PASSWORD}, e.g.:")
        for name, role, *_ in PEOPLE[:3] + PEOPLE[5:6] + PEOPLE[9:10]:
            print(f"  {email_for(name):<28} {role.value}")
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--reset", action="store_true", help="delete and recreate the demo organisation")
    seed(parser.parse_args().reset)
