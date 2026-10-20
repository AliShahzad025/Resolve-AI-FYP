import json
import secrets
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.dependencies import Principal, current_principal, require_owner
from app.models import Customer, Membership, Order, ShopifyCredential, ShopifyOAuthState, Store, User
from app.shopify_service import (
    SCOPES,
    encrypt_bundle,
    ensure_encryption_ready,
    exchange_code,
    fetch_and_store_orders,
    order_output,
    process_webhook,
    register_webhooks,
    validate_shop_domain,
    verify_oauth_hmac,
    verify_webhook_hmac,
)

router = APIRouter()


class ShopifyConnectRequest(BaseModel):
    shop_domain: str


@router.get("/api/shopify/status")
def shopify_status(principal: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    store = principal.membership.store
    credential = db.scalar(select(ShopifyCredential).where(ShopifyCredential.store_id == store.id))
    return {
        "connected": bool(store.shopify_domain and credential),
        "shop_domain": store.shopify_domain,
        "connection_status": store.connection_status,
        "last_synced_at": store.last_synced_at,
        "webhooks_registered": credential.webhooks_registered if credential else False,
        "webhook_error": credential.webhook_error if credential else None,
        "scopes": credential.scopes.split(",") if credential and credential.scopes else [],
    }


@router.post("/api/shopify/connect")
def shopify_connect(body: ShopifyConnectRequest, principal: Principal = Depends(require_owner), db: Session = Depends(get_db)):
    if not settings.shopify_api_key or not settings.shopify_api_secret or not settings.shopify_app_base_url:
        raise HTTPException(status_code=503, detail="Configure the Shopify app key, secret, and public backend URL first")
    ensure_encryption_ready()
    shop = validate_shop_domain(body.shop_domain)
    store = db.scalar(select(Store).where(Store.id == principal.membership.store_id).with_for_update())
    if not store:
        raise HTTPException(status_code=404, detail="Store not found")
    existing_credential = db.scalar(select(ShopifyCredential.id).where(ShopifyCredential.store_id == store.id))
    if existing_credential and store.shopify_domain and store.shopify_domain != shop:
        raise HTTPException(status_code=409, detail="Disconnect the currently connected Shopify store first")
    state = secrets.token_urlsafe(32)
    db.add(ShopifyOAuthState(state=state, store_id=store.id, user_id=principal.user.id, shop_domain=shop, expires_at=datetime.now(timezone.utc) + timedelta(minutes=10)))
    db.commit()
    callback = settings.shopify_app_base_url.strip().rstrip("/") + "/api/shopify/callback"
    params = urlencode({"client_id": settings.shopify_api_key, "scope": SCOPES, "redirect_uri": callback, "state": state})
    return {"authorization_url": f"https://{shop}/admin/oauth/authorize?{params}"}


@router.get("/api/shopify/callback")
def shopify_callback(request: Request, db: Session = Depends(get_db)):
    params = list(request.query_params.multi_items())
    if not verify_oauth_hmac(params):
        raise HTTPException(status_code=401, detail="Invalid Shopify authorization signature")
    query = dict(params)
    try:
        if abs(int(query.get("timestamp", "0")) - int(time.time())) > 600:
            raise HTTPException(status_code=400, detail="Shopify authorization callback is too old")
    except ValueError:
        raise HTTPException(status_code=400, detail="Shopify authorization callback timestamp is invalid")
    state_value = query.get("state")
    state = db.scalar(select(ShopifyOAuthState).where(ShopifyOAuthState.state == state_value).with_for_update()) if state_value else None
    if not state or state.expires_at <= datetime.now(timezone.utc):
        if state:
            db.delete(state)
            db.commit()
        raise HTTPException(status_code=400, detail="Shopify authorization has expired; start the connection again")
    active_owner = db.scalar(
        select(Membership.id)
        .join(User, User.id == Membership.user_id)
        .where(
            Membership.store_id == state.store_id,
            Membership.user_id == state.user_id,
            Membership.role == "owner",
            Membership.active.is_(True),
            User.active.is_(True),
        )
    )
    if not active_owner:
        db.delete(state)
        db.commit()
        raise HTTPException(status_code=403, detail="The owner who started authorization is no longer active")
    if query.get("error"):
        db.delete(state)
        db.commit()
        return RedirectResponse(settings.frontend_base_url + "/#/settings?shopify=denied", status_code=303)
    shop = validate_shop_domain(query.get("shop", ""))
    if shop != state.shop_domain:
        raise HTTPException(status_code=400, detail="Shopify returned a different store than the one authorized")
    store = db.scalar(select(Store).where(Store.id == state.store_id).with_for_update())
    if not store:
        raise HTTPException(status_code=400, detail="Store no longer exists")
    try:
        token = exchange_code(shop, query.get("code", ""))
    except HTTPException:
        db.delete(state)
        db.commit()
        raise
    granted_scopes = {scope.strip() for scope in str(token.get("scope", "")).split(",") if scope.strip()}
    required_scopes = set(SCOPES.split(","))
    if not required_scopes.issubset(granted_scopes):
        db.delete(state)
        db.commit()
        raise HTTPException(status_code=403, detail="Shopify did not grant all requested access scopes; start authorization again")
    if not token.get("refresh_token") or not token.get("expires_in"):
        db.delete(state)
        db.commit()
        raise HTTPException(status_code=502, detail="Shopify did not return a refreshable offline access token")
    now = int(time.time())
    bundle = {
        "access_token": token["access_token"],
        "refresh_token": token.get("refresh_token"),
        "expires_at": now + int(token.get("expires_in", 3600)) if token.get("expires_in") else 0,
        "refresh_token_expires_at": now + int(token.get("refresh_token_expires_in", 7776000)) if token.get("refresh_token") else 0,
    }
    credential = db.scalar(select(ShopifyCredential).where(ShopifyCredential.store_id == store.id).with_for_update())
    if not credential:
        credential = ShopifyCredential(store_id=store.id, encrypted_token_bundle=encrypt_bundle(bundle), scopes=token.get("scope", ""))
        db.add(credential)
    else:
        credential.encrypted_token_bundle = encrypt_bundle(bundle)
        credential.scopes = token.get("scope", credential.scopes)
    store.shopify_domain = shop
    db.flush()
    store.shopify_token_ref = str(credential.id)
    store.connection_status = "active"
    db.delete(state)
    db.commit()
    db.refresh(credential)
    registered, webhook_error = register_webhooks(db, store, credential)
    credential.webhooks_registered = registered
    credential.webhook_error = webhook_error
    db.commit()
    return RedirectResponse(settings.frontend_base_url + "/#/settings?shopify=connected", status_code=303)


@router.delete("/api/shopify/connect", status_code=204)
def shopify_disconnect(principal: Principal = Depends(require_owner), db: Session = Depends(get_db)):
    store = db.scalar(select(Store).where(Store.id == principal.membership.store_id).with_for_update())
    if store:
        db.query(ShopifyCredential).filter(ShopifyCredential.store_id == store.id).delete(synchronize_session=False)
        store.shopify_domain = None
        store.shopify_token_ref = None
        store.connection_status = "disconnected"
        store.last_synced_at = None
        db.commit()
    return Response(status_code=204)


@router.post("/api/shopify/sync")
def sync_shopify_orders(principal: Principal = Depends(require_owner), db: Session = Depends(get_db), limit: int = 250, after: str | None = None):
    if limit < 1 or limit > 250:
        raise HTTPException(status_code=422, detail="Sync limit must be between 1 and 250 orders")
    store = principal.membership.store
    if not store.shopify_domain:
        raise HTTPException(status_code=409, detail="Connect Shopify before syncing orders")
    result = fetch_and_store_orders(db, store, limit, after)
    return {**result, "last_synced_at": store.last_synced_at}


@router.get("/api/orders")
def list_store_orders(principal: Principal = Depends(current_principal), db: Session = Depends(get_db), limit: int = 250):
    if limit < 1 or limit > 500:
        raise HTTPException(status_code=422, detail="Limit must be between 1 and 500")
    if not principal.membership.store.shopify_domain:
        return []
    rows = db.execute(
        select(Order, Customer)
        .outerjoin(Customer, (Customer.id == Order.customer_id) & (Customer.store_id == Order.store_id))
        .where(Order.store_id == principal.membership.store_id)
        .order_by(Order.ordered_at.desc())
        .limit(limit)
    ).all()
    return [order_output(order, customer) for order, customer in rows]


@router.post("/api/webhooks/shopify", status_code=200)
async def receive_shopify_webhook(request: Request, db: Session = Depends(get_db)):
    raw = await request.body()
    if not verify_webhook_hmac(raw, request.headers.get("x-shopify-hmac-sha256")):
        raise HTTPException(status_code=401, detail="Invalid Shopify webhook signature")
    shop = validate_shop_domain(request.headers.get("x-shopify-shop-domain", ""))
    topic = request.headers.get("x-shopify-topic", "").lower()
    webhook_id = request.headers.get("x-shopify-webhook-id")
    if not topic or not webhook_id:
        raise HTTPException(status_code=400, detail="Shopify webhook metadata is missing")
    store = db.scalar(select(Store).where(Store.shopify_domain == shop))
    if not store:
        # Shopify retries may arrive after a merchant disconnects; acknowledge unknown shops.
        return {"received": True, "ignored": True}
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise HTTPException(status_code=400, detail="Invalid Shopify webhook JSON")
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Invalid Shopify webhook payload")
    if topic == "app/uninstalled":
        db.query(ShopifyCredential).filter(ShopifyCredential.store_id == store.id).delete(synchronize_session=False)
        store.shopify_domain = None
        store.shopify_token_ref = None
        store.connection_status = "disconnected"
        db.commit()
        return {"received": True, "uninstalled": True}
    inserted = process_webhook(db, store, topic, webhook_id, payload)
    return {"received": True, "duplicate": not inserted}
