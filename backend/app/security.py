"""Cryptographic primitives; no credentials or token values are logged."""

import hashlib
import hmac
from functools import lru_cache

import jwt
from argon2 import PasswordHasher, Type
from argon2.exceptions import InvalidHashError, VerificationError

from app.config import Settings
from app.models import AuthSession, User

password_hasher = PasswordHasher(type=Type.ID)


@lru_cache(maxsize=1)
def dummy_password_hash() -> str:
    # Same verification work for a missing login; never a usable account password.
    import secrets

    return password_hasher.hash(secrets.token_urlsafe(32))


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return password_hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


def context_digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def csrf_token(settings: Settings, digest: str, session_id) -> str:
    # Purpose separation and an explicit session binding. Only the cookie digest is persisted.
    message = f"csrf-v1:{digest}:{session_id or 'anonymous'}".encode()
    return hmac.new(settings.auth_secret().encode(), message, hashlib.sha256).hexdigest()


def encode_session(settings: Settings, user: User, session: AuthSession) -> str:
    return jwt.encode(
        {
            "sub": str(user.id),
            "role": user.role,
            "sid": str(session.id),
            "iat": int(session.created_at.timestamp()),
            "exp": int(session.expires_at.timestamp()),
            "iss": settings.auth_jwt_issuer,
            "aud": settings.auth_jwt_audience,
        },
        settings.auth_secret(),
        algorithm="HS256",
    )


def decode_session(settings: Settings, token: str) -> dict:
    return jwt.decode(
        token,
        settings.auth_secret(),
        algorithms=["HS256"],
        issuer=settings.auth_jwt_issuer,
        audience=settings.auth_jwt_audience,
        options={"require": ["sub", "role", "sid", "iat", "exp", "iss", "aud"], "strict_aud": True},
    )
