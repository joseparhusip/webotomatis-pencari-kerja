import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime

import httpx

from app.models.enums import EmploymentType, ExperienceLevel, SourceType, WorkMode

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
)


@dataclass
class JobIn:
    """Lowongan hasil normalisasi dari satu sumber, sebelum dedup & simpan."""

    external_id: str
    url: str
    title: str
    company_name: str
    raw: dict
    company_logo_url: str | None = None
    location_city: str | None = None
    location_province: str | None = None
    work_mode: WorkMode | None = None
    employment_type: EmploymentType | None = None
    experience_level: ExperienceLevel | None = None
    salary_min: int | None = None
    salary_max: int | None = None
    salary_currency: str | None = None
    description: str | None = None
    requirements: str | None = None
    posted_at: datetime | None = None
    expires_at: datetime | None = None
    required_skills: list[str] = field(default_factory=list)
    benefits: list[str] = field(default_factory=list)
    education: str | None = None
    experience_min: int | None = None
    experience_max: int | None = None
    source_category: str | None = None  # kategori/fungsi asli dari situs sumber
    # True bila data sudah selengkap halaman detail (tidak perlu fetch_detail lagi).
    is_complete: bool = False


@dataclass
class JobDetail:
    """Isi halaman detail lowongan di situs sumber. None = situs tidak menyebutkan."""

    description: str | None = None
    requirements: str | None = None
    required_skills: list[str] = field(default_factory=list)
    benefits: list[str] = field(default_factory=list)
    education: str | None = None
    experience_min: int | None = None
    experience_max: int | None = None
    salary_min: int | None = None
    salary_max: int | None = None
    work_mode: WorkMode | None = None
    employment_type: EmploymentType | None = None
    location_city: str | None = None
    location_province: str | None = None
    posted_at: datetime | None = None
    expires_at: datetime | None = None
    closed: bool = False  # lowongan sudah ditutup / kedaluwarsa di situs sumber


@dataclass
class SearchPage:
    items: list[JobIn]
    has_more: bool


class Connector(ABC):
    name: str
    base_url: str
    type: SourceType = SourceType.API
    page_size: int = 30
    max_pages: int | None = None  # batas halaman khusus sumber ini (None = ikut setting global)
    detail_delay_seconds: float = 1.5

    @abstractmethod
    async def search(
        self, client: httpx.AsyncClient, keyword: str, page: int, known: set[str]
    ) -> SearchPage:
        """Ambil satu halaman hasil pencarian (halaman mulai dari 1).

        `known` = external_id yang sudah ada di DB untuk sumber ini. Connector yang harus membuka
        halaman detail per lowongan sebaiknya melewati id ini supaya tidak membebani situs.
        """

    supports_detail: bool = False
    # Jarak minimal antar scraping untuk sumber ini (mis. API berkuota). None = ikut jadwal global.
    min_interval_hours: float | None = None

    def keywords(self, default: list[str]) -> list[str]:
        """Keyword pencarian untuk sumber ini (sumber berkuota bisa membatasi jumlahnya)."""
        return default

    def keyword_label(self, keyword: str) -> str:
        """Teks keyword untuk progres scraping."""
        return f'"{keyword}"' if keyword else "semua lowongan"

    async def targets(self, session, default: list[str]) -> list[str]:
        """Daftar yang dicari per scraping (keyword, atau mis. username Instagram dari DB)."""
        return self.keywords(default)

    def disabled_reason(self) -> str | None:
        """Alasan sumber dilewati (mis. API key belum diisi). None = aktif."""
        return None

    async def fetch_detail(
        self, client: httpx.AsyncClient, url: str, external_id: str, raw: dict
    ) -> JobDetail | None:
        """Ambil halaman detail lowongan. Hanya untuk connector dengan supports_detail=True."""
        return None

    async def pause(self) -> None:
        await asyncio.sleep(self.detail_delay_seconds)


def new_http_client(timeout: float) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=True,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json, text/html;q=0.9, */*;q=0.8",
            "Accept-Language": "id-ID,id;q=0.9,en;q=0.8",
        },
    )
