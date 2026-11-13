import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.dependencies import Principal, current_principal, require_owner
from app.models import Store, WhatsAppCredential
from app.whatsapp_service import (
    SUBSCRIBE_WARNING,
    WhatsAppError,
    encrypt_token,
    fetch_phone_number,
    process_webhook_payload,
    queue_counts,
    subscribe_app_to_waba,
    validate_phone_number_id,
    verify_signature,
    verify_subscription,
)

logger = logging.getLogger(__name__)
router = APIRouter()


class WhatsAppConnectRequest(BaseModel):
    phone_number_id: str = Field(min_length=5, max_length=30)
    access_token: str = Field(min_length=20, max_length=1000)
    waba_id: str | None = Field(default=None, max_length=30)


def _status(db: Session, store_id) -> dict:
    credential = db.scalar(select(WhatsAppCredential).where(WhatsAppCredential.store_id == store_id))
    base = settings.public_base_url
    return {
        "connected": credential is not None,
        "phone_number_id": credential.phone_number_id if credential else None,
        "waba_id": credential.waba_id if credential else None,
        "display_phone_number": credential.display_phone_number if credential else None,
        "verified_name": credential.verified_name if credential else None,
        "last_error": credential.last_error if credential else None,
        "last_webhook_at": credential.last_webhook_at if credential else None,
        "webhook_url": f"{base}/api/webhooks/whatsapp" if base else None,
        "app_secret_configured": bool(settings.whatsapp_app_secret),
        "verify_token_configured": bool(settings.whatsapp_verify_token),
        "worker_enabled": settings.whatsapp_worker_enabled,
        "queue": queue_counts(db, store_id),
    }


@router.get("/api/whatsapp/status")
def whatsapp_status(principal: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    return _status(db, principal.membership.store_id)


@router.post("/api/whatsapp/connect")
def whatsapp_connect(body: WhatsAppConnectRequest, principal: Principal = Depends(require_owner), db: Session = Depends(get_db)):
    phone_number_id = validate_phone_number_id(body.phone_number_id)
    waba_id = (body.waba_id or "").strip() or None
    if waba_id and not waba_id.isdigit():
        raise HTTPException(status_code=422, detail="WhatsApp Business Account ID must be numeric")
    token = body.access_token.strip()
    encrypted = encrypt_token(token)  # fails early if encryption is not configured
    try:
        info = fetch_phone_number(phone_number_id, token)
    except WhatsAppError as err:
        raise HTTPException(status_code=400, detail=f"Meta rejected these credentials: {err.message}")
    store = db.scalar(select(Store).where(Store.id == principal.membership.store_id).with_for_update())
    if not store:
        raise HTTPException(status_code=404, detail="Store not found")
    other = db.scalar(select(WhatsAppCredential.id).where(WhatsAppCredential.phone_number_id == phone_number_id, WhatsAppCredential.store_id != store.id))
    if other:
        raise HTTPException(status_code=409, detail="This WhatsApp number is already connected to another Resolve AI store")
    credential = db.scalar(select(WhatsAppCredential).where(WhatsAppCredential.store_id == store.id).with_for_update())
    if not credential:
        credential = WhatsAppCredential(store_id=store.id, phone_number_id=phone_number_id, encrypted_access_token=encrypted)
        db.add(credential)
    credential.phone_number_id = phone_number_id
    credential.encrypted_access_token = encrypted
    credential.waba_id = waba_id
    credential.display_phone_number = str(info.get("display_phone_number") or "")[:50] or None
    credential.verified_name = str(info.get("verified_name") or "")[:200] or None
    credential.last_error = None
    db.flush()
    store.whatsapp_number_id = phone_number_id
    store.whatsapp_token_ref = str(credential.id)
    warning = None
    if waba_id:
        for attempt in range(2):  # one quick retry for temporary network hiccups
            try:
                subscribe_app_to_waba(waba_id, token)
                break
            except WhatsAppError as err:
                if attempt == 0 and err.retryable:
                    continue
                warning = f"{SUBSCRIBE_WARNING}: {err.message}. If customer messages still arrive, you can ignore this."
                credential.last_error = warning
                break
    db.commit()
    return {**_status(db, store.id), "warning": warning}


@router.delete("/api/whatsapp/connect", status_code=204)
def whatsapp_disconnect(principal: Principal = Depends(require_owner), db: Session = Depends(get_db)):
    store = db.scalar(select(Store).where(Store.id == principal.membership.store_id).with_for_update())
    if store:
        db.query(WhatsAppCredential).filter(WhatsAppCredential.store_id == store.id).delete(synchronize_session=False)
        store.whatsapp_number_id = None
        store.whatsapp_token_ref = None
        db.commit()
    return Response(status_code=204)


@router.get("/api/webhooks/whatsapp")
def whatsapp_verify(request: Request):
    """Meta calls this once when you save the webhook URL in the app dashboard."""
    params = request.query_params
    if verify_subscription(params.get("hub.mode"), params.get("hub.verify_token")):
        return PlainTextResponse(params.get("hub.challenge", ""))
    raise HTTPException(status_code=403, detail="Webhook verification failed")


@router.post("/api/webhooks/whatsapp")
async def whatsapp_webhook(request: Request):
    raw = await request.body()
    if not verify_signature(raw, request.headers.get("x-hub-signature-256")):
        raise HTTPException(status_code=401, detail="Invalid WhatsApp webhook signature")
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise HTTPException(status_code=400, detail="Invalid WhatsApp webhook JSON")
    if not isinstance(payload, dict) or payload.get("object") != "whatsapp_business_account":
        return {"received": True, "ignored": True}
    # Saved and committed before the 200 response; if this raises, Meta retries and duplicates are skipped.
    summary = await run_in_threadpool(process_webhook_payload, payload)
    return {"received": True, **summary}
