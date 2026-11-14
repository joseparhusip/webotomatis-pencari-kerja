import json
import re
from datetime import datetime

import httpx

from app.connectors.base import Connector, JobDetail, JobIn, SearchPage
from app.models.enums import EmploymentType, WorkMode
from app.services.parsing import html_to_text

_MODES = {"onSite": WorkMode.ONSITE, "remote": WorkMode.REMOTE, "hybrid": WorkMode.HYBRID}
_TYPES = {
    "fullTime": EmploymentType.FULLTIME,
    "contract": EmploymentType.CONTRACT,
    "internship": EmploymentType.INTERNSHIP,
    "partTime": EmploymentType.PARTTIME,
    "freelance": EmploymentType.FREELANCE,
}
# Field yang tidak disimpan: data pribadi recruiter & statistik internal.
_DROP = {"author", "authorId", "candidatePreference", "stats"}
_NEXT_DATA = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)


class DeallsConnector(Connector):
    """dealls.com — API publik yang dipakai situs Dealls (api.sejutacita.id) + halaman detail."""

    name = "dealls"
    base_url = "https://dealls.com"
    api_url = "https://api.sejutacita.id/v1/explore-job/job"
    supports_detail = True

    async def search(
        self, client: httpx.AsyncClient, keyword: str, page: int, known: set[str]
    ) -> SearchPage:
        resp = await client.get(
            self.api_url,
            params={
                "page": page,
                "limit": self.page_size,
                "sortParam": "publishedAt",
                "sortBy": "desc",
                "published": "true",
                "search": keyword,
            },
        )
        resp.raise_for_status()
        data = resp.json().get("data") or {}
        docs = [d for d in data.get("docs", []) if (d.get("country") or {}).get("name") in (None, "Indonesia")]
        return SearchPage(
            [self._normalize(d) for d in docs if d.get("status", "active") == "active"],
            has_more=page < (data.get("totalPages") or 0),
        )

    def _page_url(self, d: dict) -> str:
        return f"{self.base_url}/loker/{d.get('slug')}~{(d.get('company') or {}).get('slug', '')}"

    def _normalize(self, d: dict) -> JobIn:
        company = d.get("company") or {}
        salary = d.get("salaryRange") or {}
        types = d.get("employmentTypes") or []
        return JobIn(
            external_id=d["id"],
            url=d.get("externalPlatformApplyUrl") or self._page_url(d),
            title=(d.get("role") or "").strip(),
            company_name=(company.get("name") or "").strip(),
            company_logo_url=company.get("logoUrl"),
            source_category=(d.get("jobRoleCategorySlug") or "").replace("-", " ") or None,
            location_city=(d.get("city") or {}).get("name"),
            work_mode=_MODES.get(d.get("workplaceType") or ""),
            employment_type=next((_TYPES[t] for t in types if t in _TYPES), None),
            salary_min=salary.get("start"),
            salary_max=salary.get("end"),
            salary_currency="IDR" if (salary.get("start") or salary.get("end")) else None,
            # Skill dari API pencarian tidak ditampilkan di halaman lowongan Dealls → tidak disimpan,
            # supaya isi lowongan sama dengan situs aslinya.
            description=None,  # hanya ada di halaman detail
            posted_at=_dt(d.get("publishedAt")),
            raw={k: v for k, v in d.items() if k not in _DROP},
        )

    async def fetch_detail(
        self, client: httpx.AsyncClient, url: str, external_id: str, raw: dict
    ) -> JobDetail | None:
        resp = await client.get(self._page_url(raw), headers={"Accept": "text/html"})
        if resp.status_code == 404:
            return JobDetail(closed=True)
        resp.raise_for_status()
        m = _NEXT_DATA.search(resp.text)
        if not m:
            raise ValueError("__NEXT_DATA__ tidak ditemukan (struktur halaman Dealls berubah?)")
        queries = ((((json.loads(m.group(1)).get("props") or {}).get("pageProps") or {})
                    .get("dehydratedState") or {}).get("queries") or [])
        job = None
        for q in queries:
            key = q.get("queryKey") or []
            if key and key[0] == "/v1/job-portal/job/slug":
                data = (q.get("state") or {}).get("data") or {}
                job = data.get("data", data) if isinstance(data, dict) else None
                break
        if not isinstance(job, dict) or job.get("id") != external_id:
            raise ValueError("data lowongan tidak ditemukan di halaman detail Dealls")

        salary = job.get("salaryRange") or {}
        parts = [html_to_text(job.get("description")), html_to_text(job.get("responsibilities"))]
        description = "\n\n".join(p for p in parts if p) or None
        benefits = job.get("benefits")
        return JobDetail(
            description=description,
            requirements=html_to_text(job.get("requirements")),
            benefits=[str(b) for b in benefits] if isinstance(benefits, list) else [],
            salary_min=salary.get("start"),
            salary_max=salary.get("end"),
            work_mode=_MODES.get(job.get("workplaceType") or ""),
            closed=bool(job.get("closed")) or job.get("status") not in (None, "active"),
        )


def _dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None
