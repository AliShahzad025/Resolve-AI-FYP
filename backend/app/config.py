from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "postgresql+psycopg://postgres:postgres@localhost:5432/resolveAi"
    jwt_secret: str = "development-only-change-this-secret-before-use"
    access_token_minutes: int = 60
    frontend_origins: str = "http://127.0.0.1:5173,http://localhost:5173"
    shopify_api_key: str = ""
    shopify_api_secret: str = ""
    shopify_encryption_key: str = ""
    shopify_app_base_url: str = ""
    shopify_webhook_base_url: str = ""
    shopify_api_version: str = "2026-10"
    # WhatsApp Cloud API (Meta). The app secret signs webhooks; the verify token answers
    # Meta's one-time subscription challenge. Per-store phone IDs/tokens are saved encrypted.
    whatsapp_app_secret: str = ""
    whatsapp_verify_token: str = ""
    whatsapp_api_version: str = "v23.0"
    whatsapp_graph_base_url: str = "https://graph.facebook.com"
    # Fernet key for stored WhatsApp tokens; falls back to SHOPIFY_ENCRYPTION_KEY when empty.
    whatsapp_encryption_key: str = ""
    # Run the outgoing-message worker inside the API process.
    whatsapp_worker_enabled: bool = True
    whatsapp_worker_interval_seconds: float = 3.0
    # Stage 5 complaint classifier (fine-tuned XLM-RoBERTa folders: category/, urgency/, sentiment/)
    classifier_enabled: bool = True
    classifier_model_dir: str = "models"      # relative to the backend folder
    classifier_dtype: str = "bfloat16"        # "bfloat16" halves RAM use; "float32" for full precision
    classifier_backfill_days: int = 7         # auto-analyse incoming messages this recent
    classifier_low_confidence: float = 0.6    # below this a label is flagged for staff review
    # Stage 5 policy retrieval (RAG): multilingual embeddings + Qdrant
    rag_enabled: bool = True
    embedding_model_dir: str = "models/embedding"   # filled by scripts/download_embedding_model.py
    qdrant_path: str = "qdrant_data"                # embedded Qdrant (no server needed); relative to backend/
    qdrant_url: str = ""                            # set to use a Qdrant server instead, e.g. http://localhost:6333
    qdrant_api_key: str = ""
    qdrant_collection: str = "policy_passages"
    rag_top_k: int = 3
    # Similarity floor for candidate passages. E5 scores are compressed (unrelated text still scores ~0.80), so this
    # only removes clear non-matches; the drafting model then judges whether the passages answer the customer.
    rag_min_score: float = 0.80
    # Stage 5 draft replies. Provider: "groq", "xai", "openai_compatible" or "anthropic"; empty = drafts disabled.
    draft_provider: str = ""
    draft_api_key: str = ""
    draft_model: str = ""                           # empty = the provider's default below
    draft_base_url: str = ""                        # only for "openai_compatible"
    draft_auto: bool = True                         # draft automatically for new customer messages
    draft_timeout_seconds: float = 60.0

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def public_base_url(self) -> str:
        return (self.shopify_webhook_base_url or self.shopify_app_base_url).strip().rstrip("/")

    @property
    def credential_encryption_key(self) -> str:
        return self.whatsapp_encryption_key or self.shopify_encryption_key

    @property
    def allowed_origins(self) -> list[str]:
        return [origin.strip() for origin in self.frontend_origins.split(",") if origin.strip()]

    @property
    def frontend_base_url(self) -> str:
        return self.allowed_origins[0] if self.allowed_origins else "http://127.0.0.1:5173"


settings = Settings()
