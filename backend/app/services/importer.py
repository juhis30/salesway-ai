"""CSV/Excel import: parse -> map columns -> validate -> (preview) -> apply.

Validation is pure with respect to the database (it only reads), so the same function runs
at preview time and again at confirm time against the then-current data.
"""

import io
import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    ActivityStatus,
    ActivityType,
    Customer,
    ImportEntity,
    Opportunity,
    OpportunityStage,
    OrderStatus,
    Product,
    SalesActivity,
    SalesOrder,
    SalesOrderItem,
    SalesTarget,
    Territory,
    User,
)

MAX_ROWS = 100_000
MAX_REPORTED_ERRORS = 500


class ImportFileError(ValueError):
    """The file as a whole cannot be processed (wrong format, empty, missing columns...)."""


# --- Field specifications ----------------------------------------------------------------


@dataclass(frozen=True)
class Field:
    name: str
    kind: str  # str | email | decimal | date | month | status
    required: bool = False
    synonyms: tuple[str, ...] = ()


SPECS: dict[ImportEntity, list[Field]] = {
    ImportEntity.CUSTOMERS: [
        Field("code", "str", synonyms=("customer_code", "customer_id", "cust_code", "account_code", "party_code")),
        Field("name", "str", True, ("customer_name", "customer", "account_name", "party_name", "party")),
        Field("segment", "str", synonyms=("customer_segment", "channel", "customer_type", "type")),
        Field("city", "str", synonyms=("town", "location")),
        Field("territory", "str", synonyms=("territory_name", "area", "zone", "region")),
        Field("owner_email", "email", synonyms=("salesperson_email", "sales_rep_email", "rep_email", "assigned_to")),
        Field("phone", "str", synonyms=("mobile", "phone_number", "contact_number", "mobile_number")),
        Field("email", "email", synonyms=("customer_email", "email_id")),
    ],
    ImportEntity.PRODUCTS: [
        Field("sku", "str", True, ("product_code", "item_code", "code", "sku_code")),
        Field("name", "str", True, ("product_name", "item_name", "product", "item", "description")),
        Field("category", "str", synonyms=("product_category", "group", "product_group")),
        Field("unit_price", "decimal", synonyms=("price", "rate", "list_price", "selling_price")),
    ],
    ImportEntity.ORDERS: [
        Field("order_number", "str", True, ("order_no", "order_id", "invoice_number", "invoice_no", "bill_no")),
        Field("order_date", "date", True, ("date", "invoice_date", "bill_date")),
        Field("customer", "str", True, ("customer_code", "customer_name", "party", "party_name")),
        Field("salesperson_email", "email", True, ("salesperson", "sales_rep_email", "rep_email", "owner_email")),
        Field("product", "str", True, ("sku", "product_code", "item_code", "product_name", "item")),
        Field("quantity", "decimal", True, ("qty", "units")),
        Field("unit_price", "decimal", True, ("rate", "price")),
        Field("tax_amount", "decimal", synonyms=("tax", "gst", "gst_amount")),
        Field("status", "status", synonyms=("order_status",)),
    ],
    ImportEntity.TARGETS: [
        Field("salesperson_email", "email", True, ("email", "salesperson", "rep_email")),
        Field("period", "month", True, ("month", "target_month")),
        Field("target_amount", "decimal", True, ("target", "amount", "monthly_target")),
    ],
    ImportEntity.OPPORTUNITIES: [
        Field("code", "str", True, ("opportunity_id", "deal_id", "opportunity_code", "deal_code", "id")),
        Field("name", "str", True, ("opportunity_name", "deal_name", "title")),
        Field("customer", "str", True, ("customer_code", "customer_name", "account", "account_name", "company")),
        Field("owner_email", "email", True, ("salesperson_email", "owner", "deal_owner", "rep_email")),
        Field("stage", "stage", True, ("deal_stage", "status", "pipeline_stage")),
        Field("amount", "decimal", True, ("deal_value", "value", "deal_amount", "expected_revenue")),
        Field("opened_on", "date", True, ("created_date", "create_date", "created_on", "open_date")),
        Field("expected_close", "date", synonyms=("close_date", "expected_close_date", "closing_date")),
        Field("closed_on", "date", synonyms=("closed_date", "actual_close_date", "won_lost_date")),
        Field("source", "str", synonyms=("lead_source", "channel")),
        Field("lost_reason", "str", synonyms=("loss_reason", "reason_lost")),
    ],
    ImportEntity.ACTIVITIES: [
        Field("activity_date", "date", True, ("date", "due_date", "visit_date", "call_date")),
        Field("salesperson_email", "email", True, ("salesperson", "owner_email", "rep_email", "user_email")),
        Field("activity_type", "activity_type", True, ("type", "activity")),
        Field("customer", "str", synonyms=("customer_code", "customer_name", "account")),
        Field("opportunity", "str", synonyms=("opportunity_id", "deal_id", "opportunity_code")),
        Field("status", "activity_status", synonyms=("activity_status", "state")),
        Field("notes", "str", synonyms=("note", "remarks", "comments", "outcome", "description")),
    ],
}


# --- File reading ------------------------------------------------------------------------


def read_table(content: bytes, filename: str) -> list[dict[str, str]]:
    name = filename.lower()
    try:
        if name.endswith(".csv"):
            try:
                df = pd.read_csv(io.BytesIO(content), dtype=str, keep_default_na=False, encoding="utf-8-sig")
            except UnicodeDecodeError:
                df = pd.read_csv(io.BytesIO(content), dtype=str, keep_default_na=False, encoding="latin-1")
        elif name.endswith(".xlsx"):
            df = pd.read_excel(io.BytesIO(content), dtype=str, keep_default_na=False, engine="openpyxl")
        else:
            raise ImportFileError("Only .csv and .xlsx files are supported")
    except ImportFileError:
        raise
    except Exception as exc:  # pandas raises many different parser errors
        raise ImportFileError(f"Could not read the file: {exc}") from exc

    if df.empty:
        raise ImportFileError("The file has no data rows")
    if len(df) > MAX_ROWS:
        raise ImportFileError(f"The file has more than {MAX_ROWS:,} rows; split it into smaller files")

    df.columns = [str(c).strip() for c in df.columns]
    return [
        {col: _clean_cell(value) for col, value in record.items()}
        for record in df.to_dict(orient="records")
    ]


def _clean_cell(value) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return "" if text.lower() in {"nan", "nat", "none"} else text


def _normalise_header(header: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", header.strip().lower()).strip("_")


def auto_map(columns: list[str], spec: list[Field]) -> dict[str, str]:
    """Map each spec field to the first matching file column (by name, then synonyms)."""
    normalised = {_normalise_header(c): c for c in columns}
    mapping: dict[str, str] = {}
    used: set[str] = set()
    for f in spec:
        for candidate in (f.name, *f.synonyms):
            column = normalised.get(candidate)
            if column is not None and column not in used:
                mapping[f.name] = column
                used.add(column)
                break
    return mapping


# --- Value coercion ----------------------------------------------------------------------

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
# Day-first formats only: dates like 03/04/2026 are read as 3 April (Indian convention).
DATE_FORMATS = ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d.%m.%Y", "%Y/%m/%d", "%d-%b-%Y", "%d %b %Y", "%d-%b-%y")
MONTH_FORMATS = ("%Y-%m", "%m/%Y", "%m-%Y", "%b-%Y", "%b %Y", "%B %Y", "%b-%y")
STATUS_ALIASES = {
    "pending": OrderStatus.PENDING, "open": OrderStatus.PENDING, "new": OrderStatus.PENDING,
    "confirmed": OrderStatus.CONFIRMED, "approved": OrderStatus.CONFIRMED,
    "completed": OrderStatus.COMPLETED, "complete": OrderStatus.COMPLETED,
    "delivered": OrderStatus.COMPLETED, "invoiced": OrderStatus.COMPLETED,
    "closed": OrderStatus.COMPLETED, "paid": OrderStatus.COMPLETED,
    "cancelled": OrderStatus.CANCELLED, "canceled": OrderStatus.CANCELLED,
    "returned": OrderStatus.RETURNED, "return": OrderStatus.RETURNED,
}


def _to_str(text: str) -> str:
    if len(text) > 200:
        raise ValueError("is longer than 200 characters")
    return text


def _to_email(text: str) -> str:
    text = text.lower()
    if not EMAIL_RE.match(text):
        raise ValueError(f"'{text}' is not a valid email")
    return text


def _to_decimal(text: str) -> Decimal:
    cleaned = re.sub(r"[,\s₹]|^rs\.?|^inr", "", text.lower())
    try:
        value = Decimal(cleaned)
    except InvalidOperation:
        raise ValueError(f"'{text}' is not a number") from None
    if not value.is_finite() or value < 0:
        raise ValueError(f"'{text}' must be a non-negative number")
    return value


def _parse_with(text: str, formats: tuple[str, ...]) -> date | None:
    for fmt in formats:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _to_date(text: str) -> date:
    # Excel cells often arrive as "2026-01-05 00:00:00".
    parsed = _parse_with(text.split(" ")[0].split("T")[0], DATE_FORMATS) or _parse_with(text, DATE_FORMATS)
    if parsed is None:
        raise ValueError(f"'{text}' is not a recognised date (use YYYY-MM-DD or DD/MM/YYYY)")
    return parsed


def _to_month(text: str) -> date:
    parsed = _parse_with(text, MONTH_FORMATS)
    if parsed is None:
        try:
            parsed = _to_date(text)
        except ValueError:
            raise ValueError(f"'{text}' is not a recognised month (use YYYY-MM)") from None
    return parsed.replace(day=1)


STAGE_ALIASES = {
    "prospecting": OpportunityStage.PROSPECTING, "prospect": OpportunityStage.PROSPECTING,
    "new": OpportunityStage.PROSPECTING, "lead": OpportunityStage.PROSPECTING,
    "qualification": OpportunityStage.QUALIFICATION, "qualified": OpportunityStage.QUALIFICATION,
    "needs analysis": OpportunityStage.QUALIFICATION,
    "proposal": OpportunityStage.PROPOSAL, "quote": OpportunityStage.PROPOSAL,
    "quotation": OpportunityStage.PROPOSAL, "proposal/price quote": OpportunityStage.PROPOSAL,
    "negotiation": OpportunityStage.NEGOTIATION, "negotiation/review": OpportunityStage.NEGOTIATION,
    "won": OpportunityStage.WON, "closed won": OpportunityStage.WON, "closedwon": OpportunityStage.WON,
    "lost": OpportunityStage.LOST, "closed lost": OpportunityStage.LOST, "closedlost": OpportunityStage.LOST,
}
ACTIVITY_TYPE_ALIASES = {
    "visit": ActivityType.VISIT, "field visit": ActivityType.VISIT, "store visit": ActivityType.VISIT,
    "call": ActivityType.CALL, "phone": ActivityType.CALL, "phone call": ActivityType.CALL,
    "meeting": ActivityType.MEETING, "demo": ActivityType.MEETING,
    "whatsapp": ActivityType.WHATSAPP, "message": ActivityType.WHATSAPP,
}
ACTIVITY_STATUS_ALIASES = {
    "done": ActivityStatus.DONE, "completed": ActivityStatus.DONE, "complete": ActivityStatus.DONE,
    "planned": ActivityStatus.PLANNED, "scheduled": ActivityStatus.PLANNED,
    "pending": ActivityStatus.PLANNED, "open": ActivityStatus.PLANNED, "follow up": ActivityStatus.PLANNED,
}


def _alias(table: dict, what: str) -> Callable[[str], object]:
    def coerce(text: str):
        value = table.get(re.sub(r"[\s_-]+", " ", text.strip().lower()))
        if value is None:
            raise ValueError(f"'{text}' is not a known {what}")
        return value

    return coerce


COERCERS: dict[str, Callable[[str], object]] = {
    "str": _to_str,
    "email": _to_email,
    "decimal": _to_decimal,
    "date": _to_date,
    "month": _to_month,
    "status": _alias(STATUS_ALIASES, "order status"),
    "stage": _alias(STAGE_ALIASES, "deal stage"),
    "activity_type": _alias(ACTIVITY_TYPE_ALIASES, "activity type"),
    "activity_status": _alias(ACTIVITY_STATUS_ALIASES, "activity status"),
}


# --- Validation --------------------------------------------------------------------------


@dataclass
class ValidationResult:
    entity: ImportEntity
    mapping: dict[str, str]
    unmapped_columns: list[str]
    total_rows: int = 0
    rows: list[tuple[int, dict]] = field(default_factory=list)  # (spreadsheet row number, clean values)
    errors: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def error_row_count(self) -> int:
        return len({e["row"] for e in self.errors})

    def add_error(self, row: int, field_name: str | None, message: str) -> None:
        self.errors.append({"row": row, "field": field_name, "message": message})


def validate(
    db: Session,
    organization_id: uuid.UUID,
    entity: ImportEntity,
    raw_rows: list[dict[str, str]],
    mapping_override: dict[str, str] | None = None,
) -> ValidationResult:
    spec = SPECS[entity]
    columns = list(raw_rows[0].keys()) if raw_rows else []

    mapping = auto_map(columns, spec)
    if mapping_override:
        field_names = {f.name for f in spec}
        for field_name, column in mapping_override.items():
            if field_name not in field_names:
                raise ImportFileError(f"Unknown field '{field_name}' in column mapping")
            if column not in columns:
                raise ImportFileError(f"Column '{column}' is not in the file")
        mapping.update(mapping_override)

    result = ValidationResult(
        entity=entity,
        mapping=mapping,
        unmapped_columns=[c for c in columns if c not in mapping.values()],
    )
    missing = [f.name for f in spec if f.required and f.name not in mapping]
    if missing:
        raise ImportFileError(f"Required columns not found: {', '.join(missing)}")

    checker = _CHECKERS[entity](db, organization_id, result)
    for index, raw in enumerate(raw_rows):
        row_number = index + 2  # header is row 1
        if not any(raw.values()):
            continue
        result.total_rows += 1

        values: dict = {}
        row_errors: list[tuple[str | None, str]] = []
        for f in spec:
            column = mapping.get(f.name)
            text = raw.get(column, "") if column else ""
            if text == "":
                values[f.name] = None
                if f.required:
                    row_errors.append((f.name, "is required"))
                continue
            try:
                values[f.name] = COERCERS[f.kind](text)
            except ValueError as exc:
                row_errors.append((f.name, str(exc)))

        if not row_errors:
            row_errors = checker.check(row_number, values)
        if row_errors:
            for field_name, message in row_errors:
                result.add_error(row_number, field_name, message)
        else:
            result.rows.append((row_number, values))

    checker.finish()
    return result


class _Checker:
    """Entity-specific checks: reference lookups and duplicate detection."""

    def __init__(self, db: Session, organization_id: uuid.UUID, result: ValidationResult):
        self.db = db
        self.org_id = organization_id
        self.result = result

    def check(self, row: int, values: dict) -> list[tuple[str | None, str]]:
        return []

    def finish(self) -> None:
        pass

    def _users_by_email(self) -> dict[str, uuid.UUID]:
        return {
            email.lower(): user_id
            for user_id, email in self.db.execute(
                select(User.id, User.email).where(
                    User.organization_id == self.org_id, User.deleted_at.is_(None)
                )
            )
        }


class _CustomerChecker(_Checker):
    def __init__(self, *args):
        super().__init__(*args)
        rows = self.db.execute(
            select(Customer.id, Customer.code, Customer.name).where(Customer.organization_id == self.org_id)
        ).all()
        self.by_code = {code.lower(): cid for cid, code, _ in rows if code}
        self.by_name = {name.lower(): cid for cid, _, name in rows}
        self.territories = {
            name.lower(): tid
            for tid, name in self.db.execute(
                select(Territory.id, Territory.name).where(Territory.organization_id == self.org_id)
            )
        }
        self.users = self._users_by_email()
        self.seen: set[str] = set()
        self.new_territories: set[str] = set()
        self.updates = 0

    def check(self, row, values):
        key = f"code:{values['code'].lower()}" if values["code"] else f"name:{values['name'].lower()}"
        if key in self.seen:
            return [("code" if values["code"] else "name", "duplicate of an earlier row in this file")]
        self.seen.add(key)

        errors = []
        values["owner_id"] = None
        if values["owner_email"]:
            values["owner_id"] = self.users.get(values["owner_email"])
            if values["owner_id"] is None:
                errors.append(("owner_email", f"no user with email {values['owner_email']} in this organisation"))

        values["territory_id"] = None
        if values["territory"]:
            values["territory_id"] = self.territories.get(values["territory"].lower())
            if values["territory_id"] is None:
                self.new_territories.add(values["territory"])

        existing = self.by_code.get(values["code"].lower()) if values["code"] else self.by_name.get(values["name"].lower())
        values["existing_id"] = existing
        if existing and not errors:
            self.updates += 1
        return errors

    def finish(self):
        if self.new_territories:
            self.result.warnings.append(
                f"{len(self.new_territories)} new territories will be created: "
                + ", ".join(sorted(self.new_territories))
            )
        if self.updates:
            self.result.warnings.append(f"{self.updates} existing customers will be updated")


class _ProductChecker(_Checker):
    def __init__(self, *args):
        super().__init__(*args)
        self.existing = {
            sku.lower(): pid
            for pid, sku in self.db.execute(
                select(Product.id, Product.sku).where(Product.organization_id == self.org_id)
            )
        }
        self.seen: set[str] = set()

    def check(self, row, values):
        key = values["sku"].lower()
        if key in self.seen:
            return [("sku", "duplicate of an earlier row in this file")]
        self.seen.add(key)
        values["existing_id"] = self.existing.get(key)
        return []

    def finish(self):
        updates = sum(1 for _, v in self.result.rows if v["existing_id"])
        if updates:
            self.result.warnings.append(f"{updates} existing products will be updated")


class _OrderChecker(_Checker):
    """Orders are one row per line item; lines sharing an order_number form one order."""

    def __init__(self, *args):
        super().__init__(*args)
        customers = self.db.execute(
            select(Customer.id, Customer.code, Customer.name, Customer.territory_id).where(
                Customer.organization_id == self.org_id
            )
        ).all()
        self.customers_by_code = {c.code.lower(): (c.id, c.territory_id) for c in customers if c.code}
        self.customers_by_name = {c.name.lower(): (c.id, c.territory_id) for c in customers}
        products = self.db.execute(
            select(Product.id, Product.sku, Product.name).where(Product.organization_id == self.org_id)
        ).all()
        self.products_by_sku = {p.sku.lower(): p.id for p in products}
        self.products_by_name = {p.name.lower(): p.id for p in products}
        self.users = self._users_by_email()
        self.existing_orders = set(
            self.db.scalars(select(SalesOrder.order_number).where(SalesOrder.organization_id == self.org_id))
        )
        self.headers: dict[str, tuple] = {}
        self.bad_orders: set[str] = set()
        self.duplicates: set[str] = set()

    def check(self, row, values):
        errors = []
        order_number = values["order_number"]

        key = values["customer"].lower()
        customer = self.customers_by_code.get(key) or self.customers_by_name.get(key)
        if customer is None:
            errors.append(("customer", f"unknown customer '{values['customer']}' (import customers first)"))
        else:
            values["customer_id"], values["territory_id"] = customer

        key = values["product"].lower()
        values["product_id"] = self.products_by_sku.get(key) or self.products_by_name.get(key)
        if values["product_id"] is None:
            errors.append(("product", f"unknown product '{values['product']}' (import products first)"))

        values["salesperson_id"] = self.users.get(values["salesperson_email"])
        if values["salesperson_id"] is None:
            errors.append(("salesperson_email", f"no user with email {values['salesperson_email']} in this organisation"))

        if values["quantity"] == 0:
            errors.append(("quantity", "must be greater than zero"))
        values["status"] = values["status"] or OrderStatus.COMPLETED
        values["tax_amount"] = values["tax_amount"] or Decimal("0")

        if not errors:
            header = (values["order_date"], values["customer_id"], values["salesperson_id"], values["status"])
            first = self.headers.setdefault(order_number, header)
            if first != header:
                errors.append(("order_number", "date, customer, salesperson or status differs from other lines of this order"))

        if errors:
            self.bad_orders.add(order_number)
        elif order_number in self.existing_orders:
            self.duplicates.add(order_number)
        return errors

    def finish(self):
        # An order is imported all-or-nothing: drop valid lines of orders that have a bad line.
        kept = []
        for row, values in self.result.rows:
            if values["order_number"] in self.bad_orders:
                self.result.add_error(row, "order_number", "skipped because another line of this order has errors")
            else:
                kept.append((row, values))
        self.result.rows = kept
        if self.duplicates:
            self.result.warnings.append(
                f"{len(self.duplicates)} orders already exist and will be skipped"
            )


class _TargetChecker(_Checker):
    def __init__(self, *args):
        super().__init__(*args)
        self.users = self._users_by_email()
        self.seen: set[tuple] = set()

    def check(self, row, values):
        values["user_id"] = self.users.get(values["salesperson_email"])
        if values["user_id"] is None:
            return [("salesperson_email", f"no user with email {values['salesperson_email']} in this organisation")]
        key = (values["user_id"], values["period"])
        if key in self.seen:
            return [("period", "duplicate target for this salesperson and month")]
        self.seen.add(key)
        return []


class _CustomerLookupMixin:
    def _load_customers(self) -> None:
        rows = self.db.execute(
            select(Customer.id, Customer.code, Customer.name).where(Customer.organization_id == self.org_id)
        ).all()
        self.customers_by_code = {c.code.lower(): c.id for c in rows if c.code}
        self.customers_by_name = {c.name.lower(): c.id for c in rows}

    def _customer(self, text: str) -> uuid.UUID | None:
        key = text.lower()
        return self.customers_by_code.get(key) or self.customers_by_name.get(key)


class _OpportunityChecker(_CustomerLookupMixin, _Checker):
    def __init__(self, *args):
        super().__init__(*args)
        self._load_customers()
        self.users = self._users_by_email()
        self.existing = {
            code.lower(): oid
            for oid, code in self.db.execute(
                select(Opportunity.id, Opportunity.code).where(Opportunity.organization_id == self.org_id)
            )
        }
        self.seen: set[str] = set()

    def check(self, row, values):
        key = values["code"].lower()
        if key in self.seen:
            return [("code", "duplicate of an earlier row in this file")]
        self.seen.add(key)

        errors = []
        values["customer_id"] = self._customer(values["customer"])
        if values["customer_id"] is None:
            errors.append(("customer", f"unknown customer '{values['customer']}' (import customers first)"))
        values["owner_id"] = self.users.get(values["owner_email"])
        if values["owner_id"] is None:
            errors.append(("owner_email", f"no user with email {values['owner_email']} in this organisation"))

        closed = values["stage"] in (OpportunityStage.WON, OpportunityStage.LOST)
        if closed and values["closed_on"] is None:
            errors.append(("closed_on", "is required for won or lost deals"))
        if not closed:
            values["closed_on"] = None  # an open deal has no close date yet
        if values["closed_on"] and values["closed_on"] < values["opened_on"]:
            errors.append(("closed_on", "is before opened_on"))
        values["existing_id"] = self.existing.get(key)
        return errors

    def finish(self):
        updates = sum(1 for _, v in self.result.rows if v["existing_id"])
        if updates:
            self.result.warnings.append(f"{updates} existing opportunities will be updated")


class _ActivityChecker(_CustomerLookupMixin, _Checker):
    def __init__(self, *args):
        super().__init__(*args)
        self._load_customers()
        self.users = self._users_by_email()
        self.opportunities = {
            code.lower(): (oid, cid)
            for oid, code, cid in self.db.execute(
                select(Opportunity.id, Opportunity.code, Opportunity.customer_id).where(
                    Opportunity.organization_id == self.org_id
                )
            )
        }
        # Activities have no natural ID, so an identical row is treated as already imported.
        self.existing = set(
            self.db.execute(
                select(
                    SalesActivity.activity_date, SalesActivity.user_id, SalesActivity.customer_id,
                    SalesActivity.activity_type, SalesActivity.status,
                ).where(SalesActivity.organization_id == self.org_id)
            ).tuples()
        )
        self.seen: set[tuple] = set()
        self.duplicates = 0

    def check(self, row, values):
        errors = []
        values["user_id"] = self.users.get(values["salesperson_email"])
        if values["user_id"] is None:
            errors.append(("salesperson_email", f"no user with email {values['salesperson_email']} in this organisation"))

        values["customer_id"] = values["opportunity_id"] = None
        if values["customer"]:
            values["customer_id"] = self._customer(values["customer"])
            if values["customer_id"] is None:
                errors.append(("customer", f"unknown customer '{values['customer']}'"))
        if values["opportunity"]:
            found = self.opportunities.get(values["opportunity"].lower())
            if found is None:
                errors.append(("opportunity", f"unknown opportunity '{values['opportunity']}'"))
            else:
                values["opportunity_id"] = found[0]
                values["customer_id"] = values["customer_id"] or found[1]
        values["status"] = values["status"] or ActivityStatus.DONE
        if errors:
            return errors

        key = (values["activity_date"], values["user_id"], values["customer_id"], values["activity_type"], values["status"])
        if key in self.seen:
            return [(None, "duplicate of an earlier row in this file")]
        self.seen.add(key)
        values["duplicate"] = key in self.existing
        self.duplicates += values["duplicate"]
        return []

    def finish(self):
        if self.duplicates:
            self.result.warnings.append(f"{self.duplicates} activities already exist and will be skipped")


_CHECKERS = {
    ImportEntity.CUSTOMERS: _CustomerChecker,
    ImportEntity.PRODUCTS: _ProductChecker,
    ImportEntity.ORDERS: _OrderChecker,
    ImportEntity.TARGETS: _TargetChecker,
    ImportEntity.OPPORTUNITIES: _OpportunityChecker,
    ImportEntity.ACTIVITIES: _ActivityChecker,
}


# --- Applying ----------------------------------------------------------------------------


def apply(db: Session, organization_id: uuid.UUID, result: ValidationResult) -> dict[str, int]:
    """Write validated rows. The caller commits."""
    return _APPLIERS[result.entity](db, organization_id, [v for _, v in result.rows])


def _apply_customers(db: Session, org_id: uuid.UUID, rows: list[dict]) -> dict[str, int]:
    new_territories: dict[str, Territory] = {}
    for values in rows:
        if values["territory"] and values["territory_id"] is None:
            key = values["territory"].lower()
            if key not in new_territories:
                new_territories[key] = Territory(organization_id=org_id, name=values["territory"])
                db.add(new_territories[key])
    db.flush()

    created = updated = 0
    for values in rows:
        territory_id = values["territory_id"]
        if values["territory"] and territory_id is None:
            territory_id = new_territories[values["territory"].lower()].id
        fields = {
            "code": values["code"],
            "name": values["name"],
            "segment": values["segment"],
            "city": values["city"],
            "phone": values["phone"],
            "email": values["email"],
            "territory_id": territory_id,
            "owner_id": values["owner_id"],
        }
        if values["existing_id"]:
            customer = db.get(Customer, values["existing_id"])
            # Blank cells don't erase existing data.
            for name, value in fields.items():
                if value is not None:
                    setattr(customer, name, value)
            updated += 1
        else:
            db.add(Customer(organization_id=org_id, **fields))
            created += 1
    return {"created": created, "updated": updated, "territories_created": len(new_territories)}


def _apply_products(db: Session, org_id: uuid.UUID, rows: list[dict]) -> dict[str, int]:
    created = updated = 0
    for values in rows:
        fields = {k: values[k] for k in ("sku", "name", "category", "unit_price")}
        if values["existing_id"]:
            product = db.get(Product, values["existing_id"])
            for name, value in fields.items():
                if value is not None:
                    setattr(product, name, value)
            updated += 1
        else:
            db.add(Product(organization_id=org_id, **fields))
            created += 1
    return {"created": created, "updated": updated}


def _apply_orders(db: Session, org_id: uuid.UUID, rows: list[dict]) -> dict[str, int]:
    existing = set(db.scalars(select(SalesOrder.order_number).where(SalesOrder.organization_id == org_id)))
    orders: dict[str, SalesOrder] = {}
    skipped: set[str] = set()
    cent = Decimal("0.01")

    for values in rows:
        number = values["order_number"]
        if number in existing:
            skipped.add(number)
            continue
        order = orders.get(number)
        if order is None:
            order = SalesOrder(
                organization_id=org_id,
                order_number=number,
                order_date=values["order_date"],
                status=values["status"],
                customer_id=values["customer_id"],
                salesperson_id=values["salesperson_id"],
                territory_id=values["territory_id"],
                net_amount=Decimal("0"),
                tax_amount=Decimal("0"),
                total_amount=Decimal("0"),
                source="import",
            )
            orders[number] = order
        # Round to the column precision up front so stored and computed values agree exactly.
        quantity = values["quantity"].quantize(Decimal("0.001"))
        unit_price = values["unit_price"].quantize(cent)
        line_amount = (quantity * unit_price).quantize(cent)
        tax = values["tax_amount"].quantize(cent)
        order.items.append(
            SalesOrderItem(
                organization_id=org_id,
                product_id=values["product_id"],
                quantity=quantity,
                unit_price=unit_price,
                line_amount=line_amount,
                tax_amount=tax,
            )
        )
        order.net_amount += line_amount
        order.tax_amount += tax
        order.total_amount = order.net_amount + order.tax_amount

    db.add_all(orders.values())
    return {
        "orders_created": len(orders),
        "lines_created": sum(len(o.items) for o in orders.values()),
        "orders_skipped_existing": len(skipped),
    }


def _apply_targets(db: Session, org_id: uuid.UUID, rows: list[dict]) -> dict[str, int]:
    existing = {
        (t.user_id, t.period): t
        for t in db.scalars(select(SalesTarget).where(SalesTarget.organization_id == org_id))
    }
    created = updated = 0
    for values in rows:
        target = existing.get((values["user_id"], values["period"]))
        if target:
            target.target_amount = values["target_amount"]
            updated += 1
        else:
            db.add(
                SalesTarget(
                    organization_id=org_id,
                    user_id=values["user_id"],
                    period=values["period"],
                    target_amount=values["target_amount"],
                )
            )
            created += 1
    return {"created": created, "updated": updated}


def _apply_opportunities(db: Session, org_id: uuid.UUID, rows: list[dict]) -> dict[str, int]:
    created = updated = 0
    names = ("code", "name", "customer_id", "owner_id", "stage", "amount", "opened_on",
             "expected_close", "closed_on", "source", "lost_reason")
    for values in rows:
        fields = {k: values[k] for k in names}
        fields["amount"] = fields["amount"].quantize(Decimal("0.01"))
        if values["existing_id"]:
            opportunity = db.get(Opportunity, values["existing_id"])
            for name, value in fields.items():
                # Stage changes always apply (closed_on is cleared when a deal reopens).
                if value is not None or name == "closed_on":
                    setattr(opportunity, name, value)
            updated += 1
        else:
            db.add(Opportunity(organization_id=org_id, **fields))
            created += 1
    return {"created": created, "updated": updated}


def _apply_activities(db: Session, org_id: uuid.UUID, rows: list[dict]) -> dict[str, int]:
    created = skipped = 0
    for values in rows:
        if values["duplicate"]:
            skipped += 1
            continue
        db.add(
            SalesActivity(
                organization_id=org_id,
                user_id=values["user_id"],
                customer_id=values["customer_id"],
                opportunity_id=values["opportunity_id"],
                activity_type=values["activity_type"],
                status=values["status"],
                activity_date=values["activity_date"],
                notes=values["notes"],
            )
        )
        created += 1
    return {"created": created, "skipped_existing": skipped}


_APPLIERS = {
    ImportEntity.CUSTOMERS: _apply_customers,
    ImportEntity.PRODUCTS: _apply_products,
    ImportEntity.ORDERS: _apply_orders,
    ImportEntity.TARGETS: _apply_targets,
    ImportEntity.OPPORTUNITIES: _apply_opportunities,
    ImportEntity.ACTIVITIES: _apply_activities,
}


def preview_rows(result: ValidationResult, limit: int = 10) -> list[dict]:
    """A JSON-safe sample of clean rows for the preview screen (only the spec fields)."""
    names = [f.name for f in SPECS[result.entity]]
    return [
        {"row": row, **{n: (None if values.get(n) is None else str(getattr(values[n], "value", values[n]))) for n in names}}
        for row, values in result.rows[:limit]
    ]
