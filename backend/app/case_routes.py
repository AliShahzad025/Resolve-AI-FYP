from datetime import datetime, timezone
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import Principal, current_principal
from app.models import AuditEntry, ComplaintCase, Message
from app.schemas import AuditOut, CaseCreate, CaseMessageOut, CaseOut, CaseUpdate, MessageCreate

router = APIRouter()

STATUS_TO_DB = {
    "Open": "open",
    "In progress": "in_progress",
    "Awaiting customer": "awaiting_customer",
    "Escalated": "escalated",
    "Resolved": "resolved",
}
DB_TO_STATUS = {value: key for key, value in STATUS_TO_DB.items()}
ALLOWED_TRANSITIONS = {
    "open": {"in_progress", "escalated"},
    "in_progress": {"awaiting_customer", "escalated", "resolved"},
    "awaiting_customer": {"in_progress", "escalated"},
    "escalated": {"in_progress", "resolved"},
    "resolved": {"open"},
}


def _is_manager_or_owner(principal: Principal) -> bool:
    return principal.membership.role in {"owner", "manager"}


def _can_manage_case(principal: Principal, row) -> bool:
    return _is_manager_or_owner(principal) or row["assigned_user_id"] == principal.user.id


def _require_case_access(principal: Principal, row):
    if not _can_manage_case(principal, row):
        raise HTTPException(status_code=403, detail="This case is not assigned to you")


def _case_row(db: Session, case_id: uuid.UUID, store_id: uuid.UUID):
    row = db.execute(text("""
        SELECT c.*, COALESCE(cu.name, 'Unknown customer') AS customer_name,
               cu.email, COALESCE(o.shopify_order_id, '') AS order_ref,
               COALESCE(au.name, 'Unassigned') AS assignee,
               COALESCE(a.category, 'General inquiry') AS category,
               COALESCE(a.urgency::text, 'medium') AS urgency,
               COALESCE(a.sentiment::text, 'neutral') AS sentiment,
               COALESCE((a.confidence->>'category')::numeric, 0.0) AS confidence,
               COALESCE(last_incoming.body, '') AS latest_message
        FROM complaint_cases c
        LEFT JOIN customers cu ON cu.id = c.customer_id AND cu.store_id = c.store_id
        LEFT JOIN orders o ON o.id = c.order_id AND o.store_id = c.store_id
        LEFT JOIN users au ON au.id = c.assigned_user_id
        LEFT JOIN LATERAL (
          SELECT ca.category, ca.urgency, ca.sentiment, ca.confidence
          FROM complaint_analyses ca
          JOIN messages m ON m.id = ca.message_id
          WHERE m.case_id = c.id
          ORDER BY ca.analysed_at DESC LIMIT 1
        ) a ON TRUE
        LEFT JOIN LATERAL (
          SELECT m.body FROM messages m
          WHERE m.case_id = c.id AND m.direction = 'incoming'
          ORDER BY m.sent_at DESC LIMIT 1
        ) last_incoming ON TRUE
        WHERE c.id = :case_id AND c.store_id = :store_id
    """), {"case_id": case_id, "store_id": store_id}).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Case not found")
    return row


def _messages(db: Session, case_id: uuid.UUID) -> list[CaseMessageOut]:
    rows = db.execute(text("""
        SELECT m.*, u.name AS sender_name
        FROM messages m LEFT JOIN users u ON u.id = m.sender_user_id
        WHERE m.case_id = :case_id ORDER BY m.sent_at
    """), {"case_id": case_id}).mappings()
    return [CaseMessageOut(
        id=row["id"], body=row["body"], incoming=row["direction"] == "incoming",
        sender_name=row["sender_name"], sent_at=row["sent_at"].isoformat(),
        delivery_status=row["delivery_status"],
    ) for row in rows]


def _audit(db: Session, case_id: uuid.UUID) -> list[AuditOut]:
    rows = db.execute(text("""
        SELECT a.*, u.name AS actor_name
        FROM audit_entries a LEFT JOIN users u ON u.id = a.actor_user_id
        WHERE a.entity_type = 'case' AND a.entity_id = :case_id
        ORDER BY a.occurred_at
    """), {"case_id": case_id}).mappings()
    return [AuditOut(
        id=row["id"], action=row["action"], change_summary=row["change_summary"],
        actor_name=row["actor_name"], occurred_at=row["occurred_at"].isoformat(),
    ) for row in rows]


def _out(db: Session, row) -> CaseOut:
    last_activity = db.execute(text("""
        SELECT GREATEST(
            COALESCE((SELECT MAX(sent_at) FROM messages WHERE case_id = :case_id), :created_at),
            COALESCE((SELECT MAX(occurred_at) FROM audit_entries WHERE entity_type = 'case' AND entity_id = :case_id), :created_at)
        )
    """), {"case_id": row["id"], "created_at": row["created_at"]}).scalar()
    return CaseOut(
        id=row["id"], case_ref=row["case_ref"], customer=row["customer_name"],
        email=row["email"], subject=row["latest_message"][:80] or "Customer support case",
        preview=row["latest_message"], category=row["category"].replace("_", " ").title(),
        urgency=row["urgency"].title(), sentiment=row["sentiment"].title(),
        confidence=round(float(row["confidence"]) * 100), status=DB_TO_STATUS.get(row["status"], row["status"]),
        assigned=row["assignee"], assigned_user_id=row["assigned_user_id"],
        order_id=row["order_ref"] or None, language="English", updated=last_activity.isoformat(),
        unread=False, takeover=row["human_takeover"], messages=_messages(db, row["id"]),
        audit=_audit(db, row["id"]),
    )


def _record(db: Session, principal: Principal, case_id: uuid.UUID, action: str, summary: dict):
    db.add(AuditEntry(
        id=uuid.uuid4(), store_id=principal.membership.store_id, actor_user_id=principal.user.id,
        entity_type="case", entity_id=case_id, action=action, change_summary=summary,
        occurred_at=datetime.now(timezone.utc),
    ))


@router.get("/api/cases", response_model=list[CaseOut])
def list_cases(principal: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    query = """
        SELECT c.id FROM complaint_cases c
        WHERE c.store_id = :store_id
    """
    params = {"store_id": principal.membership.store_id}
    if principal.membership.role == "agent":
        query += " AND c.assigned_user_id = :user_id"
        params["user_id"] = principal.user.id
    query += " ORDER BY c.created_at DESC"
    rows = db.execute(text(query), params)
    return [_out(db, _case_row(db, row.id, principal.membership.store_id)) for row in rows]


@router.post("/api/cases", response_model=CaseOut, status_code=201)
def create_case(body: CaseCreate, principal: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    if not _is_manager_or_owner(principal):
        raise HTTPException(status_code=403, detail="Only an owner or manager can create a case")
    store_id = principal.membership.store_id
    customer_id = uuid.uuid4()
    case_id = uuid.uuid4()
    message_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    db.execute(text("""
        INSERT INTO customers (id, store_id, name, email, messaging_consent)
        VALUES (:id, :store_id, :name, :email, false)
    """), {"id": customer_id, "store_id": store_id, "name": body.customer_name.strip(), "email": body.customer_email})
    db.execute(text("""
        INSERT INTO complaint_cases
          (id, store_id, customer_id, status, route, routing_reason, human_takeover, created_at)
        VALUES (:id, :store_id, :customer_id, 'open', 'human_review', 'Created manually for case handling demo', false, :created_at)
    """), {"id": case_id, "store_id": store_id, "customer_id": customer_id, "created_at": now})
    db.execute(text("""
        INSERT INTO messages
          (id, case_id, direction, body, sent_at, delivery_status)
        VALUES (:id, :case_id, CAST('incoming' AS message_direction_t), :body, :sent_at,
                CAST('received' AS delivery_status_t))
    """), {"id": message_id, "case_id": case_id, "body": body.body.strip(), "sent_at": now})
    urgency = body.urgency.lower()
    db.execute(text("""
        INSERT INTO complaint_analyses
          (id, message_id, category, urgency, sentiment, confidence, model_versions, analysed_at)
        VALUES (:id, :message_id, :category, CAST(:urgency AS urgency_t), 'neutral', :confidence, '{}'::jsonb, :analysed_at)
    """), {
        "id": uuid.uuid4(), "message_id": message_id, "category": body.category.strip().lower().replace(" ", "_"),
        "urgency": urgency, "confidence": '{"category": 0.5, "urgency": 0.5}', "analysed_at": now,
    })
    db.add(AuditEntry(
        id=uuid.uuid4(), store_id=store_id, actor_user_id=principal.user.id,
        entity_type="case", entity_id=case_id, action="case_created",
        change_summary={"source": "manual_test", "category": body.category.strip()},
        occurred_at=now,
    ))
    db.commit()
    return _out(db, _case_row(db, case_id, store_id))


@router.get("/api/cases/{case_id}", response_model=CaseOut)
def get_case(case_id: uuid.UUID, principal: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    row = _case_row(db, case_id, principal.membership.store_id)
    _require_case_access(principal, row)
    return _out(db, row)


@router.patch("/api/cases/{case_id}", response_model=CaseOut)
def update_case(case_id: uuid.UUID, body: CaseUpdate, principal: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    row = _case_row(db, case_id, principal.membership.store_id)
    _require_case_access(principal, row)
    if "assigned_user_id" in body.model_fields_set and not _is_manager_or_owner(principal):
        raise HTTPException(status_code=403, detail="Only an owner or manager can assign a case")
    if body.status is not None:
        row_status = STATUS_TO_DB[body.status]
        if row_status != row["status"] and row_status not in ALLOWED_TRANSITIONS.get(row["status"], set()):
            raise HTTPException(
                status_code=409,
                detail=f"Invalid status transition from {DB_TO_STATUS.get(row['status'], row['status'])} to {body.status}",
            )
        if row_status != row["status"]:
            db.execute(text("UPDATE complaint_cases SET status = CAST(:status AS case_status_t) WHERE id = :id"), {"status": row_status, "id": case_id})
            _record(db, principal, case_id, "status_changed", {
                "from": DB_TO_STATUS.get(row["status"], row["status"]), "to": body.status,
            })
    if "assigned_user_id" in body.model_fields_set:
        if body.assigned_user_id is None:
            if row["assigned_user_id"] is not None:
                db.execute(text("UPDATE complaint_cases SET assigned_user_id = NULL WHERE id = :id"), {"id": case_id})
                _record(db, principal, case_id, "assigned", {"from": row["assignee"], "to": "Unassigned"})
        else:
            member = db.execute(text("""
              SELECT u.id AS user_id, u.name
              FROM users u
              JOIN memberships m ON m.user_id = u.id
              WHERE (u.id = :staff_id OR m.id = :staff_id)
                AND m.store_id = :store_id
                AND m.active = true
                AND u.active = true
            """), {"staff_id": body.assigned_user_id, "store_id": principal.membership.store_id}).first()
            if not member:
                raise HTTPException(status_code=400, detail="Assigned staff member is not active in this store")
            if row["assigned_user_id"] != member.user_id:
                db.execute(text("UPDATE complaint_cases SET assigned_user_id=:user_id WHERE id=:id"), {"user_id": member.user_id, "id": case_id})
                _record(db, principal, case_id, "assigned", {"from": row["assignee"], "to": member.name})
            if body.status is None and row["status"] == "open" and row["assigned_user_id"] != member.user_id:
                db.execute(
                    text("UPDATE complaint_cases SET status = 'in_progress'::case_status_t WHERE id = :id"),
                    {"id": case_id},
                )
                _record(db, principal, case_id, "status_changed", {"from": "Open", "to": "In progress"})
    if body.human_takeover is not None:
        if body.human_takeover != row["human_takeover"]:
            db.execute(text("UPDATE complaint_cases SET human_takeover=:value WHERE id=:id"), {"value": body.human_takeover, "id": case_id})
            _record(db, principal, case_id, "takeover_changed", {"enabled": body.human_takeover})
    db.commit()
    return _out(db, _case_row(db, case_id, principal.membership.store_id))


@router.post("/api/cases/{case_id}/messages", response_model=CaseOut, status_code=201)
def send_message(case_id: uuid.UUID, body: MessageCreate, principal: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    case = _case_row(db, case_id, principal.membership.store_id)
    _require_case_access(principal, case)
    if case["status"] == "resolved":
        raise HTTPException(status_code=409, detail="Reopen the case before sending a reply")
    message_body = body.body.strip()
    if not message_body:
        raise HTTPException(status_code=422, detail="Reply cannot be blank")
    now = datetime.now(timezone.utc)
    db.add(Message(
        id=uuid.uuid4(), case_id=case_id, sender_user_id=principal.user.id,
        direction="outgoing", body=message_body, sent_at=now, delivery_status="queued",
    ))
    _record(db, principal, case_id, "reply_sent", {"body": message_body, "delivery_status": "queued"})
    if case["status"] == "open":
        db.execute(
            text("UPDATE complaint_cases SET status = 'in_progress'::case_status_t WHERE id = :id"),
            {"id": case_id},
        )
        _record(db, principal, case_id, "status_changed", {"from": "Open", "to": "In progress"})
    db.commit()
    return _out(db, _case_row(db, case_id, principal.membership.store_id))
