"""Instagram akun info loker — lewat Meta Graph API (Business Discovery), bukan scraping.

Business Discovery bisa membaca postingan publik akun Instagram BISNIS/KREATOR lain, memakai akun
Instagram bisnis milik pengguna sendiri (INSTAGRAM_BUSINESS_ID) dan token Meta (META_ACCESS_TOKEN).
Isi postingan (gambar + caption) dibaca Gemini menjadi lowongan terstruktur.
"""

import logging
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import select

from app.connectors.base import Connector, JobIn, SearchPage
from app.core.config import get_settings
from app.core.db import SessionLocal
from app.models import InstagramAccount, InstagramPost
from app.models.enums import EmploymentType, SourceType, WorkMode
from app.services.ig_parse import ParseError, parse_post

log = logging.getLogger(__name__)
WIB = ZoneInfo("Asia/Jakarta")
_MODES = {"onsite": WorkMode.ONSITE, "remote": WorkMode.REMOTE, "hybrid": WorkMode.HYBRID}
_TYPES = {"fulltime": EmploymentType.FULLTIME, "contract": EmploymentType.CONTRACT,
          "internship": EmploymentType.INTERNSHIP, "freelance": EmploymentType.FREELANCE,
          "parttime": EmploymentType.PARTTIME}
MEDIA_FIELDS = "id,caption,media_type,media_url,thumbnail_url,permalink,timestamp,children{media_type,media_url,thumbnail_url}"


class GraphError(Exception):
    pass


class InstagramConnector(Connector):
    name = "instagram"
    base_url = "https://www.instagram.com"
    type = SourceType.API
    max_pages = 1
    detail_delay_seconds = 1.0

    def disabled_reason(self) -> str | None:
        s = get_settings()
        if not (s.meta_access_token and s.instagram_business_id):
            return "META_ACCESS_TOKEN & INSTAGRAM_BUSINESS_ID belum diisi"
        if not s.gemini_api_key:
            return "GEMINI_API_KEY dibutuhkan untuk membaca gambar loker"
        return None

    async def targets(self, session, default: list[str]) -> list[str]:
        rows = await session.scalars(select(InstagramAccount.username).where(InstagramAccount.is_active))
        return list(rows)

    async def search(
        self, client: httpx.AsyncClient, username: str, page: int, known: set[str]
    ) -> SearchPage:
        s = get_settings()
        try:
            media = await self._recent_media(client, username)
        except GraphError as exc:
            await _mark_account(username, error=str(exc))
            raise ValueError(f"@{username}: {exc}") from exc

        since = datetime.now(UTC) - timedelta(days=s.instagram_max_age_days)
        fresh = [m for m in media if (ts := _ts(m.get("timestamp"))) and ts >= since]
        async with SessionLocal() as session:
            done = set(await session.scalars(
                select(InstagramPost.media_id).where(InstagramPost.media_id.in_([m["id"] for m in fresh] or [""]))
            ))
        items: list[JobIn] = []
        for m in fresh:
            if m["id"] in done:
                continue
            jobs, note = await self._read_post(client, username, m)
            items += jobs
            await _record_post(username, m, len(jobs), note)
            await self.pause()
        await _mark_account(username, found=len(items))
        return SearchPage(items, has_more=False)

    async def _recent_media(self, client: httpx.AsyncClient, username: str) -> list[dict]:
        s = get_settings()
        fields = (f"business_discovery.username({username})"
                  f"{{username,name,media.limit({s.instagram_posts_per_account}){{{MEDIA_FIELDS}}}}}")
        resp = await client.get(
            f"https://graph.facebook.com/{s.meta_graph_version}/{s.instagram_business_id}",
            params={"fields": fields, "access_token": s.meta_access_token},
        )
        data = resp.json()
        if "error" in data:
            err = data["error"]
            raise GraphError(f"{err.get('message', 'error Graph API')} (kode {err.get('code')})")
        resp.raise_for_status()
        return (((data.get("business_discovery") or {}).get("media") or {}).get("data")) or []

    async def _read_post(self, client: httpx.AsyncClient, username: str, m: dict) -> tuple[list[JobIn], str | None]:
        images = []
        for url in _image_urls(m):
            try:
                r = await client.get(url)
                r.raise_for_status()
                images.append((r.content, r.headers.get("content-type", "image/jpeg").split(";")[0]))
            except httpx.HTTPError as exc:
                log.warning("gambar @%s %s gagal: %s", username, m.get("id"), exc)
        try:
            parsed = await parse_post(m.get("caption") or "", images, username)
        except (ParseError, Exception) as exc:  # jangan sampai satu postingan menghentikan akun lain
            return [], f"gagal dibaca: {type(exc).__name__}: {str(exc)[:200]}"
        if not parsed.get("is_job_post"):
            return [], "bukan lowongan"
        jobs = []
        for i, j in enumerate(parsed["jobs"]):
            job = _to_jobin(j, m, username, i)
            if job:
                jobs.append(job)
        return jobs, None if jobs else "lowongan tanpa nama perusahaan/posisi — dilewati"


def _image_urls(m: dict) -> list[str]:
    kind = m.get("media_type")
    if kind == "CAROUSEL_ALBUM":
        kids = ((m.get("children") or {}).get("data")) or []
        return [k.get("media_url") if k.get("media_type") == "IMAGE" else k.get("thumbnail_url")
                for k in kids[:4] if k.get("media_url") or k.get("thumbnail_url")]
    url = m.get("thumbnail_url") if kind == "VIDEO" else m.get("media_url")
    return [url] if url else []


def _ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S%z")
    except ValueError:
        return None


def _date(value: str | None) -> datetime | None:
    try:
        d = datetime.strptime(value or "", "%Y-%m-%d")
    except ValueError:
        return None
    return d.replace(hour=23, minute=59, second=59, tzinfo=WIB).astimezone(UTC)


def _int(v) -> int | None:
    try:
        return int(float(v)) if v not in (None, "", "null") else None
    except (TypeError, ValueError):
        return None


def _bullets(items) -> str | None:
    items = [str(x).strip() for x in items or [] if str(x).strip()]
    return "\n".join(f"• {x}" for x in items) or None


def _to_jobin(j: dict, m: dict, username: str, index: int) -> JobIn | None:
    title, company = (j.get("title") or "").strip(), (j.get("company") or "").strip()
    if not title or not company:
        return None
    apply_lines = [f"Email: {j['email']}" if j.get("email") else None,
                   f"WhatsApp: {j['whatsapp']}" if j.get("whatsapp") else None,
                   f"Link: {j['apply_link']}" if j.get("apply_link") else None]
    apply_text = "\n".join(x for x in apply_lines if x)
    description = "\n\n".join(filter(None, [
        _bullets(j.get("responsibilities")),
        f"Cara melamar:\n{apply_text}" if apply_text else None,
        f"Sumber: postingan Instagram @{username}",
        "⚠ Postingan ini memiliki tanda penipuan (mis. meminta biaya). Periksa dengan hati-hati." if j.get("suspicious") else None,
    ]))
    exp = _int(j.get("experience_min_years"))
    return JobIn(
        external_id=f"{m['id']}-{index}",
        url=m.get("permalink") or f"https://www.instagram.com/{username}/",
        title=title,
        company_name=company,
        location_city=(j.get("location_city") or None),
        work_mode=_MODES.get((j.get("work_mode") or "").lower()),
        employment_type=_TYPES.get((j.get("employment_type") or "").lower()),
        salary_min=_int(j.get("salary_min")),
        salary_max=_int(j.get("salary_max")),
        salary_currency="IDR" if (_int(j.get("salary_min")) or _int(j.get("salary_max"))) else None,
        description=description,
        requirements=_bullets(j.get("requirements")),
        required_skills=[str(x).strip() for x in j.get("skills") or [] if str(x).strip()][:20],
        benefits=[str(x).strip() for x in j.get("benefits") or [] if str(x).strip()][:20],
        education=(j.get("education") or None),
        experience_min=exp,
        posted_at=_ts(m.get("timestamp")),
        expires_at=_date(j.get("deadline")),
        raw={"username": username, "media_id": m["id"], "permalink": m.get("permalink"),
             "caption": (m.get("caption") or "")[:4000], "parsed": j},
        is_complete=True,
    )


async def _record_post(username: str, m: dict, jobs: int, note: str | None) -> None:
    async with SessionLocal() as session:
        if await session.get(InstagramPost, m["id"]) is None:
            session.add(InstagramPost(media_id=m["id"], username=username, permalink=m.get("permalink"),
                                      posted_at=_ts(m.get("timestamp")), processed_at=datetime.now(UTC),
                                      jobs_found=jobs, note=note))
            await session.commit()


async def _mark_account(username: str, found: int = 0, error: str | None = None) -> None:
    async with SessionLocal() as session:
        acc = await session.scalar(select(InstagramAccount).where(InstagramAccount.username == username))
        if acc:
            acc.last_checked_at = datetime.now(UTC)
            acc.last_error = error
            acc.jobs_found = (acc.jobs_found or 0) + found
            await session.commit()
