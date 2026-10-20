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

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def allowed_origins(self) -> list[str]:
        return [origin.strip() for origin in self.frontend_origins.split(",") if origin.strip()]

    @property
    def frontend_base_url(self) -> str:
        return self.allowed_origins[0] if self.allowed_origins else "http://127.0.0.1:5173"


settings = Settings()
