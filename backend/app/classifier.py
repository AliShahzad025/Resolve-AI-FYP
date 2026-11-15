"""Stage 5 complaint analysis: category, urgency and sentiment from fine-tuned XLM-RoBERTa models.

The models are loaded in a background thread so the API starts immediately. Incoming customer
messages are analysed asynchronously by a worker; a missing or failing model never blocks message
intake or staff replies (cases simply show "Unclassified").
"""
import json
import logging
import os
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import settings
from app.database import SessionLocal

logger = logging.getLogger(__name__)

TASKS = ("category", "urgency", "sentiment")
# transformers temporarily changes torch's global default dtype while a model loads, so two models must
# never load at the same time (the half-precision classifier would corrupt the embedding model's dtype).
MODEL_LOAD_LOCK = threading.Lock()
MAX_LENGTH = 128
BACKEND_DIR = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------------------
# Language detection (English / Roman Urdu / mixed / Urdu script)
# ---------------------------------------------------------------------------
ROMAN_URDU_WORDS = {
    "hai", "hain", "nahi", "nai", "nhi", "kya", "kab", "kahan", "kyun", "kaise", "mera", "meri", "mere", "aap", "ap",
    "ka", "ki", "ke", "ko", "ho", "kar", "karein", "karo", "karna", "raha", "rahi", "rahe", "chahiye", "wapis", "bhai",
    "abhi", "tak", "aaya", "aya", "aayi", "gaya", "gayi", "diya", "bhej", "bheja", "bhejein", "se", "mein", "main",
    "ye", "yeh", "wo", "woh", "aur", "lekin", "bohat", "bahut", "hua", "hui", "tha", "thi", "dein", "jaldi", "paise",
    "kitne", "hun", "hoon", "sahi", "ghalat", "kharab", "toota", "phata", "foran", "warna", "batayein", "milega",
}
ENGLISH_WORDS = {
    "the", "is", "are", "my", "i", "you", "your", "a", "an", "to", "of", "and", "not", "have", "has", "was", "were",
    "it", "for", "this", "that", "please", "when", "where", "what", "why", "can", "will", "received", "still",
    "been", "did", "do", "does", "with", "but", "me", "want", "need", "should", "would", "they", "no", "yet",
}
LANGUAGE_LABEL = {"en": "English", "roman_urdu": "Roman Urdu", "mixed": "Mixed", "urdu": "Urdu"}


def detect_language(text_value: str) -> str:
    if re.search(r"[؀-ۿ]", text_value):
        return "urdu"
    words = re.findall(r"[a-z]+", text_value.lower())
    ru = sum(w in ROMAN_URDU_WORDS for w in words)
    en = sum(w in ENGLISH_WORDS for w in words)
    if ru == 0:
        return "en"
    if en == 0:
        return "roman_urdu"
    return "mixed" if min(ru, en) / max(ru, en) >= 0.25 else ("roman_urdu" if ru > en else "en")


# Greetings / acknowledgements. The models only know complaint categories, so these must not be classified
# (they would get a confident but meaningless complaint label and replace the case's real analysis).
SMALL_TALK_WORDS = {
    "hi", "hello", "hey", "helo", "salam", "salaam", "assalam", "asalam", "aoa", "o", "u", "alaikum", "alaykum", "walaikum",
    "how", "are", "r", "you", "ade", "ar", "kaise", "kese", "kaisay", "ho", "hain", "aap", "ap", "thanks", "thank", "thx",
    "shukriya", "ok", "okay", "okk", "k", "hmm", "hm", "yes", "no", "ji", "jee", "haan", "han", "theek", "thik", "hai",
    "good", "morning", "evening", "night", "bye", "khuda", "hafiz", "allah", "sir", "bhai", "dear", "team", "there", "all",
    "fine", "great", "nice", "done", "sure", "welcome", "doing",
}


def is_small_talk(text_value: str) -> bool:
    words = re.findall(r"[a-z]+", text_value.lower())
    return 0 < len(words) <= 8 and all(w in SMALL_TALK_WORDS for w in words)


def analysable_text(body: str) -> str | None:
    """Text worth classifying: media markers removed; greetings/acknowledgements skipped."""
    cleaned = re.sub(r"^\[[^\]]*\]\s*", "", (body or "").strip()).strip()
    if len(cleaned) < 2 or is_small_talk(cleaned):
        return None
    return cleaned


# ---------------------------------------------------------------------------
# Model holder
# ---------------------------------------------------------------------------
class ComplaintClassifier:
    def __init__(self) -> None:
        self.state = "not_loaded"   # not_loaded | loading | ready | unavailable | error
        self.error: str | None = None
        self.models: dict = {}
        self.versions: dict[str, str] = {}
        self.model_dir = (BACKEND_DIR / settings.classifier_model_dir).resolve()
        self._lock = threading.Lock()

    def status(self) -> dict:
        return {"state": self.state, "error": self.error, "model_versions": self.versions,
                "model_dir": str(self.model_dir), "dtype": settings.classifier_dtype}

    def load(self) -> None:
        if not settings.classifier_enabled:
            self.state, self.error = "unavailable", "Classifier disabled (CLASSIFIER_ENABLED=false)"
            return
        self.state = "loading"
        try:
            import torch
            from transformers import AutoModelForSequenceClassification, AutoTokenizer
        except ImportError:
            self.state, self.error = "unavailable", "ML packages are not installed (see backend/requirements-ml.txt)"
            return
        missing = [t for t in TASKS if not (self.model_dir / t / "config.json").exists()]
        if missing:
            self.state = "unavailable"
            self.error = f"Model folders missing in {self.model_dir}: {', '.join(missing)}. Extract resolve_models.zip there."
            return
        dtype = torch.bfloat16 if settings.classifier_dtype.lower() == "bfloat16" else torch.float32
        torch.set_num_threads(max(1, min(4, os.cpu_count() or 1)))
        try:
            models, versions = {}, {}
            for task in TASKS:
                folder = self.model_dir / task
                with MODEL_LOAD_LOCK:
                    tokenizer = AutoTokenizer.from_pretrained(folder)
                    try:
                        model = AutoModelForSequenceClassification.from_pretrained(folder, dtype=dtype)
                    except TypeError:  # transformers < 4.56 used torch_dtype
                        model = AutoModelForSequenceClassification.from_pretrained(folder, torch_dtype=dtype)
                model.eval()
                meta_file = folder / "resolve_model.json"
                meta = json.loads(meta_file.read_text(encoding="utf-8")) if meta_file.exists() else {}
                models[task] = (tokenizer, model)
                versions[task] = str(meta.get("model_version") or f"{task}-{folder.name}")
                logger.info("Loaded %s model %s", task, versions[task])
            self.models, self.versions = models, versions
            self.state, self.error = "ready", None
        except Exception as exc:  # corrupt download, out of memory, incompatible files...
            logger.exception("Could not load complaint classifier")
            self.state, self.error = "error", f"Could not load models: {type(exc).__name__}: {exc}"[:500]

    def predict(self, text_value: str) -> dict[str, tuple[str, float]]:
        import torch
        if self.state != "ready":
            raise RuntimeError(self.error or "Classifier is not ready")
        results = {}
        with self._lock, torch.inference_mode():
            for task, (tokenizer, model) in self.models.items():
                encoded = tokenizer(text_value, truncation=True, max_length=MAX_LENGTH, return_tensors="pt")
                probs = torch.softmax(model(**encoded).logits.float(), dim=-1)[0]
                index = int(probs.argmax())
                labels = model.config.id2label
                label = labels.get(index, labels.get(str(index), str(index)))
                results[task] = (str(label).lower(), round(float(probs[index]), 4))
        return results


classifier = ComplaintClassifier()


# ---------------------------------------------------------------------------
# Persisting analyses
# ---------------------------------------------------------------------------
def analyse_message(db: Session, message_id, body: str) -> dict | None:
    """Classify one message and append a complaint_analyses row (the latest row is the current one)."""
    content = analysable_text(body)
    if content is None:
        return None
    predicted = classifier.predict(content)
    urgency = predicted["urgency"][0] if predicted["urgency"][0] in {"low", "medium", "high"} else "medium"
    sentiment = predicted["sentiment"][0] if predicted["sentiment"][0] in {"positive", "neutral", "negative"} else "neutral"
    row = {
        "id": uuid.uuid4(), "message_id": message_id, "category": predicted["category"][0][:100],
        "urgency": urgency, "sentiment": sentiment, "language": detect_language(content),
        "confidence": json.dumps({task: score for task, (_, score) in predicted.items()}),
        "model_versions": json.dumps(classifier.versions), "analysed_at": datetime.now(timezone.utc),
    }
    db.execute(text("""
        INSERT INTO complaint_analyses (id, message_id, category, urgency, sentiment, language, confidence, model_versions, analysed_at)
        VALUES (:id, :message_id, :category, CAST(:urgency AS urgency_t), CAST(:sentiment AS sentiment_t), :language,
                CAST(:confidence AS jsonb), CAST(:model_versions AS jsonb), :analysed_at)
    """), row)
    return row


def analyse_pending(limit: int = 10) -> int:
    """Analyse recent incoming messages that have no analysis yet. Returns how many were analysed."""
    if classifier.state != "ready":
        return 0
    done = 0
    with SessionLocal() as db:
        rows = db.execute(text("""
            SELECT m.id, m.body FROM messages m
            WHERE m.direction = 'incoming'
              AND m.sent_at > now() - make_interval(days => :days)
              AND NOT EXISTS (SELECT 1 FROM complaint_analyses a WHERE a.message_id = m.id)
              AND NOT (m.id = ANY(CAST(:skip AS uuid[])))
            ORDER BY m.sent_at LIMIT :limit
        """), {"days": settings.classifier_backfill_days, "limit": limit, "skip": list(_skip)}).all()
        for row in rows:
            try:
                # Another backend process may pick the same message: lock it, then re-check it is still unanalysed.
                locked = db.execute(text("SELECT pg_try_advisory_xact_lock(hashtext(:key))"), {"key": f"analyse:{row.id}"}).scalar()
                already = db.execute(text("SELECT EXISTS (SELECT 1 FROM complaint_analyses WHERE message_id = :id)"), {"id": row.id}).scalar()
                if not locked or already:
                    db.rollback()
                    continue
                if analyse_message(db, row.id, row.body) is None:
                    _skip.add(str(row.id))  # media-only message: nothing to classify
                db.commit()
                done += 1
            except Exception:
                db.rollback()
                _skip.add(str(row.id))
                logger.exception("Complaint analysis failed for message %s", row.id)
    return done


# ---------------------------------------------------------------------------
# Background thread: load models once, then analyse new messages
# ---------------------------------------------------------------------------
_skip: set[str] = set()
_stop = threading.Event()
_thread: threading.Thread | None = None


def _run() -> None:
    classifier.load()
    logger.info("Complaint classifier: %s%s", classifier.state, f" ({classifier.error})" if classifier.error else "")
    while not _stop.wait(3):
        try:
            while analyse_pending() and not _stop.is_set():
                pass
        except Exception:
            logger.exception("Complaint analysis worker iteration failed")


def start_classifier() -> None:
    global _thread
    if _thread and _thread.is_alive():
        return
    _stop.clear()
    _thread = threading.Thread(target=_run, name="complaint-classifier", daemon=True)
    _thread.start()


def stop_classifier() -> None:
    _stop.set()
