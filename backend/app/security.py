from datetime import datetime, timedelta, timezone
from uuid import uuid4

import jwt
from pwdlib import PasswordHash

from app.config import settings

password_hash = PasswordHash.recommended()


def hash_password(password: str) -> str:
    return password_hash.hash(password)


def verify_password(password: str, encoded: str) -> bool:
    try:
        return password_hash.verify(password, encoded)
    except Exception:
        return False


def issue_token(user_id: str, store_id: str) -> tuple[str, str, datetime]:
    jti = str(uuid4())
    expires = datetime.now(timezone.utc) + timedelta(minutes=settings.access_token_minutes)
    token = jwt.encode({"sub": user_id, "store_id": store_id, "jti": jti, "exp": expires}, settings.jwt_secret, algorithm="HS256")
    return token, jti, expires
