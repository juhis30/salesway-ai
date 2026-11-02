import uuid

import jwt
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_current_org, get_current_user
from app.core.security import (
    DUMMY_PASSWORD_HASH,
    create_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.models import Organization, Role, User
from app.schemas.auth import LoginRequest, RefreshRequest, RegisterRequest, TokenPair
from app.schemas.org import MeOut, OrganizationOut, UserOut
from app.services import audit

router = APIRouter(prefix="/auth", tags=["auth"])


def issue_tokens(user: User) -> TokenPair:
    user_id, org_id = str(user.id), str(user.organization_id)
    return TokenPair(
        access_token=create_token(user_id, org_id, "access"),
        refresh_token=create_token(user_id, org_id, "refresh"),
    )


@router.post("/register", response_model=TokenPair, status_code=status.HTTP_201_CREATED)
def register(body: RegisterRequest, db: Session = Depends(get_db)):
    """Create a new organisation with its first user as organisation admin."""
    email = body.email.lower()
    if db.scalar(select(User.id).where(User.email == email)):
        raise HTTPException(status.HTTP_409_CONFLICT, "An account with this email already exists")

    org = Organization(name=body.organization_name, industry=body.industry, timezone=body.timezone)
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
    user = db.scalar(select(User).where(User.email == body.email.lower()))
    password_ok = verify_password(body.password, user.hashed_password if user else DUMMY_PASSWORD_HASH)
    if user is None or not password_ok or not user.is_active or user.deleted_at is not None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid email or password")
    audit.record(db, user.organization_id, user.id, "user.login")
    db.commit()
    return issue_tokens(user)


@router.post("/refresh", response_model=TokenPair)
def refresh(body: RefreshRequest, db: Session = Depends(get_db)):
    try:
        payload = decode_token(body.refresh_token, "refresh")
        user = db.get(User, uuid.UUID(payload["sub"]))
    except (jwt.InvalidTokenError, ValueError):
        user = None
    if user is None or not user.is_active or user.deleted_at is not None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid refresh token")
    return issue_tokens(user)


@router.get("/me", response_model=MeOut)
def me(user: User = Depends(get_current_user), org: Organization = Depends(get_current_org)):
    return MeOut(user=UserOut.model_validate(user), organization=OrganizationOut.model_validate(org))
