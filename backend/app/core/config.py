from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://jobs:jobs@localhost:5433/jobs"
    redis_url: str = "redis://localhost:6379"

    # AI penulis surat: Gemini dipakai bila terisi, lalu Claude, lalu template (tanpa AI).
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.5-flash"
    # Dicoba berurutan bila model utama sibuk (503), kena limit (429), atau timeout.
    gemini_fallback_models: list[str] = ["gemini-3.5-flash-lite"]
    # Tingkat "berpikir" model Gemini 3.x: low = cepat (±5 dtk) & murah, cukup untuk surat lamaran.
    gemini_thinking_level: str = "low"
    anthropic_api_key: str = ""
    serpapi_key: str = ""
    serpapi_max_searches_per_day: int = 3  # kuota gratis SerpApi ±100 pencarian/bulan

    # Instagram (Meta Graph API — Business Discovery). Lihat README "Lowongan dari Instagram".
    meta_access_token: str = ""
    instagram_business_id: str = ""
    meta_graph_version: str = "v24.0"
    instagram_max_age_days: int = 3          # hanya postingan 3 hari terakhir
    instagram_posts_per_account: int = 25
    google_client_id: str = ""
    google_client_secret: str = ""

    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""

    storage_dir: str = "/storage"
    letter_model: str = "claude-opus-5-5"

    max_emails_per_day: int = 25
    min_seconds_between_emails: int = 90

    @property
    def smtp_ready(self) -> bool:
        return bool(self.smtp_user and self.smtp_password)

    @property
    def ai_provider(self) -> str | None:
        if self.gemini_api_key:
            return "gemini"
        if self.anthropic_api_key:
            return "anthropic"
        return None

    @property
    def ai_ready(self) -> bool:
        return self.ai_provider is not None

    @property
    def ai_model(self) -> str | None:
        return {"gemini": self.gemini_model, "anthropic": self.letter_model}.get(self.ai_provider or "")

    @property
    def ai_label(self) -> str:
        return {"gemini": "Gemini", "anthropic": "Claude"}.get(self.ai_provider or "", "Template")

    scrape_interval_hours: int = 3
    scrape_pages_per_keyword: int = 2
    # Hanya lowongan yang diposting dalam N hari terakhir yang diambil (semua sumber).
    scrape_max_age_days: int = 3
    http_timeout_seconds: float = 30.0
    request_delay_seconds: float = 2.0


@lru_cache
def get_settings() -> Settings:
    return Settings()
