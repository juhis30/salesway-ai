from datetime import datetime, timedelta, timezone
from typing import Literal

import bcrypt
import jwt

from app.core.config import get_settings

TokenType = Literal["access", "refresh"]


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode(), hashed.encode())
    except ValueError:
        return False


# Compared against when the email is unknown, so login timing doesn't reveal which emails exist.
DUMMY_PASSWORD_HASH = hash_password("not-a-real-password")


def create_token(user_id: str, organization_id: str, token_type: TokenType, version: int = 0,
                 jti: str | None = None) -> str:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    if token_type == "access":
        expires = now + timedelta(minutes=settings.access_token_minutes)
    else:
        expires = now + timedelta(days=settings.refresh_token_days)
    payload = {
        "sub": user_id,
        "org": organization_id,
        "type": token_type,
        "ver": version,
        "iat": now,
        "exp": expires,
    }
    if jti:
        payload["jti"] = jti  # refresh tokens: identifies the stored record that makes them single-use
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_token(token: str, expected_type: TokenType) -> dict:
    """Raises jwt.InvalidTokenError if the token is invalid, expired or of the wrong type."""
    settings = get_settings()
    # Tokens signed with a previous secret stay valid during a rotation (JWT_PREVIOUS_SECRETS).
    for secret in [settings.jwt_secret, *settings.jwt_previous_secrets]:
        try:
            payload = jwt.decode(
                token,
                secret,
                algorithms=[settings.jwt_algorithm],
                options={"require": ["sub", "org", "type", "exp"]},
            )
            break
        except jwt.InvalidSignatureError:
            continue
    else:
        raise jwt.InvalidSignatureError("signature verification failed")
    if payload["type"] != expected_type:
        raise jwt.InvalidTokenError("wrong token type")
    return payload
