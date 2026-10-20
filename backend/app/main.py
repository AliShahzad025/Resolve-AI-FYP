from datetime import datetime, timezone

from fastapi import Depends, FastAPI, HTTPException, Response, status
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from app.config import settings
from app.database import get_db
from app.dependencies import Principal, bearer, current_principal, require_owner
from app.models import AuthSession, Membership, Store, User
from app.schemas import AuthOut, LoginRequest, MemberCreate, MemberOut, RegisterRequest, StoreOut
from app.security import hash_password, issue_token, verify_password
from app.shopify_routes import router as shopify_router

app = FastAPI(title="Resolve AI API", version="0.1.0")
app.include_router(shopify_router)
app.add_middleware(CORSMiddleware, allow_origins=settings.allowed_origins, allow_credentials=True, allow_methods=["GET", "POST", "DELETE"], allow_headers=["Authorization", "Content-Type"])


def auth_response(db: Session, user: User, membership: Membership, memberships: list[Membership] | None = None) -> AuthOut:
    token, jti, expires = issue_token(str(user.id), str(membership.store_id))
    db.add(AuthSession(jti=jti, user_id=user.id, store_id=membership.store_id, expires_at=expires))
    db.commit()
    rows = memberships or [membership]
    available = [StoreOut(id=row.store.id, name=row.store.name, role=row.role.title()) for row in rows]
    return AuthOut(access_token=token, user=user, store=StoreOut(id=membership.store.id, name=membership.store.name, role=membership.role.title()), available_stores=available)


@app.get("/api/health")
def health(db: Session = Depends(get_db)):
    from sqlalchemy import text
    db.execute(text("SELECT 1"))
    return {"status": "ok", "database": "connected"}


@app.post("/api/auth/register", response_model=AuthOut, status_code=status.HTTP_201_CREATED)
def register(body: RegisterRequest, db: Session = Depends(get_db)):
    email = str(body.email).strip().lower()
    if db.scalar(select(User.id).where(func.lower(User.email) == email)):
        raise HTTPException(status_code=409, detail="An account with this email already exists")
    user = User(name=body.name.strip(), email=email, password_hash=hash_password(body.password), active=True)
    store = Store(name=body.store_name.strip())
    db.add_all([user, store])
    try:
        db.flush()
        membership = Membership(user=user, store=store, role="owner", active=True)
        db.add(membership)
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="An account with this email already exists")
    return auth_response(db, user, membership)


@app.post("/api/auth/login", response_model=AuthOut)
def login(body: LoginRequest, db: Session = Depends(get_db)):
    email = str(body.email).strip().lower()
    user = db.scalar(select(User).where(func.lower(User.email) == email))
    # Keep the same response for unknown, inactive, and bad-password accounts.
    if not user or not user.active or not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    memberships = db.scalars(select(Membership).options(joinedload(Membership.store)).where(Membership.user_id == user.id, Membership.active.is_(True)).order_by(Membership.store_id)).all()
    if not memberships:
        raise HTTPException(status_code=401, detail="Invalid email or password")
    if body.store_id is None and len(memberships) > 1:
        available = [StoreOut(id=row.store.id, name=row.store.name, role=row.role.title()) for row in memberships]
        return AuthOut(access_token="", user=user, store=available[0], available_stores=available)
    membership = next((row for row in memberships if row.store_id == body.store_id), None) if body.store_id else memberships[0]
    if not membership:
        raise HTTPException(status_code=401, detail="Invalid email or password")
    return auth_response(db, user, membership, memberships)


@app.get("/api/auth/me", response_model=AuthOut)
def me(principal: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    membership = principal.membership
    memberships = db.scalars(select(Membership).options(joinedload(Membership.store)).where(Membership.user_id == principal.user.id, Membership.active.is_(True)).order_by(Membership.store_id)).all()
    available = [StoreOut(id=row.store.id, name=row.store.name, role=row.role.title()) for row in memberships]
    return AuthOut(access_token="", user=principal.user, store=StoreOut(id=membership.store.id, name=membership.store.name, role=membership.role.title()), available_stores=available)


@app.post("/api/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(principal: Principal = Depends(current_principal), db: Session = Depends(get_db), credentials=Depends(bearer)):
    # The active token identifier is extracted only after current_principal validated it.
    import jwt
    payload = jwt.decode(credentials.credentials, settings.jwt_secret, algorithms=["HS256"])
    session = db.get(AuthSession, payload["jti"])
    if session:
        session.revoked_at = datetime.now(timezone.utc)
        db.commit()
    return Response(status_code=204)


@app.get("/api/store", response_model=StoreOut)
def get_store(principal: Principal = Depends(current_principal)):
    return StoreOut(id=principal.membership.store.id, name=principal.membership.store.name, role=principal.membership.role.title())


@app.get("/api/members", response_model=list[MemberOut])
def list_members(principal: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    rows = db.scalars(select(Membership).options(joinedload(Membership.user)).where(Membership.store_id == principal.membership.store_id).order_by(Membership.role, Membership.user_id)).all()
    return [MemberOut(id=row.id, name=row.user.name, email=row.user.email, role=row.role.title(), active=row.active) for row in rows]


@app.post("/api/members", response_model=MemberOut, status_code=status.HTTP_201_CREATED)
def add_member(body: MemberCreate, principal: Principal = Depends(require_owner), db: Session = Depends(get_db)):
    email = str(body.email).strip().lower()
    user = db.scalar(select(User).where(func.lower(User.email) == email))
    if user and not user.active:
        raise HTTPException(status_code=409, detail="This account is inactive")
    if not user:
        if not body.password:
            raise HTTPException(status_code=422, detail="An initial password is required for a new account")
        user = User(name=body.name.strip(), email=email, password_hash=hash_password(body.password), active=True)
        db.add(user)
        try:
            db.flush()
        except IntegrityError:
            db.rollback()
            raise HTTPException(status_code=409, detail="An account with this email already exists")
        membership = Membership(store_id=principal.membership.store_id, user_id=user.id, role=body.role.lower(), active=True)
        db.add(membership)
    else:
        existing_member = db.scalar(select(Membership).where(Membership.store_id == principal.membership.store_id, Membership.user_id == user.id, Membership.active.is_(True)))
        if existing_member:
            raise HTTPException(status_code=409, detail="This user is already a member of this store")
        existing_member = db.scalar(select(Membership).where(Membership.store_id == principal.membership.store_id, Membership.user_id == user.id, Membership.active.is_(False)).limit(1))
        if existing_member:
            existing_member.active = True
            existing_member.role = body.role.lower()
            membership = existing_member
        else:
            membership = Membership(store_id=principal.membership.store_id, user_id=user.id, role=body.role.lower(), active=True)
            db.add(membership)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="This member is already in the store")
    db.refresh(membership)
    return MemberOut(id=membership.id, name=user.name, email=user.email, role=membership.role.title(), active=membership.active)


@app.patch("/api/members/{membership_id}", response_model=MemberOut)
def update_member(membership_id: str, active: bool, principal: Principal = Depends(require_owner), db: Session = Depends(get_db)):
    from uuid import UUID
    try:
        member_id = UUID(membership_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Member not found")
    row = db.scalar(select(Membership).options(joinedload(Membership.user)).where(Membership.id == member_id, Membership.store_id == principal.membership.store_id))
    if not row or row.role == "owner":
        raise HTTPException(status_code=404, detail="Member not found")
    row.active = active
    db.commit()
    return MemberOut(id=row.id, name=row.user.name, email=row.user.email, role=row.role.title(), active=row.active)


@app.patch("/api/members/{membership_id}/role", response_model=MemberOut)
def change_role(membership_id: str, role: str, principal: Principal = Depends(require_owner), db: Session = Depends(get_db)):
    from uuid import UUID
    if role not in {"Manager", "Agent"}:
        raise HTTPException(status_code=422, detail="Role must be Manager or Agent")
    try:
        member_id = UUID(membership_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Member not found")
    row = db.scalar(select(Membership).options(joinedload(Membership.user)).where(Membership.id == member_id, Membership.store_id == principal.membership.store_id))
    if not row or row.role == "owner":
        raise HTTPException(status_code=404, detail="Member not found")
    row.role = role.lower()
    db.commit()
    return MemberOut(id=row.id, name=row.user.name, email=row.user.email, role=row.role.title(), active=row.active)
