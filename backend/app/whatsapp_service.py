"""WhatsApp Cloud API (Meta) integration: webhook intake, delivery statuses, and the send queue.

Flow (Stage 3):
  incoming:  Meta webhook -> verify X-Hub-Signature-256 -> save source event + message in one
             transaction (deduplicated by Meta's message ID) -> return 200.
  outgoing:  staff reply -> messages row (queued) + message_outbox row -> worker sends through
             the Graph API -> provider ID saved (submitted) -> status webhooks move it to
             delivered / read / failed.
"""
import hashlib
import hmac
import json
import logging
import re
import threading
import uuid
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from cryptography.fernet import Fernet, InvalidToken
from fastapi import HTTPException
from sqlalchemy import or_, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.config import settings
from app.database import SessionLocal
from app.models import AuditEntry, ComplaintCase, Customer, Message, MessageOutbox, SourceEvent, Store, WhatsAppCredential

logger = logging.getLogger(__name__)

PHONE_NUMBER_ID_RE = re.compile(r"^\d{5,30}$")
MAX_TEXT_LENGTH = 4096
MAX_ATTEMPTS = 5
RETRY_DELAYS = [timedelta(seconds=10), timedelta(seconds=30), timedelta(minutes=2), timedelta(minutes=10)]
LOCK_SECONDS = 60

# Meta "status" values -> delivery_status_t. Higher rank wins; statuses can arrive out of order.
STATUS_MAP = {"sent": "submitted", "delivered": "delivered", "read": "read", "failed": "failed"}
STATUS_RANK = {"received": 0, "queued": 0, "submitted": 1, "failed": 2, "delivered": 3, "read": 4}
SUBSCRIBE_WARNING = "Connected, but subscribing the app to the business account failed"
STATUS_LABEL = {"queued": "Queued", "submitted": "Sent", "delivered": "Delivered", "read": "Read", "failed": "Failed"}

# Graph API error codes worth retrying (throttling / temporary service problems).
RETRYABLE_CODES = {1, 2, 4, 17, 341, 80007, 130429, 131000, 131016, 131048, 131056, 133004}
FRIENDLY_ERRORS = {
    131047: "More than 24 hours have passed since the customer's last message. WhatsApp only allows template messages outside the 24-hour customer service window.",
    131030: "This number is not in the test number's allowed recipient list. Add it under WhatsApp > API Setup in the Meta app dashboard.",
    131026: "WhatsApp could not deliver to this number. The customer may not use WhatsApp or may need to update the app.",
    190: "The WhatsApp access token is invalid or expired. Reconnect WhatsApp in Settings with a new token.",
    10: "The WhatsApp access token does not have permission for this phone number.",
    100: "WhatsApp rejected the request parameters.",
}


class WhatsAppError(Exception):
    def __init__(self, message: str, *, code: int | None = None, retryable: bool = False):
        super().__init__(message)
        self.message = message
        self.code = code
        self.retryable = retryable


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Credentials
# ---------------------------------------------------------------------------

def _fernet() -> Fernet:
    key = settings.credential_encryption_key
    if not key:
        raise HTTPException(status_code=503, detail="Credential encryption is not configured on the server (set SHOPIFY_ENCRYPTION_KEY or WHATSAPP_ENCRYPTION_KEY)")
    try:
        return Fernet(key.encode("ascii"))
    except (ValueError, UnicodeEncodeError):
        raise HTTPException(status_code=503, detail="Credential encryption key is invalid")


def encrypt_token(token: str) -> str:
    return _fernet().encrypt(token.encode()).decode("ascii")


def decrypt_token(value: str) -> str:
    try:
        return _fernet().decrypt(value.encode("ascii")).decode()
    except (InvalidToken, ValueError, UnicodeEncodeError):
        raise WhatsAppError("Stored WhatsApp credentials cannot be decrypted; reconnect WhatsApp in Settings.")


def validate_phone_number_id(value: str) -> str:
    value = value.strip()
    if not PHONE_NUMBER_ID_RE.fullmatch(value):
        raise HTTPException(status_code=422, detail="Enter the numeric Phone number ID from Meta's WhatsApp API Setup page (not the phone number itself)")
    return value


def verify_signature(raw_body: bytes, header: str | None) -> bool:
    if not header or not settings.whatsapp_app_secret or not header.startswith("sha256="):
        return False
    digest = hmac.new(settings.whatsapp_app_secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(digest, header.removeprefix("sha256="))


def verify_subscription(mode: str | None, token: str | None) -> bool:
    if mode != "subscribe" or not token or not settings.whatsapp_verify_token:
        return False
    return hmac.compare_digest(token.encode(), settings.whatsapp_verify_token.encode())


# ---------------------------------------------------------------------------
# Graph API client
# ---------------------------------------------------------------------------

def _graph(method: str, path: str, token: str, body: dict | None = None) -> dict:
    url = f"{settings.whatsapp_graph_base_url.rstrip('/')}/{settings.whatsapp_api_version}/{path.lstrip('/')}"
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Authorization": f"Bearer {token}"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    request = Request(url, data=data, headers=headers, method=method)
    try:
        with urlopen(request, timeout=20) as response:
            return json.loads(response.read() or b"{}")
    except HTTPError as exc:
        try:
            error = json.loads(exc.read()).get("error") or {}
        except (json.JSONDecodeError, ValueError, AttributeError):
            error = {}
        code = error.get("code")
        retryable = exc.code >= 500 or exc.code == 429 or code in RETRYABLE_CODES
        details = (error.get("error_data") or {}).get("details") if isinstance(error.get("error_data"), dict) else None
        message = FRIENDLY_ERRORS.get(code) or details or error.get("message") or f"WhatsApp returned HTTP {exc.code}"
        raise WhatsAppError(str(message)[:500], code=code, retryable=retryable)
    except (URLError, TimeoutError, OSError):
        raise WhatsAppError("Could not reach the WhatsApp Cloud API", retryable=True)
    except json.JSONDecodeError:
        raise WhatsAppError("WhatsApp returned an unreadable response", retryable=True)


def fetch_phone_number(phone_number_id: str, token: str) -> dict:
    return _graph("GET", f"{phone_number_id}?fields=display_phone_number,verified_name,quality_rating", token)


def subscribe_app_to_waba(waba_id: str, token: str) -> None:
    _graph("POST", f"{waba_id}/subscribed_apps", token, {})


def send_text(credential: WhatsAppCredential, to_phone: str, body: str) -> str:
    if len(body) > MAX_TEXT_LENGTH:
        raise WhatsAppError(f"WhatsApp text messages are limited to {MAX_TEXT_LENGTH} characters")
    to = re.sub(r"\D", "", to_phone)
    if not to:
        raise WhatsAppError("The customer has no valid WhatsApp number")
    result = _graph("POST", f"{credential.phone_number_id}/messages", decrypt_token(credential.encrypted_access_token), {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": "text",
        "text": {"preview_url": False, "body": body},
    })
    wamid = ((result.get("messages") or [{}])[0] or {}).get("id")
    if not wamid:
        raise WhatsAppError("WhatsApp accepted the request but returned no message ID", retryable=True)
    return wamid


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _audit(db: Session, store_id, case_id, action: str, summary: dict, actor_user_id=None) -> None:
    db.add(AuditEntry(
        id=uuid.uuid4(), store_id=store_id, actor_user_id=actor_user_id, entity_type="case",
        entity_id=case_id, action=action, change_summary=summary, occurred_at=utcnow(),
    ))


def _timestamp(value) -> datetime:
    try:
        return datetime.fromtimestamp(int(value), tz=timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError):
        return utcnow()


def _save_event(db: Session, store_id, provider_event_id: str, event_type: str, payload: dict, occurred: datetime, status: str = "pending"):
    """Insert a source event; returns its ID, or None when Meta re-delivered the same event."""
    statement = insert(SourceEvent).values(
        store_id=store_id, provider_event_id=provider_event_id[:200], source="whatsapp", event_type=event_type,
        payload=payload, occurred_at=occurred, processing_status=status, attempts=0,
    ).on_conflict_do_nothing(constraint="source_events_store_id_source_provider_event_id_key").returning(SourceEvent.id)
    return db.scalar(statement)


def message_text(message: dict) -> str | None:
    """Readable text for a WhatsApp message; None for types that should not open a case."""
    kind = message.get("type")
    if kind == "text":
        return (message.get("text") or {}).get("body") or ""
    if kind == "reaction":
        return None
    if kind in {"image", "video", "document", "audio", "sticker", "voice"}:
        media = message.get(kind) or {}
        caption = media.get("caption") or media.get("filename") or ""
        return f"[{kind} received]" + (f" {caption}" if caption else "")
    if kind == "location":
        loc = message.get("location") or {}
        label = ", ".join(filter(None, [loc.get("name"), loc.get("address")]))
        return f"[location] {label or ''} ({loc.get('latitude')}, {loc.get('longitude')})".strip()
    if kind == "button":
        return (message.get("button") or {}).get("text") or "[button reply]"
    if kind == "interactive":
        inter = message.get("interactive") or {}
        reply = inter.get("button_reply") or inter.get("list_reply") or {}
        return reply.get("title") or "[interactive reply]"
    if kind == "contacts":
        return "[contact card received]"
    return f"[{kind or 'unsupported'} message]"


# ---------------------------------------------------------------------------
# Incoming webhook processing
# ---------------------------------------------------------------------------

def process_webhook_payload(payload: dict) -> dict:
    """Persist everything in the payload, then commit. The caller acknowledges Meta only afterwards."""
    summary = {"messages": 0, "duplicates": 0, "statuses": 0, "ignored": 0}
    with SessionLocal() as db:
        for entry in payload.get("entry") or []:
            for change in (entry or {}).get("changes") or []:
                if (change or {}).get("field") != "messages":
                    summary["ignored"] += 1
                    continue
                value = change.get("value") or {}
                phone_number_id = str((value.get("metadata") or {}).get("phone_number_id") or "")
                credential = db.scalar(select(WhatsAppCredential).where(WhatsAppCredential.phone_number_id == phone_number_id)) if phone_number_id else None
                if not credential:
                    # Not connected to any store (or disconnected); acknowledge so Meta stops retrying.
                    summary["ignored"] += 1
                    continue
                credential.last_webhook_at = utcnow()
                if credential.last_error and credential.last_error.startswith(SUBSCRIBE_WARNING):
                    credential.last_error = None  # a delivered webhook proves the subscription works
                store = db.get(Store, credential.store_id)
                contacts = {str(c.get("wa_id")): ((c.get("profile") or {}).get("name")) for c in value.get("contacts") or [] if isinstance(c, dict)}
                for message in value.get("messages") or []:
                    with db.begin_nested():
                        if handle_incoming_message(db, store, message, contacts):
                            summary["messages"] += 1
                        else:
                            summary["duplicates"] += 1
                for status in value.get("statuses") or []:
                    with db.begin_nested():
                        handle_status(db, store, status)
                        summary["statuses"] += 1
        db.commit()
    return summary


def handle_incoming_message(db: Session, store: Store, message: dict, contacts: dict) -> bool:
    wamid = str(message.get("id") or "")
    wa_id = re.sub(r"\D", "", str(message.get("from") or ""))
    if not wamid or not wa_id:
        return False
    occurred = _timestamp(message.get("timestamp"))
    event_id = _save_event(db, store.id, wamid, "message", {"from": wa_id, "type": message.get("type"), "timestamp": message.get("timestamp")}, occurred)
    if event_id is None:
        return False  # Meta re-delivered a message we already stored.
    body = message_text(message)
    if body is None:
        db.execute(text("UPDATE source_events SET processing_status='processed', attempts=1 WHERE id=:id"), {"id": event_id})
        return True
    phone = "+" + wa_id
    # Serialize intake per customer so two quick messages cannot open two cases.
    db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": f"wa:{store.id}:{phone}"})
    customer = db.scalar(
        select(Customer)
        .where(Customer.store_id == store.id, Customer.phone.in_([phone, wa_id]))
        .order_by(Customer.shopify_customer_id.is_(None))  # prefer the Shopify-linked record
        .limit(1)
    )
    profile_name = (contacts.get(wa_id) or "").strip()[:200] or None
    if not customer:
        customer = Customer(id=uuid.uuid4(), store_id=store.id, phone=phone, name=profile_name or phone)
        db.add(customer)
        db.flush()
    elif not customer.name and profile_name:
        customer.name = profile_name

    case = db.execute(text("""
        SELECT id, status FROM complaint_cases
        WHERE store_id = :store_id AND customer_id = :customer_id AND status <> 'resolved'
        ORDER BY created_at DESC LIMIT 1 FOR UPDATE
    """), {"store_id": store.id, "customer_id": customer.id}).first()
    if case:
        case_id = case.id
        if case.status == "awaiting_customer":
            db.execute(text("UPDATE complaint_cases SET status='in_progress'::case_status_t WHERE id=:id"), {"id": case_id})
            _audit(db, store.id, case_id, "status_changed", {"from": "Awaiting customer", "to": "In progress", "reason": "Customer replied on WhatsApp"})
    else:
        case_id = uuid.uuid4()
        db.execute(text("""
            INSERT INTO complaint_cases (id, store_id, customer_id, status, route, routing_reason, human_takeover, created_at)
            VALUES (:id, :store_id, :customer_id, 'open', 'human_review', 'WhatsApp intake: awaiting staff review', false, :created_at)
        """), {"id": case_id, "store_id": store.id, "customer_id": customer.id, "created_at": occurred})
        _audit(db, store.id, case_id, "case_created", {"source": "whatsapp", "from": phone})

    db.add(Message(
        id=uuid.uuid4(), case_id=case_id, provider_message_id=wamid, direction="incoming",
        body=body, sent_at=occurred, delivery_status="received",
    ))
    db.execute(text("UPDATE source_events SET processing_status='processed', attempts=1 WHERE id=:id"), {"id": event_id})
    return True


def handle_status(db: Session, store: Store, status: dict) -> None:
    wamid = str(status.get("id") or "")
    raw_status = str(status.get("status") or "")
    new_status = STATUS_MAP.get(raw_status)
    if not wamid or not new_status:
        return
    errors = [{"code": e.get("code"), "title": e.get("title"), "details": (e.get("error_data") or {}).get("details")} for e in status.get("errors") or [] if isinstance(e, dict)]
    payload = {"message_id": wamid, "status": raw_status, "timestamp": status.get("timestamp"), "errors": errors}
    event_id = _save_event(db, store.id, f"status:{wamid}:{raw_status}", "status", payload, _timestamp(status.get("timestamp")))
    if event_id is None:
        return
    message = _store_message(db, store.id, wamid)
    if not message:
        # The send call may not have saved the provider ID yet; applied later by apply_pending_statuses.
        return
    apply_status(db, store.id, message, new_status, errors)
    db.execute(text("UPDATE source_events SET processing_status='processed', attempts=1 WHERE id=:id"), {"id": event_id})


def _store_message(db: Session, store_id, wamid: str) -> Message | None:
    return db.scalar(
        select(Message).join(ComplaintCase, ComplaintCase.id == Message.case_id)
        .where(Message.provider_message_id == wamid, ComplaintCase.store_id == store_id, Message.direction == "outgoing")
    )


def _error_text(errors: list[dict]) -> str:
    if not errors:
        return "WhatsApp reported the message as failed"
    first = errors[0]
    return FRIENDLY_ERRORS.get(first.get("code")) or first.get("details") or first.get("title") or f"WhatsApp error {first.get('code')}"


def apply_status(db: Session, store_id, message: Message, new_status: str, errors: list[dict] | None = None) -> bool:
    if STATUS_RANK.get(new_status, 0) <= STATUS_RANK.get(message.delivery_status, 0):
        return False
    previous = message.delivery_status
    message.delivery_status = new_status
    summary = {"from": STATUS_LABEL.get(previous, previous), "to": STATUS_LABEL.get(new_status, new_status)}
    if new_status == "failed":
        reason = _error_text(errors or [])
        summary["error"] = reason
        job = db.scalar(select(MessageOutbox).where(MessageOutbox.message_id == message.id))
        if job:
            job.last_error = reason
    _audit(db, store_id, message.case_id, "delivery_failed" if new_status == "failed" else "delivery_status_changed", summary)
    return True


def apply_pending_statuses(db: Session, store_id, wamid: str) -> None:
    message = _store_message(db, store_id, wamid)
    if not message:
        return
    rows = db.execute(text("""
        SELECT id, payload FROM source_events
        WHERE store_id = :store_id AND source = 'whatsapp' AND event_type = 'status'
          AND processing_status = 'pending' AND payload->>'message_id' = :wamid
        ORDER BY occurred_at
    """), {"store_id": store_id, "wamid": wamid}).all()
    for row in rows:
        new_status = STATUS_MAP.get(row.payload.get("status"))
        if new_status:
            apply_status(db, store_id, message, new_status, row.payload.get("errors"))
        db.execute(text("UPDATE source_events SET processing_status='processed', attempts=attempts+1 WHERE id=:id"), {"id": row.id})


# ---------------------------------------------------------------------------
# Outgoing queue
# ---------------------------------------------------------------------------

def enqueue(db: Session, store_id, message_id) -> None:
    db.add(MessageOutbox(id=uuid.uuid4(), message_id=message_id, store_id=store_id, channel="whatsapp", next_attempt_at=utcnow()))


def process_outbox(limit: int = 10) -> int:
    """Claim due jobs (safe across threads/processes) and send them. Returns the number claimed."""
    now = utcnow()
    with SessionLocal() as db:
        jobs = db.scalars(
            select(MessageOutbox)
            .where(
                MessageOutbox.completed_at.is_(None),
                MessageOutbox.next_attempt_at <= now,
                or_(MessageOutbox.locked_until.is_(None), MessageOutbox.locked_until < now),
            )
            .order_by(MessageOutbox.next_attempt_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        ).all()
        job_ids = [job.id for job in jobs]
        for job in jobs:
            job.locked_until = now + timedelta(seconds=LOCK_SECONDS)
            job.attempts += 1
        db.commit()
    for job_id in job_ids:
        try:
            deliver(job_id)
        except Exception:  # never let one job stop the worker
            logger.exception("WhatsApp delivery crashed for outbox job %s", job_id)
    return len(job_ids)


def deliver(job_id) -> None:
    with SessionLocal() as db:
        job = db.get(MessageOutbox, job_id)
        if not job or job.completed_at:
            return
        message = db.get(Message, job.message_id)
        if not message or message.direction != "outgoing" or message.provider_message_id:
            job.completed_at = utcnow()
            job.locked_until = None
            db.commit()
            return
        phone = db.execute(text("""
            SELECT cu.phone FROM complaint_cases c
            LEFT JOIN customers cu ON cu.id = c.customer_id AND cu.store_id = c.store_id
            WHERE c.id = :case_id AND c.store_id = :store_id
        """), {"case_id": message.case_id, "store_id": job.store_id}).scalar()
        credential = db.scalar(select(WhatsAppCredential).where(WhatsAppCredential.store_id == job.store_id))
        try:
            if not credential:
                raise WhatsAppError("WhatsApp is not connected for this store. Connect it in Settings, then retry.")
            if not phone:
                raise WhatsAppError("The customer has no WhatsApp number on record")
            wamid = send_text(credential, phone, message.body)
        except WhatsAppError as err:
            _record_failure(db, job, message, credential, err)
            db.commit()
            return
        except Exception as exc:  # unexpected bug or library error: retry rather than lose the reply
            logger.exception("Unexpected WhatsApp send error")
            _record_failure(db, job, message, credential, WhatsAppError(f"Unexpected error: {type(exc).__name__}", retryable=True))
            db.commit()
            return
        message.provider_message_id = wamid
        apply_status(db, job.store_id, message, "submitted")
        job.completed_at = utcnow()
        job.locked_until = None
        job.last_error = None
        if credential.last_error:
            credential.last_error = None
        db.commit()
        apply_pending_statuses(db, job.store_id, wamid)
        db.commit()


def _record_failure(db: Session, job: MessageOutbox, message: Message, credential: WhatsAppCredential | None, err: WhatsAppError) -> None:
    job.last_error = err.message
    job.locked_until = None
    if credential and err.code in {190, 10}:
        credential.last_error = err.message
    if err.retryable and job.attempts < MAX_ATTEMPTS:
        job.next_attempt_at = utcnow() + RETRY_DELAYS[min(job.attempts - 1, len(RETRY_DELAYS) - 1)]
        logger.warning("WhatsApp send attempt %s failed, will retry: %s", job.attempts, err.message)
        return
    job.completed_at = utcnow()
    apply_status(db, job.store_id, message, "failed", [{"details": err.message}])


def queue_counts(db: Session, store_id) -> dict:
    row = db.execute(text("""
        SELECT COUNT(*) FILTER (WHERE ob.completed_at IS NULL) AS pending,
               COUNT(*) FILTER (WHERE m.delivery_status = 'failed') AS failed
        FROM message_outbox ob JOIN messages m ON m.id = ob.message_id
        WHERE ob.store_id = :store_id
    """), {"store_id": store_id}).first()
    return {"pending": row.pending or 0, "failed": row.failed or 0}


# ---------------------------------------------------------------------------
# Background worker
# ---------------------------------------------------------------------------

_stop = threading.Event()
_thread: threading.Thread | None = None


def _worker_loop() -> None:
    logger.info("WhatsApp outbox worker started")
    while not _stop.wait(settings.whatsapp_worker_interval_seconds):
        try:
            while process_outbox() and not _stop.is_set():
                pass
        except Exception:
            logger.exception("WhatsApp outbox worker iteration failed")


def start_worker() -> None:
    global _thread
    if not settings.whatsapp_worker_enabled or (_thread and _thread.is_alive()):
        return
    _stop.clear()
    _thread = threading.Thread(target=_worker_loop, name="whatsapp-outbox", daemon=True)
    _thread.start()


def stop_worker() -> None:
    _stop.set()
    if _thread:
        _thread.join(timeout=5)


if __name__ == "__main__":
    # Optional standalone worker: set WHATSAPP_WORKER_ENABLED=false for the API and run
    #   python -m app.whatsapp_service
    logging.basicConfig(level=logging.INFO)
    settings.whatsapp_worker_enabled = True
    start_worker()
    try:
        while _thread and _thread.is_alive():
            _thread.join(1)
    except KeyboardInterrupt:
        stop_worker()
