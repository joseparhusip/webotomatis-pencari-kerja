import logging
import re
from urllib.parse import urljoin

import httpx

from app.connectors.base import Connector, JobDetail, JobIn, SearchPage
from app.connectors.jsonld import fetch_jsonld_detail, find_jobposting, jobposting_to_jobin
from app.models.enums import SourceType

log = logging.getLogger(__name__)
_DETAIL_HREF = re.compile(r'href="(/lowongan/detail/[^"?#]+)"')


class KitaLulusConnector(Connector):
    """kitalulus.com — halaman pencarian hanya merender beberapa lowongan teratas di server
    (sisanya dimuat JavaScript), jadi hasil per keyword terbatas. Detail dari JSON-LD JobPosting.
    """

    name = "kitalulus"
    base_url = "https://www.kitalulus.com"
    type = SourceType.HTML
    supports_detail = True
    max_pages = 1

    async def fetch_detail(
        self, client: httpx.AsyncClient, url: str, external_id: str, raw: dict
    ) -> JobDetail | None:
        return await fetch_jsonld_detail(client, url, external_id)

    async def search(
        self, client: httpx.AsyncClient, keyword: str, page: int, known: set[str]
    ) -> SearchPage:
        resp = await client.get(f"{self.base_url}/lowongan", params={"keyword": keyword},
                                headers={"Accept": "text/html"})
        resp.raise_for_status()
        paths = list(dict.fromkeys(_DETAIL_HREF.findall(resp.text)))

        items: list[JobIn] = []
        for path in paths:
            ext_id = path.rsplit("/", 1)[-1]
            if ext_id in known:
                continue
            job = await self._detail(client, urljoin(self.base_url, path), ext_id)
            if job:
                items.append(job)
            await self.pause()
        return SearchPage(items, has_more=False)

    async def _detail(self, client: httpx.AsyncClient, url: str, ext_id: str) -> JobIn | None:
        try:
            resp = await client.get(url, headers={"Accept": "text/html"})
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            log.warning("kitalulus detail gagal %s: %s", url, exc)
            return None
        posting = find_jobposting(resp.text)
        return jobposting_to_jobin(posting, external_id=ext_id, url=url) if posting else None

