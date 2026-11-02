"""Data-level access rules: which salespeople's data a user may see.

Every analytics and listing query is restricted to the caller's organisation *and* to the
salesperson scope returned here. `None` means "everyone in the organisation".
"""

import uuid

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ORG_WIDE_ROLES, Customer, Role, Territory, User


def salesperson_scope(db: Session, user: User) -> set[uuid.UUID] | None:
    if user.role in ORG_WIDE_ROLES:
        return None

    stmt = select(User.id).where(
        User.organization_id == user.organization_id, User.deleted_at.is_(None)
    )
    if user.role == Role.REGIONAL_MANAGER and user.territory_id:
        # A regional manager sees everyone in territories that share their region.
        region = db.scalar(select(Territory.region).where(Territory.id == user.territory_id))
        territories = select(Territory.id).where(
            Territory.organization_id == user.organization_id,
            (Territory.region == region) if region else (Territory.id == user.territory_id),
        )
        stmt = stmt.where(User.territory_id.in_(territories))
    elif user.role == Role.SALES_MANAGER and user.team_id:
        stmt = stmt.where(User.team_id == user.team_id)
    else:
        return {user.id}

    return set(db.scalars(stmt)) | {user.id}


def visible_customer(db: Session, user: User, customer_id: uuid.UUID) -> Customer:
    """The customer, if it is in the caller's organisation and assigned to someone in their scope."""
    customer = db.get(Customer, customer_id)
    if customer is None or customer.organization_id != user.organization_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Customer not found")
    scope = salesperson_scope(db, user)
    if scope is not None and customer.owner_id not in scope:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You cannot view this customer")
    return customer


def resolve_salesperson_filter(
    db: Session,
    user: User,
    salesperson_id: uuid.UUID | None = None,
    team_id: uuid.UUID | None = None,
) -> set[uuid.UUID] | None:
    """Combine the caller's scope with optional salesperson/team filters from the request."""
    scope = salesperson_scope(db, user)

    if salesperson_id is not None:
        target = db.get(User, salesperson_id)
        if target is None or target.organization_id != user.organization_id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Salesperson not found")
        if scope is not None and salesperson_id not in scope:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "You cannot view this salesperson's data")
        scope = {salesperson_id}

    if team_id is not None:
        members = set(
            db.scalars(
                select(User.id).where(
                    User.organization_id == user.organization_id, User.team_id == team_id
                )
            )
        )
        scope = members if scope is None else scope & members

    return scope
