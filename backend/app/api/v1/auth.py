import uuid
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select, update
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
from app.models import Organization, RefreshToken, Role, User
from app.core.config import get_settings
from app.core.passwords import DEMO_PASSWORDS_FIXED, password_problem
from app.core.ratelimit import RateLimiter, client_ip
from app.schemas.auth import ChangePasswordRequest, LoginRequest, RefreshRequest, RegisterRequest, TokenPair
from app.schemas.org import MeOut, OrganizationOut, UserOut
from app.services import audit

router = APIRouter(prefix="/auth", tags=["auth"])


MAX_FAILED_LOGINS = 5
LOCKOUT = timedelta(minutes=15)
# Two tabs refreshing at the same moment both present the same refresh token. Within this window a
# second use is treated as that, not as a stolen token.
REUSE_GRACE = timedelta(seconds=30)

login_limiter = RateLimiter("login-ip", get_settings().login_rate_limit_per_minute, 60,
                            "Too many sign-in attempts from your network. Please wait a minute.")
register_limiter = RateLimiter("register-ip", get_settings().register_rate_limit_per_hour, 3600,
                               "Too many new accounts from your network. Please try again later.")


def issue_tokens(db: Session, user: User, family_id: uuid.UUID | None = None) -> TokenPair:
    """A new access token and a single-use refresh token (recorded; the caller commits).

    A sign-in starts a new family; each refresh continues it, so signing out ends just that device.
    """
    user_id, org_id, version = str(user.id), str(user.organization_id), user.token_version
    record = RefreshToken(id=uuid.uuid4(), organization_id=user.organization_id, user_id=user.id,
                          family_id=family_id or uuid.uuid4(),
                          expires_at=datetime.now(timezone.utc) + timedelta(days=get_settings().refresh_token_days))
    db.add(record)
    db.flush()
    return TokenPair(
        access_token=create_token(user_id, org_id, "access", version),
        refresh_token=create_token(user_id, org_id, "refresh", version, jti=str(record.id)),
    )


def _aware(moment: datetime | None) -> datetime | None:
    return moment.replace(tzinfo=timezone.utc) if moment and moment.tzinfo is None else moment


@router.post("/register", response_model=TokenPair, status_code=status.HTTP_201_CREATED)
def register(body: RegisterRequest, request: Request, db: Session = Depends(get_db)):
    """Create a new organisation with its first user as organisation admin."""
    register_limiter.check(db, client_ip(request))
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
    tokens = issue_tokens(db, user)
    db.commit()
    return tokens


@router.post("/login", response_model=TokenPair)
def login(body: LoginRequest, request: Request, db: Session = Depends(get_db)):
    login_limiter.check(db, client_ip(request))  # many accounts from one address: password spraying
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
    tokens = issue_tokens(db, user)
    db.commit()
    return tokens


def _refresh_claims(db: Session, token: str) -> tuple[User | None, RefreshToken | None, dict]:
    try:
        payload = decode_token(token, "refresh")
        set_tenant(db, uuid.UUID(payload["org"]))
        user = db.get(User, uuid.UUID(payload["sub"]))
        record = db.get(RefreshToken, uuid.UUID(payload["jti"])) if payload.get("jti") else None
    except (jwt.InvalidTokenError, ValueError):
        return None, None, {}
    if record is not None and user is not None and record.user_id != user.id:
        record = None
    return user, record, payload


@router.post("/refresh", response_model=TokenPair)
def refresh(body: RefreshRequest, db: Session = Depends(get_db)):
    """Swap a refresh token for new tokens. Each refresh token works once."""
    user, record, payload = _refresh_claims(db, body.refresh_token)
    invalid = HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid refresh token")
    if (user is None or record is None or record.revoked_at is not None or not user.is_active
            or user.deleted_at is not None or payload.get("ver", 0) != user.token_version):
        raise invalid
    now = datetime.now(timezone.utc)
    used = _aware(record.used_at)
    if used is not None and now - used > REUSE_GRACE:
        # An old refresh token came back: someone kept a copy. End every session of this user;
        # they sign in again with their password, the thief can't.
        user.token_version += 1
        audit.record(db, user.organization_id, user.id, "user.refresh_token_reused", "user", user.id)
        db.commit()
        raise invalid
    record.used_at = used or now
    tokens = issue_tokens(db, user, record.family_id)
    db.commit()
    return tokens


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(body: RefreshRequest, db: Session = Depends(get_db)):
    """Sign this device out: its refresh token (and any it was swapped for) stops working."""
    user, record, _ = _refresh_claims(db, body.refresh_token)
    if user is not None and record is not None:
        db.execute(update(RefreshToken).where(RefreshToken.family_id == record.family_id, RefreshToken.revoked_at.is_(None))
                   .values(revoked_at=datetime.now(timezone.utc)))
        db.commit()


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
    tokens = issue_tokens(db, user)
    db.commit()
    return tokens


@router.get("/me", response_model=MeOut)
def me(user: User = Depends(get_current_user), org: Organization = Depends(get_current_org)):
    return MeOut(user=UserOut.model_validate(user), organization=OrganizationOut.model_validate(org))
