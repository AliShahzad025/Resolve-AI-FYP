"""Download the multilingual embedding model used for policy retrieval into backend/models/embedding.

Run once per machine (about 470 MB):
    python scripts/download_embedding_model.py
Everything is stored inside the project folder, not in the user profile on C:.
"""
import os
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
TARGET = BACKEND / "models" / "embedding"
os.environ.setdefault("HF_HOME", str(BACKEND / "models" / ".hf-cache"))  # keep the Hugging Face cache off C:

from huggingface_hub import snapshot_download  # noqa: E402

MODEL = sys.argv[1] if len(sys.argv) > 1 else "intfloat/multilingual-e5-small"
path = snapshot_download(
    MODEL, local_dir=TARGET,
    allow_patterns=["config.json", "model.safetensors", "tokenizer.json", "tokenizer_config.json",
                    "special_tokens_map.json", "sentencepiece.bpe.model"],
)
(TARGET / "resolve_embedding.json").write_text('{"model": "%s"}' % MODEL, encoding="utf-8")
size = sum(f.stat().st_size for f in TARGET.rglob("*") if f.is_file()) / 1e6
print(f"Saved {MODEL} to {path} ({size:.0f} MB)")
