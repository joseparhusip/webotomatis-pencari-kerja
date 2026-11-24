import json
import logging
from functools import lru_cache
from pathlib import Path

from pydantic import ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

log = logging.getLogger(__name__)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://jobs:jobs@localhost:5434/jobs"
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


# Nilai yang boleh diubah dari halaman Pengaturan (disimpan di STORAGE_DIR/config/settings.json
# dan menimpa .env). DATABASE_URL, REDIS_URL, STORAGE_DIR tetap dari .env karena dibutuhkan saat start.
RUNTIME_KEYS = {
    "gemini_api_key", "gemini_model", "gemini_fallback_models", "gemini_thinking_level", "anthropic_api_key",
    "smtp_host", "smtp_port", "smtp_user", "smtp_password", "max_emails_per_day", "min_seconds_between_emails",
    "scrape_interval_hours", "scrape_pages_per_keyword", "scrape_max_age_days",
    "meta_access_token", "instagram_business_id", "meta_graph_version", "instagram_max_age_days",
    "serpapi_key", "serpapi_max_searches_per_day",
}
SECRET_KEYS = {"gemini_api_key", "anthropic_api_key", "smtp_password", "meta_access_token", "serpapi_key"}


@lru_cache
def env_settings() -> Settings:
    """Nilai dari environment / .env saja, tanpa isian halaman Pengaturan."""
    return Settings()


def runtime_file() -> Path:
    return Path(env_settings().storage_dir) / "config" / "settings.json"


def read_overrides() -> dict:
    try:
        data = json.loads(runtime_file().read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        log.warning("Gagal membaca %s — memakai .env", runtime_file(), exc_info=True)
        return {}
    return {k: v for k, v in data.items() if k in RUNTIME_KEYS} if isinstance(data, dict) else {}


def write_overrides(values: dict) -> None:
    """Validasi lalu simpan. ValidationError dilempar bila ada nilai yang salah tipe."""
    clean = {k: v for k, v in values.items() if k in RUNTIME_KEYS}
    Settings(**clean)  # validasi tipe sebelum menulis
    path = runtime_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(clean, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


_cache: dict = {"key": None, "settings": None}


def get_settings() -> Settings:
    """Settings dari .env ditimpa isian halaman Pengaturan.

    File dicek per panggilan (stat murah) supaya web & worker langsung memakai nilai baru
    tanpa restart.
    """
    try:
        key = runtime_file().stat().st_mtime_ns
    except OSError:
        return env_settings()
    if _cache["key"] != key:
        overrides = read_overrides()
        try:
            _cache["settings"] = Settings(**overrides) if overrides else env_settings()
        except ValidationError:
            log.warning("Isian Pengaturan tidak valid — memakai .env", exc_info=True)
            _cache["settings"] = env_settings()
        _cache["key"] = key
    return _cache["settings"]
