"""Karirhub Kemnaker (karirhub.kemnaker.go.id) — portal lowongan resmi Kementerian Ketenagakerjaan.

Situsnya Next.js: data lowongan ikut terkirim sebagai JSON di payload React Server Components (self.__next_f)
di halaman HTML. Daftar: /lowongan-dalam-negeri/lowongan?page=N (terbaru dulu, 18 per halaman).
Detail: halaman lowongan (deskripsi, kualifikasi, wilayah, pengalaman, usia).
Situs ini belum mendukung pencarian kata kunci lewat URL, jadi yang diambil adalah halaman-halaman terbaru.
"""

import json
import re
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import httpx

from app.connectors.base import Connector, JobDetail, JobIn, SearchPage
from app.models.enums import SourceType
from app.services.parsing import html_to_text, parse_employment_type

WIB = ZoneInfo("Asia/Jakarta")
SITE = "https://karirhub.kemnaker.go.id"
LIST_PATH = "/lowongan-dalam-negeri/lowongan"
_PUSH = re.compile(r'self\.__next_f\.push\(\[1,"(.*?)"\]\)', re.S)
_DECODER = json.JSONDecoder()
_REGION_PREFIX = re.compile(r"^(kota adm\.|kota administrasi|kota|kabupaten|kab\.)\s+", re.I)


def rsc_payload(html: str) -> str:
    """Gabungan payload React Server Components dari halaman Next.js."""
    out = []
    for chunk in _PUSH.findall(html):
        try:
            out.append(json.loads('"' + chunk + '"'))
        except json.JSONDecodeError:
            continue
    return "".join(out)


def parse_list(html: str) -> tuple[list[dict], int | None, dict[str, str]]:
    """(lowongan, total, {id: path halaman detail}) dari halaman daftar."""
    raw = rsc_payload(html)
    total = re.search(r'"perPage":\d+,"to":\d+,"total":(\d+)', raw) or re.search(r'"total":(\d+)', raw)
    items: list[dict] = []
    end = raw.find('"perPage"')
    start = raw.rfind('"data":[', 0, end if end > 0 else len(raw))
    if start >= 0:
        try:
            data, _ = _DECODER.raw_decode(raw[start + len('"data":'):])
            items = [d for d in data if isinstance(d, dict) and d.get("id") and d.get("title")]
        except json.JSONDecodeError:
            items = []
    paths = {}
    for path in re.findall(r'href="(' + re.escape(LIST_PATH) + r'/[^"]+)"', html):
        path = path.replace("&amp;", "&")
        m = re.search(r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$", path)
        if m:
            paths[m.group(1)] = path
    return items, int(total.group(1)) if total else None, paths


def find_vacancy(html: str, vacancy_id: str) -> dict | None:
    """Objek lowongan lengkap di halaman detail."""
    raw = rsc_payload(html)
    for m in re.finditer(r'\{"id":\s*"' + re.escape(vacancy_id) + '"', raw):
        try:
            obj, _ = _DECODER.raw_decode(raw[m.start():])
        except json.JSONDecodeError:
            continue
        if "description" in obj or "qualification" in obj:
            return obj
    return None


def split_region(name: str | None) -> tuple[str | None, str | None]:
    """'Kota Adm. Jakarta Barat, DKI Jakarta' -> ('Jakarta Barat', 'DKI Jakarta')."""
    if not name:
        return None, None
    parts = [p.strip() for p in name.split(",") if p.strip()]
    if not parts:
        return None, None
    city = _REGION_PREFIX.sub("", parts[0]).strip()
    if parts[0].lower().startswith(("kabupaten", "kab.")):
        city = "Kab. " + city
    return city or None, parts[1] if len(parts) > 1 else None


def _epoch(value) -> datetime | None:
    if isinstance(value, (int, float)) and value > 0:
        return datetime.fromtimestamp(value, UTC)
    if isinstance(value, str) and value:
        try:
            dt = datetime.fromisoformat(value)
        except ValueError:
            return None
        return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
    return None


def _salary(d: dict) -> tuple[int | None, int | None]:
    if not d.get("showSalary"):
        return None, None
    lo = d.get("minSalaryAmount") or (d.get("minSalary") or {}).get("amount") or None
    hi = d.get("maxSalaryAmount") or (d.get("maxSalary") or {}).get("amount") or None
    cur = d.get("minSalaryCurrency") or (d.get("minSalary") or {}).get("currency") or "IDR"
    if cur != "IDR":
        return None, None
    return (int(lo) if lo else None), (int(hi) if hi else None)


class KarirhubConnector(Connector):
    name = "karirhub"
    base_url = SITE
    type = SourceType.HTML
    page_size = 18
    max_pages = 12            # ±200 lowongan terbaru per scraping; yang sudah ada dilewati
    supports_detail = True
    detail_delay_seconds = 1.5

    def keywords(self, default: list[str]) -> list[str]:
        return [""]  # pencarian kata kunci belum tersedia lewat URL → ambil lowongan terbaru

    async def search(
        self, client: httpx.AsyncClient, keyword: str, page: int, known: set[str]
    ) -> SearchPage:
        resp = await client.get(SITE + LIST_PATH, params={"page": page}, headers={"Accept": "text/html"})
        resp.raise_for_status()
        rows, total, paths = parse_list(resp.text)
        if not rows and page == 1:
            raise ValueError("data lowongan tidak ditemukan (struktur halaman Karirhub berubah?)")
        items = [self._normalize(d, paths.get(d["id"])) for d in rows]
        has_more = bool(rows) and (total is None or page * self.page_size < total)
        return SearchPage(items, has_more=has_more)

    def _normalize(self, d: dict, path: str | None) -> JobIn:
        lo, hi = _salary(d)
        city = d.get("cityName") or None
        if city:
            city, _ = split_region(city)
        return JobIn(
            external_id=d["id"],
            url=SITE + (path or f"{LIST_PATH}/{d['id']}"),
            title=(d.get("title") or "").strip(),
            company_name=(d.get("companyName") or "").strip() or "Perusahaan dirahasiakan",
            company_logo_url=d.get("companyLogoUri") or None,
            location_city=city,
            employment_type=parse_employment_type(d.get("jobTypeName")),
            salary_min=lo,
            salary_max=hi,
            salary_currency="IDR" if (lo or hi) else None,
            source_category=d.get("jobFunctionName") or d.get("industryName"),
            required_skills=[s for s in (d.get("skills") or []) if isinstance(s, str)][:20],
            posted_at=_epoch(d.get("publishedAt")),
            expires_at=_epoch(d.get("expiresAt")),
            raw=d,
        )

    async def fetch_detail(
        self, client: httpx.AsyncClient, url: str, external_id: str, raw: dict
    ) -> JobDetail | None:
        resp = await client.get(url, headers={"Accept": "text/html"})
        if resp.status_code in (404, 410):
            return JobDetail(closed=True)
        resp.raise_for_status()
        v = find_vacancy(resp.text, external_id)
        if v is None:
            if "Halaman tidak ditemukan" in resp.text:
                return JobDetail(closed=True)
            raise ValueError("data lowongan tidak ditemukan di halaman detail Karirhub")
        city, province = split_region(((v.get("region") or {}).get("name")))
        lo, hi = _salary(v)
        expires = _epoch(v.get("expiresAt"))
        if expires and len(str(v.get("expiresAt"))) <= 10:  # tanggal saja → berlaku sampai 23:59 WIB hari itu
            expires = expires.replace(hour=23, minute=59, second=59, tzinfo=WIB).astimezone(UTC)
        exp = v.get("minYearExperience")
        education = (v.get("minEducation") or {}).get("name") if isinstance(v.get("minEducation"), dict) else None
        return JobDetail(
            description=html_to_text(v.get("description")),
            requirements=html_to_text(v.get("qualification")),
            education=education,
            experience_min=int(exp) if isinstance(exp, (int, float)) else None,
            salary_min=lo,
            salary_max=hi,
            employment_type=parse_employment_type((v.get("jobType") or {}).get("name")),
            location_city=city,
            location_province=province,
            expires_at=expires,
            closed=v.get("status") not in (None, "published"),
        )
