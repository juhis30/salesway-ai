import uuid
from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.analytics import service as analytics
from app.analytics.metrics import REVENUE_STATUSES
from app.core.database import get_db
from app.core.deps import get_current_user
from app.models import Customer, CustomerStatus, OrderStatus, Product, SalesOrder, Territory, User
from app.services.access import salesperson_scope, visible_customer

router = APIRouter(tags=["customers & products"])


class CustomerOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str | None
    name: str
    segment: str | None
    city: str | None
    status: CustomerStatus
    territory_id: uuid.UUID | None
    owner_id: uuid.UUID | None


class ProductOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    sku: str
    name: str
    category: str | None
    unit_price: Decimal | None


class Page[T](BaseModel):
    total: int
    items: list[T]


def _escape_like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


@router.get("/customers", response_model=Page[CustomerOut])
def list_customers(
    search: str | None = Query(default=None, max_length=100),
    segment: str | None = None,
    territory_id: uuid.UUID | None = None,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    stmt = select(Customer).where(Customer.organization_id == user.organization_id)
    scope = salesperson_scope(db, user)
    if scope is not None:
        stmt = stmt.where(Customer.owner_id.in_(scope))
    if search:
        pattern = f"%{_escape_like(search.lower())}%"
        stmt = stmt.where(
            func.lower(Customer.name).like(pattern, escape="\\")
            | func.lower(Customer.code).like(pattern, escape="\\")
        )
    if segment:
        stmt = stmt.where(Customer.segment == segment)
    if territory_id:
        stmt = stmt.where(Customer.territory_id == territory_id)

    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    items = db.scalars(stmt.order_by(Customer.name).limit(limit).offset(offset)).all()
    return Page[CustomerOut](total=total, items=items)


@router.get("/customers/segments", response_model=list[str])
def customer_segments(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return db.scalars(
        select(Customer.segment)
        .where(Customer.organization_id == user.organization_id, Customer.segment.is_not(None))
        .distinct()
        .order_by(Customer.segment)
    ).all()


class CustomerDetail(CustomerOut):
    phone: str | None
    email: str | None
    territory_name: str | None
    owner_name: str | None
    # All-time figures under the standard revenue rules (see docs/metrics.md).
    first_order_date: date | None
    last_order_date: date | None
    lifetime_revenue: Decimal
    lifetime_orders: int


class OrderLineOut(BaseModel):
    product_id: uuid.UUID
    sku: str
    product_name: str
    quantity: Decimal
    unit_price: Decimal
    line_amount: Decimal


class OrderOut(BaseModel):
    id: uuid.UUID
    order_number: str
    order_date: date
    status: OrderStatus
    salesperson_name: str | None
    net_amount: Decimal
    tax_amount: Decimal
    total_amount: Decimal
    counts_as_revenue: bool
    lines: list[OrderLineOut]


@router.get("/customers/{customer_id}", response_model=CustomerDetail)
def get_customer(customer_id: uuid.UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    customer = visible_customer(db, user, customer_id)
    scope = salesperson_scope(db, user)

    stmt = select(func.min(SalesOrder.order_date), func.max(SalesOrder.order_date)).where(
        SalesOrder.organization_id == user.organization_id,
        SalesOrder.customer_id == customer.id,
        SalesOrder.status.in_(REVENUE_STATUSES),
    )
    if scope is not None:
        stmt = stmt.where(SalesOrder.salesperson_id.in_(scope))
    first, last = db.execute(stmt).one()

    revenue, orders = Decimal("0"), 0
    if first is not None:
        all_time = analytics.Filters(
            organization_id=user.organization_id,
            start=first,
            end=last,
            salesperson_ids=None if scope is None else frozenset(scope),
            customer_id=customer.id,
        )
        revenue, orders, _ = analytics.revenue_totals(db, all_time)

    territory = db.get(Territory, customer.territory_id) if customer.territory_id else None
    owner = db.get(User, customer.owner_id) if customer.owner_id else None
    return CustomerDetail(
        **CustomerOut.model_validate(customer).model_dump(),
        phone=customer.phone,
        email=customer.email,
        territory_name=territory.name if territory else None,
        owner_name=owner.full_name if owner else None,
        first_order_date=first,
        last_order_date=last,
        lifetime_revenue=revenue,
        lifetime_orders=orders,
    )


@router.get("/customers/{customer_id}/orders", response_model=Page[OrderOut])
def customer_orders(
    customer_id: uuid.UUID,
    start: date | None = None,
    end: date | None = None,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Every order for the customer, newest first, including ones that don't count as revenue."""
    customer = visible_customer(db, user, customer_id)
    stmt = select(SalesOrder).where(
        SalesOrder.organization_id == user.organization_id, SalesOrder.customer_id == customer.id
    )
    scope = salesperson_scope(db, user)
    if scope is not None:
        stmt = stmt.where(SalesOrder.salesperson_id.in_(scope))
    if start:
        stmt = stmt.where(SalesOrder.order_date >= start)
    if end:
        stmt = stmt.where(SalesOrder.order_date <= end)

    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    orders = db.scalars(
        stmt.options(selectinload(SalesOrder.items))
        .order_by(SalesOrder.order_date.desc(), SalesOrder.order_number.desc())
        .limit(limit)
        .offset(offset)
    ).all()

    product_ids = {item.product_id for o in orders for item in o.items}
    products = {p.id: p for p in db.scalars(select(Product).where(Product.id.in_(product_ids)))} if product_ids else {}
    people_ids = {o.salesperson_id for o in orders if o.salesperson_id}
    people = dict(db.execute(select(User.id, User.full_name).where(User.id.in_(people_ids))).tuples().all()) if people_ids else {}

    items = [
        OrderOut(
            id=o.id,
            order_number=o.order_number,
            order_date=o.order_date,
            status=o.status,
            salesperson_name=people.get(o.salesperson_id),
            net_amount=o.net_amount,
            tax_amount=o.tax_amount,
            total_amount=o.total_amount,
            counts_as_revenue=o.status in REVENUE_STATUSES,
            lines=[
                OrderLineOut(
                    product_id=i.product_id,
                    sku=products[i.product_id].sku,
                    product_name=products[i.product_id].name,
                    quantity=i.quantity,
                    unit_price=i.unit_price,
                    line_amount=i.line_amount,
                )
                for i in sorted(o.items, key=lambda i: i.line_amount, reverse=True)
            ],
        )
        for o in orders
    ]
    return Page[OrderOut](total=total, items=items)


@router.get("/products", response_model=Page[ProductOut])
def list_products(
    search: str | None = Query(default=None, max_length=100),
    category: str | None = None,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    stmt = select(Product).where(Product.organization_id == user.organization_id)
    if search:
        pattern = f"%{_escape_like(search.lower())}%"
        stmt = stmt.where(
            func.lower(Product.name).like(pattern, escape="\\")
            | func.lower(Product.sku).like(pattern, escape="\\")
        )
    if category:
        stmt = stmt.where(Product.category == category)

    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    items = db.scalars(stmt.order_by(Product.name).limit(limit).offset(offset)).all()
    return Page[ProductOut](total=total, items=items)
