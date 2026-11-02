import uuid

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import decode_token
from app.models import Organization, Role, User

bearer = HTTPBearer(auto_error=False)

_unauthorized = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Not authenticated",
    headers={"WWW-Authenticate": "Bearer"},
)


def get_current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(get_db),
) -> User:
    if creds is None:
        raise _unauthorized
    try:
        payload = decode_token(creds.credentials, "access")
        user_id = uuid.UUID(payload["sub"])
    except (jwt.InvalidTokenError, ValueError):
        raise _unauthorized from None

    # Role and organisation are always re-read from the database, never trusted from the token.
    user = db.get(User, user_id)
    if (
        user is None
        or not user.is_active
        or user.deleted_at is not None
        or str(user.organization_id) != payload["org"]
    ):
        raise _unauthorized
    return user


def get_current_org(
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> Organization:
    return db.get(Organization, user.organization_id)


def require_roles(*roles: Role):
    allowed = frozenset(roles)

    def dependency(user: User = Depends(get_current_user)) -> User:
        if user.role not in allowed:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "You do not have permission for this action")
        return user

    return dependency
