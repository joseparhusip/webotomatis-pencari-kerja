import json
import re
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import httpx

from app.connectors.base import Connector, JobDetail, JobIn, SearchPage
from app.models.enums import EmploymentType, ExperienceLevel, SourceType, WorkMode
from app.services.parsing import education_label, render_draftjs

_NEXT_DATA = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)
_SLUG = re.compile(r"[^a-z0-9]+")
LOGO_BASE = "https://images.glints.com/unsafe/glints-dashboard.s3.amazonaws.com/company-logo/"

_MODES = {"ONSITE": WorkMode.ONSITE, "HYBRID": WorkMode.HYBRID, "REMOTE": WorkMode.REMOTE}
_TYPES = {
    "FULL_TIME": EmploymentType.FULLTIME,
    "PART_TIME": EmploymentType.PARTTIME,
    "CONTRACT": EmploymentType.CONTRACT,
    "INTERNSHIP": EmploymentType.INTERNSHIP,
    "FREELANCE": EmploymentType.FREELANCE,
    "DAILY": EmploymentType.FREELANCE,
}


class GlintsConnector(Connector):
    """glints.com — data lowongan ada di __NEXT_DATA__ (halaman pencarian & halaman detail).

    Halaman pencarian hanya memuat ringkasan (tanpa deskripsi); deskripsi, benefit, pendidikan
    diambil dari halaman detail lewat fetch_detail.
    """

    name = "glints"
    base_url = "https://glints.com"
    type = SourceType.HTML
    max_pages = 1
    supports_detail = True

    async def search(
        self, client: httpx.AsyncClient, keyword: str, page: int, known: set[str]
    ) -> SearchPage:
        resp = await client.get(
            f"{self.base_url}/id/opportunities/jobs/explore",
            # Terbaru dulu: tanpa ini halaman pertama berisi lowongan lama yang lalu tersaring aturan 3 hari.
            params={"keyword": keyword, "country": "ID", "locationName": "All Cities/Provinces", "sortBy": "LATEST"},
            headers={"Accept": "text/html"},
        )
        resp.raise_for_status()
        data = _next_data(resp.text)
        jobs = (((data.get("props") or {}).get("pageProps") or {}).get("initialJobs") or {}).get("jobsInPage") or []
        items = [self._normalize(j) for j in jobs if ((j.get("country") or {}).get("code") in (None, "ID"))]
        return SearchPage(items, has_more=False)

    def _normalize(self, d: dict) -> JobIn:
        company = d.get("company") or {}
        city, province = _location(d)
        salary_min, salary_max = _salary(d)
        slug = _SLUG.sub("-", (d.get("title") or "lowongan").lower()).strip("-")
        logo = company.get("logo")
        return JobIn(
            external_id=d["id"],
            url=f"{self.base_url}/id/opportunities/jobs/{slug}/{d['id']}",
            title=(d.get("title") or "").strip(),
            company_name=(company.get("brandName") or company.get("name") or "").strip(),
            company_logo_url=(LOGO_BASE + logo) if logo else None,
            location_city=city,
            location_province=province,
            work_mode=_MODES.get(d.get("workArrangementOption") or ""),
            employment_type=_TYPES.get(d.get("type") or ""),
            experience_level=_level(d.get("minYearsOfExperience")),
            experience_min=d.get("minYearsOfExperience"),
            experience_max=d.get("maxYearsOfExperience"),
            education=education_label(d.get("educationLevel")),
            required_skills=_skills(d),
            source_category=(d.get("hierarchicalJobCategory") or {}).get("name"),
            salary_min=salary_min,
            salary_max=salary_max,
            salary_currency="IDR" if (salary_min or salary_max) else None,
            description=None,  # hanya ada di halaman detail
            # Glints menampilkan & mengurutkan berdasarkan waktu diperbarui.
            posted_at=_dt(d.get("updatedAt") or d.get("createdAt")),
            raw={k: v for k, v in d.items() if k != "traceInfo"},
        )

    async def fetch_detail(
        self, client: httpx.AsyncClient, url: str, external_id: str, raw: dict
    ) -> JobDetail | None:
        resp = await client.get(url, headers={"Accept": "text/html"})
        if resp.status_code == 404:
            return JobDetail(closed=True)
        resp.raise_for_status()
        data = ((_next_data(resp.text).get("props") or {}).get("pageProps") or {}).get("initialData") or {}
        job = data.get("data")
        if not isinstance(job, dict) or job.get("id") != external_id:
            raise ValueError("data lowongan tidak ditemukan di halaman detail Glints")

        closed = (job.get("status") not in (None, "OPEN")) or bool(job.get("closedAt"))
        expiry = _date(job.get("expiryDate"))
        city, province = _location(job)
        salary_min, salary_max = _salary(job)
        return JobDetail(
            description=render_draftjs(job.get("descriptionJsonString")),
            requirements=None,  # Glints menulis persyaratan di dalam deskripsi
            required_skills=_skills(job),
            benefits=[b.get("title") for b in job.get("benefits") or [] if b.get("title")]
            if job.get("shouldShowBenefits", True) else [],
            education=education_label(job.get("educationLevel")),
            experience_min=job.get("minYearsOfExperience"),
            experience_max=job.get("maxYearsOfExperience"),
            salary_min=salary_min,
            salary_max=salary_max,
            work_mode=_MODES.get(job.get("workArrangementOption") or ""),
            employment_type=_TYPES.get(job.get("type") or ""),
            location_city=city,
            location_province=province,
            posted_at=_dt(job.get("createdAt")),
            expires_at=expiry,
            closed=closed or bool(expiry and expiry < datetime.now(UTC)),
        )


def _next_data(html: str) -> dict:
    m = _NEXT_DATA.search(html)
    if not m:
        raise ValueError("__NEXT_DATA__ tidak ditemukan (struktur halaman Glints berubah?)")
    return json.loads(m.group(1))


def _skills(d: dict) -> list[str]:
    out = []
    for s in d.get("skills") or []:
        if isinstance(s, dict):
            name = (s.get("skill") or {}).get("name") or s.get("name")
            if name:
                out.append(name)
    return out


def _salary(d: dict) -> tuple[int | None, int | None]:
    if not d.get("shouldShowSalary"):
        return None, None
    for s in d.get("salaries") or []:
        if s.get("salaryMode") == "MONTH" and s.get("CurrencyCode", "IDR") == "IDR":
            return s.get("minAmount"), s.get("maxAmount")
    return None, None


def _location(d: dict) -> tuple[str | None, str | None]:
    """Lokasi Glints berjenjang: kecamatan → kota → provinsi."""
    loc = d.get("location") or {}
    chain = [loc]
    node = loc
    while (parents := node.get("parents")) and isinstance(parents, list):
        node = parents[0]
        chain.append(node)
    city = next((n.get("formattedName") or n.get("name") for n in chain
                 if n.get("administrativeLevelName") == "City" or n.get("level") == 3), None)
    province = next((n.get("formattedName") or n.get("name") for n in chain if n.get("level") == 2), None)
    if not city:
        city = (d.get("city") or {}).get("name")
    return city, province


def _level(min_years) -> ExperienceLevel | None:
    if min_years is None:
        return None
    if min_years >= 5:
        return ExperienceLevel.SENIOR
    if min_years >= 3:
        return ExperienceLevel.MID
    if min_years >= 1:
        return ExperienceLevel.JUNIOR
    return ExperienceLevel.ENTRY


def _dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None


def _date(value: str | None) -> datetime | None:
    """'2026-12-11' → akhir hari itu (lowongan masih berlaku sepanjang tanggal tersebut)."""
    if not value:
        return None
    try:
        d = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if d.tzinfo is None:  # tanggal Indonesia → berlaku sampai 23:59 WIB
        d = d.replace(hour=23, minute=59, second=59, tzinfo=ZoneInfo("Asia/Jakarta")).astimezone(UTC)
    return d
