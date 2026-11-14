import json
import re
from datetime import UTC, datetime

import httpx

from app.core.config import get_settings
from app.connectors.base import Connector, JobDetail, JobIn, SearchPage
from app.models.enums import WorkMode
from app.services.parsing import html_to_text, parse_employment_type, parse_salary_label, split_location

_REDUX = re.compile(r"window\.SEEK_REDUX_DATA\s*=\s*(\{.*?\});\s*\n", re.S)


class JobStreetConnector(Connector):
    """id.jobstreet.com — memakai endpoint JSON pencarian yang dipakai situsnya sendiri."""

    name = "jobstreet"
    base_url = "https://id.jobstreet.com"
    supports_detail = True
    max_pages = 30           # kategori IT diambil sampai habis (100/halaman); keyword dibatasi 2 halaman
    IT_CLASSIFICATION = "6281"  # "Teknologi Informasi & Komunikasi"
    ALL_IT = "@kategori-it"

    def keywords(self, default: list[str]) -> list[str]:
        # Semua lowongan di kategori IT (apa pun judulnya) + keyword untuk IT yang dipasang di kategori lain.
        return [self.ALL_IT] + default

    async def search(
        self, client: httpx.AsyncClient, keyword: str, page: int, known: set[str]
    ) -> SearchPage:
        by_category = keyword == self.ALL_IT
        params = {
            "siteKey": "ID-Main",
            "sourcesystem": "houston",
            "page": page,
            "pageSize": 100 if by_category else self.page_size,
            "locale": "id-ID",
            "sortmode": "ListedDate",
            "daterange": get_settings().scrape_max_age_days,
        }
        if by_category:
            params["classification"] = self.IT_CLASSIFICATION
        else:
            params["keywords"] = keyword
        resp = await client.get(f"{self.base_url}/api/jobsearch/v5/search", params=params)
        resp.raise_for_status()
        data = resp.json().get("data", [])
        full = len(data) >= params["pageSize"]
        return SearchPage([self._normalize(item) for item in data],
                          has_more=full and (by_category or page < 2))

    async def fetch_detail(
        self, client: httpx.AsyncClient, url: str, external_id: str, raw: dict
    ) -> JobDetail | None:
        resp = await client.get(f"{self.base_url}/id/job/{external_id}", headers={"Accept": "text/html"})
        if resp.status_code in (404, 410):
            return JobDetail(closed=True)
        resp.raise_for_status()
        m = _REDUX.search(resp.text)
        if not m:
            raise ValueError("SEEK_REDUX_DATA tidak ditemukan (struktur halaman JobStreet berubah?)")
        result = ((json.loads(m.group(1)).get("jobdetails") or {}).get("result") or {})
        job = result.get("job")
        if not isinstance(job, dict) or str(job.get("id")) != str(external_id):
            return JobDetail(closed=True)  # halaman tanpa data lowongan = sudah dihapus pengiklan

        salary_min, salary_max = parse_salary_label((job.get("salary") or {}).get("label"))
        city, province = split_location((job.get("location") or {}).get("label"))
        expires = _parse_dt((job.get("expiresAt") or {}).get("dateTimeUtc"))
        return JobDetail(
            description=html_to_text(job.get("content")),
            requirements=None,  # JobStreet menulis persyaratan di dalam konten iklan
            salary_min=salary_min,
            salary_max=salary_max,
            employment_type=parse_employment_type((job.get("workTypes") or {}).get("label")),
            work_mode=_work_mode(job.get("workArrangements")),
            location_city=city,
            location_province=province,
            posted_at=_parse_dt((job.get("listedAt") or {}).get("dateTimeUtc")),
            expires_at=expires,
            closed=bool(job.get("isExpired")) or bool(expires and expires < datetime.now(UTC)),
        )

    def _normalize(self, d: dict) -> JobIn:
        job_id = str(d["id"])
        loc = (d.get("locations") or [{}])[0].get("label")
        city, province = split_location(loc)
        salary_min, salary_max = parse_salary_label(d.get("salaryLabel"))

        description_parts = [d.get("teaser") or ""] + [f"• {b}" for b in d.get("bulletPoints") or []]
        return JobIn(
            external_id=job_id,
            url=f"{self.base_url}/id/job/{job_id}",
            title=d.get("title", "").strip(),
            # Nama yang ditampilkan JobStreet = nama pengiklan (mis. "PT X(Brand Y)").
            company_name=((d.get("advertiser") or {}).get("description") or d.get("companyName") or "").strip(),
            source_category=_classification(d),
            company_logo_url=(d.get("branding") or {}).get("serpLogoUrl"),
            location_city=city,
            location_province=province,
            work_mode=_work_mode(d.get("workArrangements")),
            employment_type=parse_employment_type(" ".join(d.get("workTypes") or [])),
            salary_min=salary_min,
            salary_max=salary_max,
            salary_currency="IDR" if salary_min or salary_max else None,
            description="\n".join(p for p in description_parts if p).strip() or None,
            posted_at=_parse_dt(d.get("listingDate")),
            raw=d,
        )


def _classification(d: dict) -> str | None:
    """'Teknologi Informasi & Komunikasi / Developer/Programmer'."""
    for c in d.get("classifications") or []:
        main = (c.get("classification") or {}).get("description")
        sub = (c.get("subclassification") or {}).get("description")
        if main:
            return f"{main} / {sub}" if sub else main
    return None


def _work_mode(arrangements: dict | None) -> WorkMode | None:
    labels = " ".join(
        (a.get("label") or {}).get("text", "") for a in (arrangements or {}).get("data", [])
    ).lower()
    if not labels:
        return None
    if "remote" in labels or "jarak jauh" in labels:
        return WorkMode.REMOTE
    if "hybrid" in labels:
        return WorkMode.HYBRID
    return WorkMode.ONSITE


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))
