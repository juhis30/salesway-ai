import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import Date, ForeignKey, Index, Numeric, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.base import IdMixin, TenantMixin, TimestampMixin, str_enum
from app.models.enums import CustomerStatus, OrderStatus

Money = Numeric(14, 2)


class Customer(IdMixin, TimestampMixin, TenantMixin, Base):
    __tablename__ = "customers"
    __table_args__ = (UniqueConstraint("organization_id", "code"),)

    code: Mapped[str | None] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(200), index=True)
    segment: Mapped[str | None] = mapped_column(String(64))
    city: Mapped[str | None] = mapped_column(String(120))
    phone: Mapped[str | None] = mapped_column(String(20))
    email: Mapped[str | None] = mapped_column(String(254))
    status: Mapped[CustomerStatus] = mapped_column(
        str_enum(CustomerStatus), default=CustomerStatus.ACTIVE
    )
    territory_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("territories.id", ondelete="SET NULL"), index=True
    )
    owner_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL"), index=True
    )


class Product(IdMixin, TimestampMixin, TenantMixin, Base):
    __tablename__ = "products"
    __table_args__ = (UniqueConstraint("organization_id", "sku"),)

    sku: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(200), index=True)
    category: Mapped[str | None] = mapped_column(String(100))
    unit_price: Mapped[Decimal | None] = mapped_column(Money)


class SalesOrder(IdMixin, TimestampMixin, TenantMixin, Base):
    __tablename__ = "sales_orders"
    __table_args__ = (
        UniqueConstraint("organization_id", "order_number"),
        Index("ix_sales_orders_org_date", "organization_id", "order_date"),
    )

    order_number: Mapped[str] = mapped_column(String(64))
    order_date: Mapped[date] = mapped_column(Date)
    status: Mapped[OrderStatus] = mapped_column(str_enum(OrderStatus), default=OrderStatus.COMPLETED)
    customer_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("customers.id", ondelete="RESTRICT"), index=True
    )
    salesperson_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    # Copied from the customer when the order is recorded, so history survives reassignment.
    territory_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("territories.id", ondelete="SET NULL"), index=True
    )
    net_amount: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"))
    tax_amount: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"))
    total_amount: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"))
    source: Mapped[str] = mapped_column(String(32), default="manual")

    items: Mapped[list["SalesOrderItem"]] = relationship(
        back_populates="order", cascade="all, delete-orphan"
    )


class SalesOrderItem(IdMixin, TenantMixin, Base):
    __tablename__ = "sales_order_items"

    order_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("sales_orders.id", ondelete="CASCADE"), index=True
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("products.id", ondelete="RESTRICT"), index=True
    )
    quantity: Mapped[Decimal] = mapped_column(Numeric(12, 3))
    unit_price: Mapped[Decimal] = mapped_column(Money)
    # quantity * unit_price, excluding tax. This is the basis of all revenue metrics.
    line_amount: Mapped[Decimal] = mapped_column(Money)
    tax_amount: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"))

    order: Mapped[SalesOrder] = relationship(back_populates="items")


class SalesTarget(IdMixin, TimestampMixin, TenantMixin, Base):
    """Monthly revenue target for one salesperson; period is the first day of the month."""

    __tablename__ = "sales_targets"
    __table_args__ = (UniqueConstraint("organization_id", "user_id", "period"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    period: Mapped[date] = mapped_column(Date, index=True)
    target_amount: Mapped[Decimal] = mapped_column(Money)
