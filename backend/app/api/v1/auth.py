import uuid
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db, rls_bypass, set_tenant
from app.core.deps import get_current_org, get_current_user
from app.core.security import (
    DUMMY_PASSWORD_HASH,
    create_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.models import Organization, Role, User
from app.core.passwords import DEMO_PASSWORDS_FIXED, password_problem
from app.schemas.auth import ChangePasswordRequest, LoginRequest, RefreshRequest, RegisterRequest, TokenPair
from app.schemas.org import MeOut, OrganizationOut, UserOut
from app.services import audit

router = APIRouter(prefix="/auth", tags=["auth"])


MAX_FAILED_LOGINS = 5
LOCKOUT = timedelta(minutes=15)


def issue_tokens(user: User) -> TokenPair:
    user_id, org_id, version = str(user.id), str(user.organization_id), user.token_version
    return TokenPair(
        access_token=create_token(user_id, org_id, "access", version),
        refresh_token=create_token(user_id, org_id, "refresh", version),
    )


def _aware(moment: datetime | None) -> datetime | None:
    return moment.replace(tzinfo=timezone.utc) if moment and moment.tzinfo is None else moment


@router.post("/register", response_model=TokenPair, status_code=status.HTTP_201_CREATED)
def register(body: RegisterRequest, db: Session = Depends(get_db)):
    """Create a new organisation with its first user as organisation admin."""
    email = body.email.lower()
    with rls_bypass(db):  # emails are unique across all organisations
        if db.scalar(select(User.id).where(User.email == email)):
            raise HTTPException(status.HTTP_409_CONFLICT, "An account with this email already exists")

    # The id is chosen up front so the new organisation can be created while acting for it.
    org = Organization(id=uuid.uuid4(), name=body.organization_name, industry=body.industry, timezone=body.timezone)
    set_tenant(db, org.id)
    db.add(org)
    db.flush()
    user = User(
        organization_id=org.id,
        email=email,
        full_name=body.full_name,
        hashed_password=hash_password(body.password),
        role=Role.ORG_ADMIN,
    )
    db.add(user)
    db.flush()
    audit.record(db, org.id, user.id, "organization.created", "organization", org.id)
    db.commit()
    return issue_tokens(user)


@router.post("/login", response_model=TokenPair)
def login(body: LoginRequest, db: Session = Depends(get_db)):
    with rls_bypass(db):  # the organisation isn't known until the user is found
        user = db.scalar(select(User).where(User.email == body.email.lower()))
    now = datetime.now(timezone.utc)
    if user is not None:
        set_tenant(db, user.organization_id)
        locked = _aware(user.locked_until)
        if locked and locked > now:
            minutes = max(1, round((locked - now).total_seconds() / 60))
            raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS,
                                f"Too many wrong passwords. Try again in {minutes} minute{'s' if minutes != 1 else ''}, "
                                "or ask your admin to reset your password.")
    password_ok = verify_password(body.password, user.hashed_password if user else DUMMY_PASSWORD_HASH)
    # The shared demo's passwords are public, so a lockout there would only let one visitor lock out everyone.
    if user is not None and not password_ok and not db.get(Organization, user.organization_id).is_shared_demo:
        user.failed_logins += 1
        if user.failed_logins >= MAX_FAILED_LOGINS:
            user.locked_until, user.failed_logins = now + LOCKOUT, 0
            audit.record(db, user.organization_id, user.id, "user.locked", "user", user.id)
        db.commit()
    if user is None or not password_ok or not user.is_active or user.deleted_at is not None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid email or password")
    user.failed_logins, user.locked_until = 0, None
    audit.record(db, user.organization_id, user.id, "user.login")
    db.commit()
    return issue_tokens(user)


@router.post("/refresh", response_model=TokenPair)
def refresh(body: RefreshRequest, db: Session = Depends(get_db)):
    payload: dict = {}
    try:
        payload = decode_token(body.refresh_token, "refresh")
        set_tenant(db, uuid.UUID(payload["org"]))
        user = db.get(User, uuid.UUID(payload["sub"]))
    except (jwt.InvalidTokenError, ValueError):
        user = None
    if user is None or not user.is_active or user.deleted_at is not None or payload.get("ver", 0) != user.token_version:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid refresh token")
    return issue_tokens(user)


@router.post("/change-password", response_model=TokenPair)
def change_password(body: ChangePasswordRequest, user: User = Depends(get_current_user),
                    org: Organization = Depends(get_current_org), db: Session = Depends(get_db)):
    """Change your own password. Every other session is signed out; this one gets new tokens."""
    if org.is_shared_demo:
        raise HTTPException(status.HTTP_403_FORBIDDEN, DEMO_PASSWORDS_FIXED)
    if not verify_password(body.current_password, user.hashed_password):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Your current password is wrong")
    if problem := password_problem(body.new_password, user.email):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, problem)
    user.hashed_password = hash_password(body.new_password)
    user.password_changed_at = datetime.now(timezone.utc)
    user.token_version += 1
    audit.record(db, user.organization_id, user.id, "user.password_changed", "user", user.id)
    db.commit()
    return issue_tokens(user)


@router.get("/me", response_model=MeOut)
def me(user: User = Depends(get_current_user), org: Organization = Depends(get_current_org)):
    return MeOut(user=UserOut.model_validate(user), organization=OrganizationOut.model_validate(org))
