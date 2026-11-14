import logging
import re

import httpx

from app.connectors.base import Connector, JobDetail, JobIn, SearchPage
from app.connectors.jsonld import fetch_jsonld_detail, find_jobposting, item_list_urls, jobposting_to_jobin
from app.models.enums import SourceType

log = logging.getLogger(__name__)
_SLUG_ID = re.compile(r"/([^/]+)\.html$")


class LokerIdConnector(Connector):
    """loker.id — daftar dari ItemList di halaman pencarian, detail dari JSON-LD JobPosting."""

    name = "lokerid"
    base_url = "https://www.loker.id"
    type = SourceType.HTML
    supports_detail = True

    async def fetch_detail(
        self, client: httpx.AsyncClient, url: str, external_id: str, raw: dict
    ) -> JobDetail | None:
        return await fetch_jsonld_detail(client, url, external_id)

    async def search(
        self, client: httpx.AsyncClient, keyword: str, page: int, known: set[str]
    ) -> SearchPage:
        path = "/cari-lowongan-kerja" if page == 1 else f"/cari-lowongan-kerja/page/{page}"
        resp = await client.get(self.base_url + path, params={"q": keyword}, headers={"Accept": "text/html"})
        resp.raise_for_status()
        urls = item_list_urls(resp.text)

        items: list[JobIn] = []
        for url in urls:
            ext_id = _external_id(url)
            if ext_id in known:
                continue
            job = await self._detail(client, url, ext_id)
            if job:
                items.append(job)
            await self.pause()
        return SearchPage(items, has_more=len(urls) >= 10)

    async def _detail(self, client: httpx.AsyncClient, url: str, ext_id: str) -> JobIn | None:
        try:
            resp = await client.get(url, headers={"Accept": "text/html"})
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            log.warning("lokerid detail gagal %s: %s", url, exc)
            return None
        posting = find_jobposting(resp.text)
        return jobposting_to_jobin(posting, external_id=ext_id, url=url) if posting else None


def _external_id(url: str) -> str:
    m = _SLUG_ID.search(url)
    return m.group(1) if m else url

