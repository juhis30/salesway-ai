import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.database import get_db, rls_bypass
from app.core.deps import get_current_org, get_current_user, require_roles
from app.core.security import hash_password
from app.models import ADMIN_ROLES, Organization, Role, Team, Territory, User
from app.core.passwords import DEMO_PASSWORDS_FIXED, password_problem
from app.schemas.org import (
    PasswordReset,
    OrganizationOut,
    OrganizationUpdate,
    TeamIn,
    TeamOut,
    TerritoryIn,
    TerritoryOut,
    UserCreate,
    UserOut,
    UserUpdate,
)
from app.services import audit
from app.services.access import salesperson_scope

router = APIRouter(tags=["organization"])
admin_only = require_roles(*ADMIN_ROLES)


@router.get("/organization", response_model=OrganizationOut)
def get_organization(org: Organization = Depends(get_current_org)):
    return org


@router.patch("/organization", response_model=OrganizationOut)
def update_organization(
    body: OrganizationUpdate,
    user: User = Depends(admin_only),
    db: Session = Depends(get_db),
):
    org = db.get(Organization, user.organization_id)
    changes = body.model_dump(exclude_unset=True)
    for field, value in changes.items():
        setattr(org, field, value)
    audit.record(db, org.id, user.id, "organization.updated", "organization", org.id, changes)
    db.commit()
    return org


# --- Territories and teams -------------------------------------------------------------


@router.get("/territories", response_model=list[TerritoryOut])
def list_territories(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return db.scalars(
        select(Territory).where(Territory.organization_id == user.organization_id).order_by(Territory.name)
    ).all()


@router.post("/territories", response_model=TerritoryOut, status_code=status.HTTP_201_CREATED)
def create_territory(body: TerritoryIn, user: User = Depends(admin_only), db: Session = Depends(get_db)):
    territory = Territory(organization_id=user.organization_id, **body.model_dump())
    db.add(territory)
    try:
        db.commit()
    except IntegrityError:
        raise HTTPException(status.HTTP_409_CONFLICT, "A territory with this name already exists") from None
    return territory


@router.get("/teams", response_model=list[TeamOut])
def list_teams(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return db.scalars(
        select(Team).where(Team.organization_id == user.organization_id).order_by(Team.name)
    ).all()


@router.post("/teams", response_model=TeamOut, status_code=status.HTTP_201_CREATED)
def create_team(body: TeamIn, user: User = Depends(admin_only), db: Session = Depends(get_db)):
    team = Team(organization_id=user.organization_id, **body.model_dump())
    db.add(team)
    try:
        db.commit()
    except IntegrityError:
        raise HTTPException(status.HTTP_409_CONFLICT, "A team with this name already exists") from None
    return team


# --- Users -----------------------------------------------------------------------------


def _check_org_refs(db: Session, org_id: uuid.UUID, team_id: uuid.UUID | None, territory_id: uuid.UUID | None):
    if team_id is not None:
        team = db.get(Team, team_id)
        if team is None or team.organization_id != org_id:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Unknown team")
    if territory_id is not None:
        territory = db.get(Territory, territory_id)
        if territory is None or territory.organization_id != org_id:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Unknown territory")


def _check_role_assignable(actor: User, role: Role):
    if role == Role.SUPER_ADMIN and actor.role != Role.SUPER_ADMIN:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only a super admin can grant super admin")


@router.get("/users", response_model=list[UserOut])
def list_users(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    stmt = select(User).where(User.organization_id == user.organization_id, User.deleted_at.is_(None))
    scope = salesperson_scope(db, user)
    if scope is not None:
        stmt = stmt.where(User.id.in_(scope))
    return db.scalars(stmt.order_by(User.full_name)).all()


@router.post("/users", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def create_user(body: UserCreate, actor: User = Depends(admin_only), db: Session = Depends(get_db)):
    _check_role_assignable(actor, body.role)
    _check_org_refs(db, actor.organization_id, body.team_id, body.territory_id)
    email = body.email.lower()
    with rls_bypass(db):  # emails are unique across all organisations
        taken = db.scalar(select(User.id).where(User.email == email))
    if taken:
        raise HTTPException(status.HTTP_409_CONFLICT, "An account with this email already exists")

    user = User(
        organization_id=actor.organization_id,
        email=email,
        full_name=body.full_name,
        hashed_password=hash_password(body.password),
        role=body.role,
        phone=body.phone,
        team_id=body.team_id,
        territory_id=body.territory_id,
    )
    db.add(user)
    db.flush()
    audit.record(db, actor.organization_id, actor.id, "user.created", "user", user.id, {"role": body.role.value})
    db.commit()
    return user


@router.patch("/users/{user_id}", response_model=UserOut)
def update_user(
    user_id: uuid.UUID,
    body: UserUpdate,
    actor: User = Depends(admin_only),
    db: Session = Depends(get_db),
):
    user = db.get(User, user_id)
    if user is None or user.organization_id != actor.organization_id or user.deleted_at is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")

    changes = body.model_dump(exclude_unset=True)
    if "role" in changes:
        _check_role_assignable(actor, changes["role"])
    if user.id == actor.id and (changes.get("is_active") is False or changes.get("role", actor.role) != actor.role):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "You cannot deactivate or demote yourself")
    _check_org_refs(db, actor.organization_id, changes.get("team_id"), changes.get("territory_id"))

    if changes.get("is_active") is False or ("role" in changes and changes["role"] != user.role):
        user.token_version += 1  # signs them out everywhere right away
    for field, value in changes.items():
        setattr(user, field, value)
    if changes.get("whatsapp_opt_in"):
        user.whatsapp_opt_in_at = datetime.now(timezone.utc)
    audit.record(
        db, actor.organization_id, actor.id, "user.updated", "user", user.id,
        {k: str(v) for k, v in changes.items()},
    )
    db.commit()
    return user


@router.post("/users/{user_id}/reset-password", status_code=status.HTTP_204_NO_CONTENT)
def reset_password(user_id: uuid.UUID, body: PasswordReset, actor: User = Depends(admin_only), db: Session = Depends(get_db)):
    """Set a temporary password for someone (also unlocks the account and signs them out everywhere)."""
    if db.get(Organization, actor.organization_id).is_shared_demo:
        raise HTTPException(status.HTTP_403_FORBIDDEN, DEMO_PASSWORDS_FIXED)
    user = db.get(User, user_id)
    if user is None or user.organization_id != actor.organization_id or user.deleted_at is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    if problem := password_problem(body.new_password, user.email):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, problem)
    user.hashed_password = hash_password(body.new_password)
    user.password_changed_at = datetime.now(timezone.utc)
    user.token_version += 1
    user.failed_logins, user.locked_until = 0, None
    audit.record(db, actor.organization_id, actor.id, "user.password_reset", "user", user.id)
    db.commit()
