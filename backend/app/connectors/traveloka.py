"""Traveloka Careers — API publik yang dipakai situs careers.traveloka.com + halaman detail (HTML)."""

from datetime import UTC, datetime

import httpx
from selectolax.parser import HTMLParser

from app.connectors.base import Connector, JobDetail, JobIn, SearchPage
from app.models.enums import EmploymentType, SourceType
from app.services.parsing import html_to_text, split_location

API = "https://careers-api.mte.traveloka.com/career/jobs/search"
SITE = "https://careers.traveloka.com"


class TravelokaConnector(Connector):
    name = "traveloka"
    base_url = SITE
    type = SourceType.API
    page_size = 10
    max_pages = 15  # total lowongan Traveloka ±100 → ambil semua, lalu saring yang di Indonesia
    supports_detail = True

    def keywords(self, default: list[str]) -> list[str]:
        return [""]  # tanpa keyword: daftar lengkapnya kecil

    async def search(
        self, client: httpx.AsyncClient, keyword: str, page: int, known: set[str]
    ) -> SearchPage:
        resp = await client.get(API, params={"intf": "desktop", "location": "", "department": "", "type": "",
                                             "keyword": keyword, "page": page})
        resp.raise_for_status()
        data = resp.json().get("data") or {}
        jobs = data.get("jobs") or []
        items = [self._normalize(j) for j in jobs if "indonesia" in (j.get("location") or "").lower()]
        return SearchPage(items, has_more=page < (data.get("lastPage") or 0))

    def _normalize(self, d: dict) -> JobIn:
        city, _ = split_location(d.get("location"))
        created = d.get("createdAt")
        kind = (d.get("employmentType") or "").lower()
        return JobIn(
            external_id=d["requisitionId"],
            url=SITE + (d.get("link") or f"/jobs/{d['requisitionId']}"),
            title=(d.get("title") or "").strip(),
            company_name="Traveloka",
            source_category=d.get("department"),
            location_city=city,
            location_province=None,
            employment_type=(EmploymentType.INTERNSHIP if "intern" in kind else EmploymentType.CONTRACT
                             if "contract" in kind else EmploymentType.FULLTIME if kind else None),
            posted_at=datetime.fromtimestamp(created / 1000, UTC) if created else None,
            description=None,  # isi lengkap di halaman detail
            raw=d,
        )

    async def fetch_detail(
        self, client: httpx.AsyncClient, url: str, external_id: str, raw: dict
    ) -> JobDetail | None:
        resp = await client.get(url, headers={"Accept": "text/html"})
        if resp.status_code == 404:
            return JobDetail(closed=True)
        resp.raise_for_status()
        tree = HTMLParser(resp.text)
        # Isi iklan ada di satu blok rich-text (berisi <ul> daftar tanggung jawab & kualifikasi).
        node = next((n for n in tree.css("div, span, section") if "list-disc" in (n.attributes.get("class") or "")
                     and n.css_first("ul")), None)
        if node is None:
            raise ValueError("blok deskripsi tidak ditemukan (struktur halaman Traveloka berubah?)")
        return JobDetail(description=html_to_text(node.html), requirements=None)
