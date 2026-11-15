"""Stage 5 policy retrieval (the "R" in RAG).

Policy versions are split into short passages, embedded with a multilingual model and stored in Qdrant.
A complaint is embedded the same way and matched ONLY against the active, indexed policy versions of
its own store. Passages below a minimum similarity are not treated as evidence.

Qdrant runs embedded by default (a folder on disk, no server); set QDRANT_URL to use a Qdrant server.
"""
import json
import logging
import os
import re
import threading
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import settings
from app.database import SessionLocal

logger = logging.getLogger(__name__)
BACKEND_DIR = Path(__file__).resolve().parent.parent
MAX_PASSAGE_WORDS = 110

# English hints added to the search query from the classifier's category. They help a Roman Urdu
# complaint ("parcel toota hua aaya") reach an English policy passage ("damaged items ...").
CATEGORY_HINTS = {
    "damaged_item": "damaged, broken or defective item: replacement or refund",
    "wrong_item": "wrong or missing item received: correction, exchange or refund",
    "return_exchange": "return or exchange an item: conditions and time limit",
    "refund_request": "refund status and how long refunds take",
    "delivery_issue": "delivery time, delay, tracking and courier",
    "payment_issue": "payment problem: charged twice or deducted without confirmation, payment methods",
    "cancellation": "cancel an order: when cancellation is possible",
}


# Roman Urdu -> English keywords. The embedding model matches Roman Urdu to English policy text poorly,
# so common complaint words are translated and appended to the search query. This works without the
# classifier (or when its category is wrong) because it only depends on the customer's own words.
GLOSSARY = [
    (r"\b(toot[ai]?|tut[ai]|toot|phat[ai]|kharab|khrab|kharaab|crack(ed)?|damage[d]?|defective|broken|torn|daagh)\b", "damaged broken defective item"),
    (r"\b(ghalat|galat|glt|wrong|dusra|dosra|different)\b", "wrong item delivered"),
    (r"\b(kam hai|kam aaya|kam aya|missing|nahi mila|nai mila|nhi mila)\b", "missing item"),
    (r"\b(wapis|wapas|wapus|waapis|return)\b", "return"),
    (r"\b(paise|paisay|pese|pesay|paisa|raqam|refund|money)\b", "refund money"),
    (r"\b(badal|badalna|tabdeel|exchange|size|chota|chhota|bara|bada)\b", "exchange size or colour"),
    (r"\b(kab|kitne din|der|late|delay|delayed|abhi tak|ab tak|intezar|intazar)\b", "delivery time delay how long"),
    (r"\b(kahan|kaha|tracking|track|rider|courier|pohanch|pohncha)\b", "delivery tracking courier"),
    (r"\b(cancel|rok dein|rok do|khatam)\b", "cancel order"),
    (r"\b(kat gaye|kat gaya|kata|kati|charge[d]?|deduct|double|do dafa|payment|easypaisa|jazzcash|cod|cash|card)\b", "payment charged deducted"),
]
_GLOSSARY = [(re.compile(pattern, re.IGNORECASE), english) for pattern, english in GLOSSARY]


def glossary_keywords(message: str) -> list[str]:
    return [english for pattern, english in _GLOSSARY if pattern.search(message)]


@dataclass
class Passage:
    chunk_id: str
    document_id: str
    title: str
    version: int
    chunk_index: int
    text: str
    score: float

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Retrieval:
    state: str                 # ok | no_policy | no_evidence | unavailable
    passages: list[Passage]    # evidence (score >= minimum), best first
    message: str = ""
    best_score: float | None = None


# ---------------------------------------------------------------------------
# Splitting a policy into passages
# ---------------------------------------------------------------------------
def _is_heading(line: str) -> bool:
    words = line.split()
    return 0 < len(words) <= 8 and not re.search(r"[.!?:;,]$", line.strip()) and len(line) <= 80


def chunk_policy(content: str, max_words: int = MAX_PASSAGE_WORDS) -> list[str]:
    """Split policy text into self-contained passages, keeping each section heading with its sentences."""
    passages: list[str] = []
    heading = ""
    for block in re.split(r"\n\s*\n", content.replace("\r\n", "\n").strip()):
        lines = [ln.strip() for ln in block.split("\n") if ln.strip()]
        if not lines:
            continue
        if _is_heading(lines[0]):
            heading = lines[0].lstrip("#").strip()
            lines = lines[1:]
            if not lines:
                continue  # heading on its own line: applies to the next block
        sentences: list[str] = []
        for line in lines:
            line = re.sub(r"^\s*(?:[-*•]|\d+[.)])\s+", "", line)
            sentences.extend(s.strip() for s in re.split(r"(?<=[.!?])\s+", line) if s.strip())
        block_passages: list[str] = []
        current: list[str] = []
        count = 0
        for sentence in sentences:
            words = len(sentence.split())
            if current and count + words > max_words:
                block_passages.append(" ".join(current))
                current, count = [], 0
            current.append(sentence)
            count += words
        if current:
            block_passages.append(" ".join(current))
        # Each passage carries its section heading so it still makes sense on its own.
        passages.extend(f"{heading}: {p}" if heading else p for p in block_passages)
    return [p for p in passages if len(p.split()) >= 3]


# ---------------------------------------------------------------------------
# Embeddings
# ---------------------------------------------------------------------------
class Embedder:
    def __init__(self) -> None:
        self.state = "not_loaded"   # not_loaded | ready | unavailable | error
        self.error: str | None = None
        self.name = "unknown"
        self.dim = 0
        self._tokenizer = None
        self._model = None
        self._lock = threading.Lock()
        self.model_dir = (BACKEND_DIR / settings.embedding_model_dir).resolve()

    def ensure_loaded(self) -> bool:
        if self.state == "ready":
            return True
        with self._lock:
            if self.state == "ready":
                return True
            if not settings.rag_enabled:
                self.state, self.error = "unavailable", "Policy retrieval disabled (RAG_ENABLED=false)"
                return False
            if not (self.model_dir / "config.json").exists():
                self.state = "unavailable"
                self.error = f"Embedding model not found in {self.model_dir}. Run: python scripts/download_embedding_model.py"
                return False
            try:
                import torch
                from transformers import AutoModel, AutoTokenizer
                from app.classifier import MODEL_LOAD_LOCK
                with MODEL_LOAD_LOCK:  # never load while the half-precision classifier is loading
                    self._tokenizer = AutoTokenizer.from_pretrained(self.model_dir)
                    try:
                        model = AutoModel.from_pretrained(self.model_dir, dtype=torch.float32)
                    except TypeError:  # transformers < 4.56 used torch_dtype
                        model = AutoModel.from_pretrained(self.model_dir, torch_dtype=torch.float32)
                self._model = model.float().eval()
                meta = self.model_dir / "resolve_embedding.json"
                self.name = json.loads(meta.read_text(encoding="utf-8")).get("model", self.model_dir.name) if meta.exists() else self.model_dir.name
                self.dim = int(self._model.config.hidden_size)
                self.state, self.error = "ready", None
                logger.info("Embedding model ready: %s (%s dims)", self.name, self.dim)
                return True
            except ImportError:
                self.state, self.error = "unavailable", "ML packages are not installed (see backend/requirements-ml.txt)"
            except Exception as exc:
                logger.exception("Could not load embedding model")
                self.state, self.error = "error", f"Could not load embedding model: {type(exc).__name__}: {exc}"[:400]
            return False

    def encode(self, texts: list[str], kind: str) -> list[list[float]]:
        """kind = "query" or "passage" (E5 models expect these prefixes)."""
        import torch
        if not self.ensure_loaded():
            raise RuntimeError(self.error or "Embedding model unavailable")
        prefix = "query: " if kind == "query" else "passage: "
        vectors: list[list[float]] = []
        with self._lock, torch.inference_mode():
            for start in range(0, len(texts), 16):
                batch = [prefix + t for t in texts[start:start + 16]]
                encoded = self._tokenizer(batch, padding=True, truncation=True, max_length=512, return_tensors="pt")
                hidden = self._model(**encoded).last_hidden_state
                mask = encoded["attention_mask"].unsqueeze(-1).to(hidden.dtype)
                pooled = (hidden * mask).sum(1) / mask.sum(1).clamp(min=1e-9)
                pooled = torch.nn.functional.normalize(pooled, p=2, dim=1)
                vectors.extend(pooled.tolist())
        return vectors


embedder = Embedder()


# ---------------------------------------------------------------------------
# Qdrant
# ---------------------------------------------------------------------------
class VectorStore:
    def __init__(self) -> None:
        self._client = None
        self._lock = threading.RLock()
        self.error: str | None = None
        self.path = (BACKEND_DIR / settings.qdrant_path).resolve()

    @property
    def mode(self) -> str:
        return "server" if settings.qdrant_url else "embedded"

    def client(self):
        with self._lock:
            if self._client is None:
                from qdrant_client import QdrantClient
                try:
                    if settings.qdrant_url:
                        self._client = QdrantClient(url=settings.qdrant_url, api_key=settings.qdrant_api_key or None, timeout=20)
                    else:
                        self._client = QdrantClient(path=str(self.path))
                    self.error = None
                except Exception as exc:  # e.g. the folder is locked by another running backend
                    self.error = f"Qdrant unavailable: {type(exc).__name__}: {exc}"[:400]
                    raise RuntimeError(self.error)
            return self._client

    def close(self) -> None:
        with self._lock:
            if self._client is not None:
                try:
                    self._client.close()
                finally:
                    self._client = None

    def ensure_collection(self, dim: int) -> None:
        from qdrant_client import models
        with self._lock:
            client = self.client()
            if not client.collection_exists(settings.qdrant_collection):
                client.create_collection(settings.qdrant_collection,
                                         vectors_config=models.VectorParams(size=dim, distance=models.Distance.COSINE))

    def _doc_filter(self, document_id):
        from qdrant_client import models
        return models.Filter(must=[models.FieldCondition(key="document_id", match=models.MatchValue(value=str(document_id)))])

    def delete_document(self, document_id) -> None:
        from qdrant_client import models
        with self._lock:
            client = self.client()
            if client.collection_exists(settings.qdrant_collection):
                client.delete(settings.qdrant_collection, points_selector=models.FilterSelector(filter=self._doc_filter(document_id)))

    def count_document(self, document_id) -> int:
        with self._lock:
            client = self.client()
            if not client.collection_exists(settings.qdrant_collection):
                return 0
            return client.count(settings.qdrant_collection, count_filter=self._doc_filter(document_id), exact=True).count

    def upsert(self, points: list[dict], dim: int) -> None:
        from qdrant_client import models
        self.ensure_collection(dim)
        with self._lock:
            self.client().upsert(settings.qdrant_collection, points=[
                models.PointStruct(id=p["id"], vector=p["vector"], payload=p["payload"]) for p in points])

    def search(self, vector: list[float], store_id, document_ids: list[str], limit: int):
        from qdrant_client import models
        with self._lock:
            client = self.client()
            if not client.collection_exists(settings.qdrant_collection):
                return []
            flt = models.Filter(must=[
                models.FieldCondition(key="store_id", match=models.MatchValue(value=str(store_id))),
                models.FieldCondition(key="document_id", match=models.MatchAny(any=[str(d) for d in document_ids])),
            ])
            return client.query_points(settings.qdrant_collection, query=vector, query_filter=flt,
                                       limit=limit, with_payload=True).points


store = VectorStore()


# ---------------------------------------------------------------------------
# Indexing
# ---------------------------------------------------------------------------
def index_document(db: Session, document_id) -> int:
    """Chunk + embed one policy version and mark it ready. The caller commits. Returns the passage count."""
    doc = db.execute(text("SELECT id, store_id, title, version, content FROM policy_documents WHERE id = :id FOR UPDATE"),
                     {"id": document_id}).first()
    if not doc:
        return 0
    passages = chunk_policy(doc.content)
    if not passages:
        raise ValueError("The policy has no text that can be indexed")
    vectors = embedder.encode(passages, "passage")
    store.delete_document(doc.id)
    db.execute(text("DELETE FROM policy_chunks WHERE document_id = :id"), {"id": doc.id})
    points = []
    for index, (passage, vector) in enumerate(zip(passages, vectors)):
        chunk_id, point_id = uuid.uuid4(), str(uuid.uuid4())
        db.execute(text("""
            INSERT INTO policy_chunks (id, document_id, chunk_index, content, qdrant_point_id, embedding_version)
            VALUES (:id, :document_id, :chunk_index, :content, :point_id, :embedding_version)
        """), {"id": chunk_id, "document_id": doc.id, "chunk_index": index, "content": passage,
               "point_id": point_id, "embedding_version": embedder.name[:100]})
        points.append({"id": point_id, "vector": vector, "payload": {
            "store_id": str(doc.store_id), "document_id": str(doc.id), "chunk_id": str(chunk_id),
            "title": doc.title, "version": doc.version, "chunk_index": index, "text": passage}})
    store.upsert(points, embedder.dim)
    db.execute(text("UPDATE policy_documents SET index_status = 'ready' WHERE id = :id"), {"id": doc.id})
    return len(passages)


def index_pending(limit: int = 5) -> int:
    """Index policy versions waiting in the 'pending' state (the database row is the queue)."""
    with SessionLocal() as db:
        ids = [r.id for r in db.execute(text(
            "SELECT id FROM policy_documents WHERE index_status = 'pending' ORDER BY uploaded_at LIMIT :n"), {"n": limit})]
    if not ids or not embedder.ensure_loaded():
        return 0
    done = 0
    for document_id in ids:
        with SessionLocal() as db:
            try:
                count = index_document(db, document_id)
                db.commit()
                done += 1
                store.error = None
                logger.info("Indexed policy version %s (%s passages)", document_id, count)
            except Exception as exc:
                db.rollback()
                logger.exception("Policy indexing failed for %s", document_id)
                # A failed version can never be activated (database rule), and staff can retry it.
                db.execute(text("UPDATE policy_documents SET index_status = 'failed', active = false WHERE id = :id"), {"id": document_id})
                db.commit()
                store.error = f"{type(exc).__name__}: {exc}"[:300]
    return done


def reconcile() -> None:
    """If Qdrant lost a ready version's vectors (new machine, deleted folder), rebuild them in place.
    The version keeps its 'ready' / active state, so staff do not have to re-activate anything."""
    with SessionLocal() as db:
        rows = db.execute(text("SELECT id FROM policy_documents WHERE index_status = 'ready'")).all()
    missing = [r.id for r in rows if store.count_document(r.id) == 0]
    for document_id in missing:
        with SessionLocal() as db:
            try:
                index_document(db, document_id)
                db.commit()
            except Exception:
                db.rollback()
                logger.exception("Could not rebuild vectors for policy version %s", document_id)
    if missing:
        logger.warning("Rebuilt Qdrant vectors for %s policy version(s)", len(missing))


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------
def compose_query(message: str, category: str | None, use_glossary: bool = True) -> str:
    """Customer message + English keywords from the glossary + an English hint for the classifier's category."""
    extras = glossary_keywords(message) if use_glossary else []
    hint = CATEGORY_HINTS.get((category or "").lower())
    if hint:
        extras.append(hint)
    return f"{message.strip()} ({'; '.join(extras)})" if extras else message.strip()


def search(db: Session, store_id, message: str, category: str | None = None, limit: int | None = None) -> Retrieval:
    active = db.execute(text("""
        SELECT id FROM policy_documents WHERE store_id = :store_id AND active AND index_status = 'ready'
    """), {"store_id": store_id}).all()
    if not active:
        return Retrieval("no_policy", [], "This store has no active policy yet. Add one in the Policy library and activate it.")
    try:
        vector = embedder.encode([compose_query(message, category)], "query")[0]
        hits = store.search(vector, store_id, [r.id for r in active], limit or settings.rag_top_k)
    except Exception as exc:
        logger.warning("Policy retrieval unavailable: %s", exc)
        return Retrieval("unavailable", [], embedder.error or store.error or f"Policy search is unavailable: {exc}"[:300])
    passages = [Passage(
        chunk_id=str(h.payload.get("chunk_id")), document_id=str(h.payload.get("document_id")),
        title=str(h.payload.get("title")), version=int(h.payload.get("version") or 0),
        chunk_index=int(h.payload.get("chunk_index") or 0), text=str(h.payload.get("text") or ""),
        score=round(float(h.score), 4)) for h in hits]
    best = passages[0].score if passages else None
    evidence = [p for p in passages if p.score >= settings.rag_min_score]
    if not evidence:
        return Retrieval("no_evidence", [], "No policy passage matches this message closely enough; a person should handle it.", best)
    return Retrieval("ok", evidence, "", best)


def status() -> dict:
    return {"embedding": {"state": embedder.state, "error": embedder.error, "model": embedder.name},
            "vector_store": {"mode": store.mode, "error": store.error, "collection": settings.qdrant_collection},
            "min_score": settings.rag_min_score, "top_k": settings.rag_top_k}


# ---------------------------------------------------------------------------
# Background indexer
# ---------------------------------------------------------------------------
_stop = threading.Event()
_thread: threading.Thread | None = None
_hooks: list = []   # extra periodic jobs (e.g. automatic drafting) registered by other modules


def register_periodic(job) -> None:
    _hooks.append(job)


def _run() -> None:
    try:
        if embedder.ensure_loaded():
            reconcile()
    except Exception:
        logger.exception("Policy index reconciliation failed")
    while not _stop.wait(4):
        try:
            index_pending()
        except Exception:
            logger.exception("Policy indexer iteration failed")
        for job in _hooks:
            try:
                job()
            except Exception:
                logger.exception("Periodic job %s failed", getattr(job, "__name__", job))


def start_indexer() -> None:
    global _thread
    if not settings.rag_enabled or (_thread and _thread.is_alive()):
        return
    _stop.clear()
    _thread = threading.Thread(target=_run, name="policy-indexer", daemon=True)
    _thread.start()


def stop_indexer() -> None:
    _stop.set()
    if os.environ.get("RESOLVE_KEEP_QDRANT_OPEN") != "1":
        store.close()
