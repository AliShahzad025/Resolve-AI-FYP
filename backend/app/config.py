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
