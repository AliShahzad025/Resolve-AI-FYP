from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.config import settings
from app.database import get_db
from app.models import AuthSession, Membership, User

bearer = HTTPBearer(auto_error=False)


@dataclass
class Principal:
    user: User
    membership: Membership


def current_principal(credentials: HTTPAuthorizationCredentials | None = Depends(bearer), db: Session = Depends(get_db)) -> Principal:
    unauthorized = HTTPException(status_code=401, detail="Invalid email or password")
    if not credentials:
        raise unauthorized
    try:
        payload = jwt.decode(credentials.credentials, settings.jwt_secret, algorithms=["HS256"])
        user_id, store_id, jti = UUID(payload["sub"]), UUID(payload["store_id"]), payload["jti"]
    except (jwt.PyJWTError, KeyError, ValueError):
        raise unauthorized
    session = db.get(AuthSession, jti)
    if not session or session.store_id != store_id or session.revoked_at or session.expires_at <= datetime.now(timezone.utc):
        raise unauthorized
    membership = db.scalar(select(Membership).options(joinedload(Membership.store)).where(Membership.user_id == user_id, Membership.store_id == store_id, Membership.active.is_(True)))
    user = db.get(User, user_id)
    if not user or not user.active or not membership:
        raise unauthorized
    return Principal(user=user, membership=membership)


def require_owner(principal: Principal = Depends(current_principal)) -> Principal:
    if principal.membership.role != "owner":
        raise HTTPException(status_code=403, detail="Owner permission required")
    return principal
