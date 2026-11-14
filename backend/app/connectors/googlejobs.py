"""Google Jobs lewat SerpApi — menjangkau lowongan dari ribuan situs karir perusahaan sekaligus.

Kuota SerpApi terbatas, jadi sumber ini hanya berjalan sekali sehari dengan beberapa keyword
(SERPAPI_MAX_SEARCHES_PER_DAY). Dokumentasi: https://serpapi.com/google-jobs-api
"""

import hashlib
import re
from datetime import UTC, datetime, timedelta

import httpx

from app.connectors.base import Connector, JobIn, SearchPage
from app.core.config import get_settings
from app.models.enums import EmploymentType, SourceType, WorkMode
from app.services.parsing import parse_salary_label, split_location

API_URL = "https://serpapi.com/search.json"
_AGGREGATORS = re.compile(r"linkedin|jobstreet|glints|kalibrr|indeed|loker\.id|kitalulus|dealls|jooble|karir\.com|"
                          r"jobsdb|talent\.com|jora", re.I)
_REL = re.compile(r"(\d+)\s*(jam|hour|hari|day|minggu|week|bulan|month)", re.I)


class GoogleJobsConnector(Connector):
    name = "googlejobs"
    base_url = "https://www.google.com/search?ibp=htl;jobs"
    type = SourceType.SERPAPI
    max_pages = 1
    min_interval_hours = 20  # sekali sehari

    def disabled_reason(self) -> str | None:
        return None if get_settings().serpapi_key else "SERPAPI_KEY belum diisi"

    def keywords(self, default: list[str]) -> list[str]:
        return default[: max(1, get_settings().serpapi_max_searches_per_day)]

    async def search(
        self, client: httpx.AsyncClient, keyword: str, page: int, known: set[str]
    ) -> SearchPage:
        resp = await client.get(API_URL, params={
            "engine": "google_jobs", "q": keyword, "location": "Indonesia", "gl": "id", "hl": "id",
            "api_key": get_settings().serpapi_key,
        })
        data = resp.json()
        if data.get("error"):
            if "hasn't returned any results" in str(data["error"]):
                return SearchPage([], has_more=False)
            raise ValueError(f"SerpApi: {data['error']}")
        resp.raise_for_status()
        return SearchPage([self._normalize(j) for j in data.get("jobs_results") or []], has_more=False)

    def _normalize(self, d: dict) -> JobIn:
        ext = d.get("detected_extensions") or {}
        city, province = split_location(d.get("location"))
        salary_min, salary_max = parse_salary_label(ext.get("salary"))
        highlights = {(h.get("title") or "").lower(): h.get("items") or [] for h in d.get("job_highlights") or []}
        quals = _items(highlights, "qualification", "kualifikasi", "persyaratan")
        resp = _items(highlights, "responsibilit", "tanggung jawab")
        benefits = _items(highlights, "benefit", "tunjangan", "manfaat")
        description = (d.get("description") or "").strip()
        if resp and not description:
            description = "\n".join(f"• {x}" for x in resp)
        options = d.get("apply_options") or []
        # Utamakan situs karir perusahaan sendiri di atas portal agregator.
        direct = next((o for o in options if o.get("link") and not _AGGREGATORS.search(o.get("link", ""))), None)
        chosen = direct or (options[0] if options else {})
        url = chosen.get("link") or d.get("share_link") or self.base_url
        ext_id = d.get("job_id") or hashlib.sha1(f"{d.get('title')}|{d.get('company_name')}".encode()).hexdigest()
        schedule = (ext.get("schedule_type") or "").lower()
        return JobIn(
            external_id=ext_id[:120],
            url=url,
            title=(d.get("title") or "").strip(),
            company_name=(d.get("company_name") or "").strip(),
            company_logo_url=d.get("thumbnail"),
            location_city=city,
            location_province=province,
            work_mode=WorkMode.REMOTE if ext.get("work_from_home") else None,
            employment_type=_employment(schedule),
            salary_min=salary_min,
            salary_max=salary_max,
            salary_currency="IDR" if (salary_min or salary_max) else None,
            description=description or None,
            requirements="\n".join(f"• {x}" for x in quals) or None,
            benefits=benefits,
            posted_at=_posted(ext.get("posted_at")),
            raw={**d, "via": d.get("via"), "chosen_apply": chosen},
            is_complete=bool(description),
        )


def _items(highlights: dict[str, list], *keys: str) -> list[str]:
    for title, items in highlights.items():
        if any(k in title for k in keys):
            return [str(i).strip() for i in items if str(i).strip()]
    return []


def _employment(schedule: str) -> EmploymentType | None:
    if not schedule:
        return None
    if "intern" in schedule or "magang" in schedule:
        return EmploymentType.INTERNSHIP
    if "contract" in schedule or "kontrak" in schedule:
        return EmploymentType.CONTRACT
    if "part" in schedule or "paruh" in schedule:
        return EmploymentType.PARTTIME
    if "full" in schedule or "penuh" in schedule:
        return EmploymentType.FULLTIME
    return None


def _posted(text: str | None) -> datetime | None:
    """'3 hari yang lalu' / '5 days ago' / '12 jam yang lalu' → tanggal."""
    m = _REL.search(text or "")
    if not m:
        return None
    n, unit = int(m.group(1)), m.group(2).lower()
    delta = {"jam": timedelta(hours=n), "hour": timedelta(hours=n), "hari": timedelta(days=n), "day": timedelta(days=n),
             "minggu": timedelta(weeks=n), "week": timedelta(weeks=n), "bulan": timedelta(days=30 * n),
             "month": timedelta(days=30 * n)}[unit]
    return datetime.now(UTC) - delta
