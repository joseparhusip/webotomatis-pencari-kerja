from datetime import datetime

import httpx

from app.connectors.base import Connector, JobIn, SearchPage
from app.models.enums import ExperienceLevel, WorkMode
from app.services.parsing import html_to_text, localize_place, parse_employment_type


class KalibrrConnector(Connector):
    """kalibrr.com — endpoint JSON job board publik."""

    name = "kalibrr"
    base_url = "https://www.kalibrr.com"

    async def search(
        self, client: httpx.AsyncClient, keyword: str, page: int, known: set[str]
    ) -> SearchPage:
        resp = await client.get(
            f"{self.base_url}/kjs/job_board/search",
            params={
                "limit": self.page_size,
                "offset": (page - 1) * self.page_size,
                "text": keyword,
                "country": "Indonesia",
            },
        )
        resp.raise_for_status()
        jobs = resp.json().get("jobs", [])
        return SearchPage(
            [self._normalize(j) for j in jobs if _in_indonesia(j)], has_more=len(jobs) >= self.page_size
        )

    def _normalize(self, d: dict) -> JobIn:
        company = d.get("company") or {}
        loc = ((d.get("google_location") or {}).get("address_components")) or {}

        # Kalibrr menyimpan gaji walau perusahaan memilih menyembunyikannya — hormati pilihan itu.
        shown = bool(d.get("salary_shown"))
        salary_min = d.get("base_salary") if shown else None
        salary_max = d.get("maximum_salary") if shown else None
        if shown and d.get("salary_interval") not in (None, "month"):
            salary_min = salary_max = None

        if d.get("is_work_from_home"):
            work_mode = WorkMode.REMOTE
        elif d.get("is_hybrid"):
            work_mode = WorkMode.HYBRID
        else:
            work_mode = WorkMode.ONSITE

        return JobIn(
            external_id=str(d["id"]),
            url=f"{self.base_url}/c/{company.get('code')}/jobs/{d['id']}/{d.get('slug', '')}",
            title=(d.get("name") or "").strip(),
            company_name=(d.get("company_name") or company.get("name") or "").strip(),
            company_logo_url=company.get("logo_small"),
            location_city=localize_place(loc.get("city")),
            location_province=localize_place(loc.get("region")),
            work_mode=work_mode,
            employment_type=parse_employment_type(d.get("tenure")),
            source_category=d.get("function"),
            experience_level=ExperienceLevel.ENTRY if d.get("is_open_to_fresh_grads") else None,
            salary_min=salary_min,
            salary_max=salary_max,
            salary_currency=(d.get("salary_currency") or "IDR") if (salary_min or salary_max) else None,
            description=html_to_text(d.get("description")),
            requirements=html_to_text(d.get("qualifications")),
            posted_at=_parse_dt(d.get("activation_date")),
            expires_at=_parse_dt(d.get("application_end_date")),
            raw={k: v for k, v in d.items() if k != "company_info"},
            is_complete=True,  # API pencarian Kalibrr sudah memuat deskripsi & kualifikasi lengkap
        )


def _in_indonesia(d: dict) -> bool:
    country = (((d.get("google_location") or {}).get("address_components")) or {}).get("country")
    return country in (None, "Indonesia")


def _parse_dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None
