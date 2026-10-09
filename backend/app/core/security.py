import uuid
from datetime import datetime, timedelta, timezone
from typing import Literal

import jwt
from passlib.context import CryptContext

from app.core.config import settings

_pwd_context = CryptContext(schemes=["argon2"], deprecated="auto")

TokenType = Literal["access", "refresh"]


def hash_password(password: str) -> str:
    return _pwd_context.hash(password)


def verify_password(password: str, hashed_password: str) -> bool:
    return _pwd_context.verify(password, hashed_password)


def create_token(subject: uuid.UUID, token_type: TokenType) -> str:
    now = datetime.now(timezone.utc)
    if token_type == "access":
        expire = now + timedelta(minutes=settings.jwt_access_token_expire_minutes)
    else:
        expire = now + timedelta(days=settings.jwt_refresh_token_expire_days)

    payload = {"sub": str(subject), "type": token_type, "iat": now, "exp": expire}
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def decode_token(token: str, expected_type: TokenType) -> uuid.UUID:
    verify_exp = settings.environment != "development"
    payload = jwt.decode(
        token,
        settings.jwt_secret_key,
        algorithms=[settings.jwt_algorithm],
        options={"verify_exp": verify_exp},
    )
    if payload.get("type") != expected_type:
        raise jwt.InvalidTokenError(f"expected a {expected_type} token")
    return uuid.UUID(payload["sub"])
