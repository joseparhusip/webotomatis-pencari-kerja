"""LinkedIn Jobs — halaman lowongan publik (tanpa login) yang juga dipakai linkedin.com/jobs untuk pengunjung.

Daftar: /jobs-guest/jobs/api/seeMoreJobPostings/search (potongan HTML berisi kartu lowongan, 10 per halaman).
Detail: /jobs-guest/jobs/api/jobPosting/{id} (deskripsi lengkap + kriteria: senioritas, jenis pekerjaan, fungsi).
Jumlah halaman dibatasi dan ada jeda antar permintaan supaya tidak membebani situs.
"""

import asyncio
import re
from datetime import UTC, datetime

import httpx
from selectolax.parser import HTMLParser

from app.core.config import get_settings
from app.connectors.base import Connector, JobDetail, JobIn, SearchPage
from app.models.enums import SourceType, WorkMode
from app.services.parsing import html_to_text, localize_place, parse_employment_type, parse_salary_label

SITE = "https://www.linkedin.com"
SEARCH = SITE + "/jobs-guest/jobs/api/seeMoreJobPostings/search"
DETAIL = SITE + "/jobs-guest/jobs/api/jobPosting/{id}"
INDONESIA_GEO_ID = "102478259"
RETRY_429_SECONDS = 30
# Label kriteria dibaca dalam bahasa Inggris (Accept-Language en) supaya stabil.
_EN = {"Accept-Language": "en-US,en;q=0.9", "Accept": "text/html"}
_COUNTRY = {"indonesia"}
_METRO = re.compile(r"\s*(metropolitan area|area|raya)$", re.I)


def _text(node) -> str:
    return re.sub(r"\s+", " ", node.text(strip=True)).strip() if node else ""


def split_linkedin_location(label: str) -> tuple[str | None, str | None, bool]:
    """'Jakarta, Jakarta, Indonesia' -> ('Jakarta', 'Jakarta', False); 'Indonesia (Remote)' -> (None, None, True)."""
    remote = "remote" in label.lower()
    label = re.sub(r"\((remote|hybrid|on-site)\)", "", label, flags=re.I)
    parts = [p.strip() for p in label.split(",") if p.strip() and p.strip().lower() not in _COUNTRY]
    if not parts:
        return None, None, remote
    city = localize_place(_METRO.sub("", parts[0]).strip()) or None
    province = localize_place(parts[1]) if len(parts) > 1 else None
    return city, province, remote


def parse_search_cards(html: str) -> list[dict]:
    """Kartu lowongan dari potongan HTML hasil pencarian LinkedIn."""
    tree = HTMLParser(html)
    cards = []
    for card in tree.css("div.base-search-card"):
        urn = card.attributes.get("data-entity-urn") or ""
        m = re.search(r"jobPosting:(\d+)", urn)
        link = card.css_first("a.base-card__full-link")
        if not m or link is None:
            continue
        img = card.css_first("img.artdeco-entity-image")
        time = card.css_first("time")
        cards.append({
            "id": m.group(1),
            "url": (link.attributes.get("href") or "").split("?")[0],
            "title": _text(card.css_first(".base-search-card__title")),
            "company": _text(card.css_first(".base-search-card__subtitle")),
            "logo": (img.attributes.get("data-delayed-url") if img else None) or None,
            "location": _text(card.css_first(".job-search-card__location")),
            "date": time.attributes.get("datetime") if time else None,
            "salary": _text(card.css_first(".job-search-card__salary-info")) or None,
        })
    return cards


def parse_detail(html: str) -> JobDetail:
    tree = HTMLParser(html)
    criteria = {}
    for item in tree.css("li.description__job-criteria-item"):
        key = _text(item.css_first(".description__job-criteria-subheader")).lower()
        criteria[key] = _text(item.css_first(".description__job-criteria-text"))
    desc = tree.css_first(".show-more-less-html__markup") or tree.css_first(".description__text")
    closed = bool(tree.css_first(".closed-job")) or "no longer accepting applications" in html.lower()
    salary_min, salary_max = None, None
    pay = _text(tree.css_first(".compensation__salary"))
    if pay and ("idr" in pay.lower() or "rp" in pay.lower()):
        salary_min, salary_max = parse_salary_label(pay)
    return JobDetail(
        description=html_to_text(desc.html) if desc else None,
        employment_type=parse_employment_type(criteria.get("employment type")),
        salary_min=salary_min,
        salary_max=salary_max,
        closed=closed,
    )


class LinkedInConnector(Connector):
    name = "linkedin"
    base_url = SITE
    type = SourceType.HTML
    page_size = 10
    max_pages = 3            # ±30 lowongan terbaru per keyword; sisanya terambil di scraping berikutnya
    supports_detail = True
    # LinkedIn membalas 429 bila diakses cepat/berbarengan: satu per satu, jeda 5 dtk (teruji aman).
    detail_delay_seconds = 5.0
    detail_concurrency = 1
    page_delay_seconds = 5.0

    async def _get(self, client: httpx.AsyncClient, url: str, params: dict | None = None) -> httpx.Response:
        """GET dengan satu kali coba ulang setelah 429 (batas sementara LinkedIn)."""
        resp = await client.get(url, headers=_EN, params=params)
        if resp.status_code == 429:
            await asyncio.sleep(RETRY_429_SECONDS)
            resp = await client.get(url, headers=_EN, params=params)
        return resp

    async def search(
        self, client: httpx.AsyncClient, keyword: str, page: int, known: set[str]
    ) -> SearchPage:
        # Rentang waktu = batas umur lowongan di Pengaturan (lowongan lebih lama juga akan dibuang).
        days = max(1, get_settings().scrape_max_age_days)
        resp = await self._get(client, SEARCH, params={
            "keywords": keyword, "location": "Indonesia", "geoId": INDONESIA_GEO_ID,
            "f_TPR": f"r{days * 86400}", "start": (page - 1) * self.page_size,
        })
        if resp.status_code == 400 and page > 1:  # LinkedIn membalas 400 saat halaman habis
            return SearchPage([], has_more=False)
        resp.raise_for_status()
        cards = parse_search_cards(resp.text)
        items = [self._normalize(c) for c in cards if c["title"] and c["company"]]
        return SearchPage(items, has_more=len(cards) >= self.page_size)

    def _normalize(self, c: dict) -> JobIn:
        city, province, remote = split_linkedin_location(c["location"])
        salary_min = salary_max = None
        if c["salary"] and ("idr" in c["salary"].lower() or "rp" in c["salary"].lower()):
            salary_min, salary_max = parse_salary_label(c["salary"])
        posted = None
        if c["date"]:
            try:
                posted = datetime.fromisoformat(c["date"]).replace(tzinfo=UTC)
            except ValueError:
                posted = None
        return JobIn(
            external_id=c["id"],
            url=c["url"] or f"{SITE}/jobs/view/{c['id']}",
            title=c["title"],
            company_name=c["company"],
            company_logo_url=c["logo"],
            location_city=city,
            location_province=province,
            work_mode=WorkMode.REMOTE if remote else None,
            salary_min=salary_min,
            salary_max=salary_max,
            salary_currency="IDR" if (salary_min or salary_max) else None,
            posted_at=posted,
            raw=c,
        )

    async def fetch_detail(
        self, client: httpx.AsyncClient, url: str, external_id: str, raw: dict
    ) -> JobDetail | None:
        resp = await self._get(client, DETAIL.format(id=external_id))
        if resp.status_code in (404, 410):
            return JobDetail(closed=True)
        resp.raise_for_status()
        return parse_detail(resp.text)
