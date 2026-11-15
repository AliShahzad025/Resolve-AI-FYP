"""Stage 5 policy library: versioned store policies, activation, indexing status and a retrieval test."""
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import rag
from app.database import get_db
from app.dependencies import Principal, current_principal
from app.starter_policies import STARTER_POLICIES

router = APIRouter()


class PolicySave(BaseModel):
    title: str = Field(min_length=2, max_length=200)
    content: str = Field(min_length=20, max_length=50000)


class PolicySearch(BaseModel):
    query: str = Field(min_length=2, max_length=1000)
    category: str | None = Field(default=None, max_length=100)


def require_editor(principal: Principal = Depends(current_principal)) -> Principal:
    if principal.membership.role not in {"owner", "manager"}:
        raise HTTPException(status_code=403, detail="Only an owner or manager can change policies")
    return principal


def _policies(db: Session, store_id) -> list[dict]:
    rows = db.execute(text("""
        SELECT d.id, d.title, d.version, d.content, d.index_status::text AS index_status, d.active, d.uploaded_at,
               (SELECT COUNT(*) FROM policy_chunks c WHERE c.document_id = d.id) AS passages
        FROM policy_documents d WHERE d.store_id = :store_id
        ORDER BY lower(d.title), d.version
    """), {"store_id": store_id}).mappings().all()
    grouped: dict[str, dict] = {}
    for row in rows:
        policy = grouped.setdefault(row["title"], {"title": row["title"], "active_version": None, "versions": []})
        policy["versions"].append({
            "id": row["id"], "version": row["version"], "content": row["content"], "index_status": row["index_status"],
            "active": row["active"], "passages": row["passages"], "uploaded_at": row["uploaded_at"].isoformat(),
        })
        if row["active"]:
            policy["active_version"] = row["version"]
    return list(grouped.values())


def _save_version(db: Session, store_id, title: str, content: str) -> int:
    title, content = title.strip(), content.strip()
    latest = db.execute(text("""
        SELECT version, content FROM policy_documents WHERE store_id = :store_id AND lower(title) = lower(:title)
        ORDER BY version DESC LIMIT 1
    """), {"store_id": store_id, "title": title}).first()
    if latest and latest.content.strip() == content:
        raise HTTPException(status_code=409, detail="This text is identical to the latest saved version")
    existing_title = db.execute(text(
        "SELECT title FROM policy_documents WHERE store_id = :s AND lower(title) = lower(:t) LIMIT 1"),
        {"s": store_id, "t": title}).scalar()
    version = (latest.version + 1) if latest else 1
    db.execute(text("""
        INSERT INTO policy_documents (id, store_id, version, title, content, index_status, active, uploaded_at)
        VALUES (:id, :store_id, :version, :title, :content, 'pending', false, :now)
    """), {"id": uuid.uuid4(), "store_id": store_id, "version": version, "title": existing_title or title,
           "content": content, "now": datetime.now(timezone.utc)})
    return version


def _document(db: Session, document_id: uuid.UUID, store_id):
    row = db.execute(text("""
        SELECT id, title, version, index_status::text AS index_status, active FROM policy_documents
        WHERE id = :id AND store_id = :store_id FOR UPDATE
    """), {"id": document_id, "store_id": store_id}).first()
    if not row:
        raise HTTPException(status_code=404, detail="Policy version not found")
    return row


@router.get("/api/policies")
def list_policies(principal: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    return {"policies": _policies(db, principal.membership.store_id), "retrieval": rag.status()}


@router.post("/api/policies", status_code=201)
def save_policy(body: PolicySave, principal: Principal = Depends(require_editor), db: Session = Depends(get_db)):
    """Create a policy, or save a new version when the title already exists. Versions are never overwritten."""
    try:
        version = _save_version(db, principal.membership.store_id, body.title, body.content)
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Another version was saved at the same time; try again")
    return {"version": version, "policies": _policies(db, principal.membership.store_id)}


@router.post("/api/policies/starter", status_code=201)
def add_starter_policies(principal: Principal = Depends(require_editor), db: Session = Depends(get_db)):
    """Add editable starter policies (not activated: the owner reviews them first)."""
    store_id = principal.membership.store_id
    existing = {t.lower() for (t,) in db.execute(text("SELECT DISTINCT title FROM policy_documents WHERE store_id = :s"), {"s": store_id})}
    added = 0
    for policy in STARTER_POLICIES:
        if policy["title"].lower() not in existing:
            _save_version(db, store_id, policy["title"], policy["content"])
            added += 1
    db.commit()
    return {"added": added, "policies": _policies(db, store_id)}


@router.post("/api/policies/{document_id}/activate")
def activate_policy(document_id: uuid.UUID, principal: Principal = Depends(require_editor), db: Session = Depends(get_db)):
    store_id = principal.membership.store_id
    doc = _document(db, document_id, store_id)
    if doc.index_status != "ready":
        raise HTTPException(status_code=409, detail="This version is still being indexed" if doc.index_status == "pending"
                            else "Indexing failed for this version; retry indexing before activating it")
    # One active version per policy: switch the old one off first (partial unique index).
    db.execute(text("UPDATE policy_documents SET active = false WHERE store_id = :s AND title = :t AND active"),
               {"s": store_id, "t": doc.title})
    db.execute(text("UPDATE policy_documents SET active = true WHERE id = :id"), {"id": doc.id})
    db.commit()
    return {"policies": _policies(db, store_id)}


@router.post("/api/policies/{document_id}/deactivate")
def deactivate_policy(document_id: uuid.UUID, principal: Principal = Depends(require_editor), db: Session = Depends(get_db)):
    doc = _document(db, document_id, principal.membership.store_id)
    db.execute(text("UPDATE policy_documents SET active = false WHERE id = :id"), {"id": doc.id})
    db.commit()
    return {"policies": _policies(db, principal.membership.store_id)}


@router.post("/api/policies/{document_id}/reindex")
def reindex_policy(document_id: uuid.UUID, principal: Principal = Depends(require_editor), db: Session = Depends(get_db)):
    doc = _document(db, document_id, principal.membership.store_id)
    if doc.index_status == "failed":
        db.execute(text("UPDATE policy_documents SET index_status = 'pending' WHERE id = :id"), {"id": doc.id})
        db.commit()
    return {"policies": _policies(db, principal.membership.store_id)}


@router.post("/api/policies/search")
def search_policies(body: PolicySearch, principal: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    """Try a customer question against this store's active policies (what the draft assistant would see)."""
    result = rag.search(db, principal.membership.store_id, body.query, body.category, limit=5)
    return {"state": result.state, "message": result.message, "best_score": result.best_score,
            "min_score": rag.settings.rag_min_score, "passages": [p.to_dict() for p in result.passages]}
