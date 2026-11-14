"""Parser schema.org JobPosting (JSON-LD) — dipakai Loker.id, KitaLulus, dan situs lain yang menyediakannya."""

import json
import re

import httpx
from zoneinfo import ZoneInfo
from datetime import UTC, datetime

from app.connectors.base import JobDetail, JobIn
from app.models.enums import EmploymentType, ExperienceLevel
from app.services.parsing import education_label, html_to_text, split_csv

_LD = re.compile(r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>', re.S)

_EMPLOYMENT = {
    "FULL_TIME": EmploymentType.FULLTIME,
    "PART_TIME": EmploymentType.PARTTIME,
    "CONTRACTOR": EmploymentType.CONTRACT,
    "TEMPORARY": EmploymentType.CONTRACT,
    "INTERN": EmploymentType.INTERNSHIP,
    "PER_DIEM": EmploymentType.FREELANCE,
}


def iter_jsonld(html: str):
    for m in _LD.finditer(html):
        try:
            data = json.loads(m.group(1))
        except json.JSONDecodeError:
            continue
        for node in data if isinstance(data, list) else [data]:
            if isinstance(node, dict):
                yield node
                yield from (n for n in node.get("@graph", []) if isinstance(n, dict))


def find_jobposting(html: str) -> dict | None:
    return next((n for n in iter_jsonld(html) if n.get("@type") == "JobPosting"), None)


def item_list_urls(html: str) -> list[str]:
    """URL dari schema.org ItemList (halaman hasil pencarian)."""
    for node in iter_jsonld(html):
        entity = node.get("mainEntity") if node.get("@type") == "CollectionPage" else node
        if isinstance(entity, dict) and entity.get("@type") == "ItemList":
            return [i["url"] for i in entity.get("itemListElement", []) if i.get("url")]
    return []


WIB = ZoneInfo("Asia/Jakarta")


def _dt(value: str | None, end_of_day: bool = False) -> datetime | None:
    """Tanggal tanpa jam ('2026-12-11') dibaca sebagai tanggal di Indonesia (WIB).
    Untuk batas lamar (end_of_day) berarti masih berlaku sampai 23:59 WIB hari itu."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo:
        return dt
    if end_of_day and len(value) <= 10:
        dt = dt.replace(hour=23, minute=59, second=59)
    return dt.replace(tzinfo=WIB).astimezone(UTC)


def _employment(value) -> EmploymentType | None:
    values = value if isinstance(value, list) else [value]
    for v in values:
        if isinstance(v, str) and v.upper() in _EMPLOYMENT:
            return _EMPLOYMENT[v.upper()]
    return None


def _level_from_months(req) -> ExperienceLevel | None:
    months = (req or {}).get("monthsOfExperience") if isinstance(req, dict) else None
    if months is None:
        return None
    years = int(months) / 12
    if years >= 5:
        return ExperienceLevel.SENIOR
    if years >= 3:
        return ExperienceLevel.MID
    if years >= 1:
        return ExperienceLevel.JUNIOR
    return ExperienceLevel.ENTRY


def _salary(base) -> tuple[int | None, int | None]:
    value = (base or {}).get("value") if isinstance(base, dict) else None
    if not isinstance(value, dict):
        return None, None
    unit = (value.get("unitText") or "MONTH").upper()
    lo, hi = value.get("minValue") or value.get("value"), value.get("maxValue") or value.get("value")
    try:
        lo, hi = (int(float(lo)) if lo else None), (int(float(hi)) if hi else None)
    except (TypeError, ValueError):
        return None, None
    if unit == "YEAR":
        lo, hi = (lo // 12 if lo else None), (hi // 12 if hi else None)
    elif unit != "MONTH":
        return None, None
    return lo, hi


def jobposting_to_jobin(d: dict, *, external_id: str, url: str) -> JobIn:
    org = d.get("hiringOrganization") or {}
    loc = d.get("jobLocation") or {}
    if isinstance(loc, list):
        loc = loc[0] if loc else {}
    addr = loc.get("address") or {}
    salary_min, salary_max = _salary(d.get("baseSalary"))
    logo = org.get("logo")
    if isinstance(logo, dict):
        logo = logo.get("url")

    requirements = html_to_text(d.get("qualifications"))
    edu = d.get("educationRequirements")
    description = "\n\n".join(
        filter(None, [html_to_text(d.get("description")), html_to_text(d.get("responsibilities"))])
    )
    return JobIn(
        external_id=external_id,
        url=url,
        title=(d.get("title") or "").strip(),
        company_name=(org.get("name") or "").strip(),
        company_logo_url=logo if isinstance(logo, str) else None,
        location_city=addr.get("addressLocality"),
        location_province=addr.get("addressRegion"),
        employment_type=_employment(d.get("employmentType")),
        experience_level=_level_from_months(d.get("experienceRequirements")),
        salary_min=salary_min,
        salary_max=salary_max,
        salary_currency=((d.get("baseSalary") or {}).get("currency") or "IDR") if (salary_min or salary_max) else None,
        description=description or None,
        requirements=requirements or None,
        required_skills=_list(d.get("skills")),
        benefits=_list(d.get("jobBenefits")),
        education=education_label(edu.get("credentialCategory") if isinstance(edu, dict) else None),
        experience_min=_years(d.get("experienceRequirements")),
        is_complete=True,
        source_category=(d.get("occupationalCategory") if isinstance(d.get("occupationalCategory"), str) else None),
        posted_at=_dt(d.get("datePosted")),
        expires_at=_dt(d.get("validThrough"), end_of_day=True),
        raw=d,
    )


def _list(value) -> list[str]:
    """Daftar pendek dipisah koma (mis. "Go, PostgreSQL"). Teks paragraf/bernomor ditolak:
    beberapa situs (Loker.id) mengisi kolom ini dengan salinan kualifikasi, yang sudah tampil terpisah."""
    if isinstance(value, list):
        items = [str(v).strip() for v in value if str(v).strip()]
    else:
        if isinstance(value, str) and "<" in value:
            value = html_to_text(value)
        if not value or "\n" in value:
            return []
        items = split_csv(value)
    if any(len(i) > 40 for i in items):
        return []
    return items


def _years(req) -> int | None:
    months = req.get("monthsOfExperience") if isinstance(req, dict) else None
    try:
        return int(months) // 12 if months is not None else None
    except (TypeError, ValueError):
        return None


def jobin_to_detail(job: JobIn) -> JobDetail:
    """Hasil JSON-LD halaman detail → JobDetail (untuk pembaruan berkala)."""
    from datetime import UTC, datetime

    return JobDetail(
        description=job.description,
        requirements=job.requirements,
        required_skills=job.required_skills,
        benefits=job.benefits,
        education=job.education,
        experience_min=job.experience_min,
        salary_min=job.salary_min,
        salary_max=job.salary_max,
        employment_type=job.employment_type,
        location_city=job.location_city,
        location_province=job.location_province,
        posted_at=job.posted_at,
        expires_at=job.expires_at,
        closed=bool(job.expires_at and job.expires_at < datetime.now(UTC)),
    )


async def fetch_jsonld_detail(client: httpx.AsyncClient, url: str, external_id: str) -> JobDetail:
    """Halaman detail berbasis JSON-LD (Loker.id, KitaLulus): ambil ulang untuk deteksi edit / penutupan."""
    resp = await client.get(url, headers={"Accept": "text/html"})
    if resp.status_code in (404, 410):
        return JobDetail(closed=True)
    resp.raise_for_status()
    posting = find_jobposting(resp.text)
    if posting is None:
        return JobDetail(closed=True)  # halaman tidak lagi memuat lowongan
    return jobin_to_detail(jobposting_to_jobin(posting, external_id=external_id, url=url))
