import asyncio
import logging
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors import REGISTRY, Connector
from app.connectors.base import new_http_client
from app.core.config import get_settings
from app.core.db import SessionLocal
from app.models import JobListing, ScrapeRun, Source
from app.services.details import backfill
from app.services.ingest import ingest
from app.services.targeting import SEARCH_KEYWORDS

log = logging.getLogger(__name__)

# Kunci Redis: hanya satu scraping boleh berjalan (jadwal otomatis vs tombol manual).
SCRAPE_LOCK = "webotomatis:scrape-lock"
SCRAPE_LOCK_TTL = 60 * 30


async def ensure_source(session: AsyncSession, connector: Connector) -> Source:
    source = await session.scalar(select(Source).where(Source.name == connector.name))
    if source is None:
        source = Source(name=connector.name, base_url=connector.base_url, type=connector.type)
        session.add(source)
        await session.flush()
    return source


async def run_connector(connector: Connector, keywords: list[str] | None = None) -> dict:
    settings = get_settings()
    found = new = 0
    errors: list[str] = []

    if reason := connector.disabled_reason():
        log.info("%s dilewati: %s", connector.name, reason)
        return {"source": connector.name, "skipped": reason}

    async with SessionLocal() as session:
        source = await ensure_source(session, connector)
        if not source.is_active:
            return {"source": connector.name, "skipped": True}
        if connector.min_interval_hours:
            last = await session.scalar(
                select(func.max(ScrapeRun.started_at)).where(
                    ScrapeRun.source_id == source.id, ScrapeRun.error.is_(None)
                )
            )
            if last and datetime.now(UTC) - last < timedelta(hours=connector.min_interval_hours):
                return {"source": connector.name, "skipped": f"menunggu jadwal (tiap {connector.min_interval_hours:g} jam)"}
        keywords = keywords or await connector.targets(session, SEARCH_KEYWORDS)
        if not keywords:
            return {"source": connector.name, "skipped": "tidak ada target (mis. belum ada akun Instagram)"}
        run = ScrapeRun(source_id=source.id, started_at=datetime.now(UTC))
        session.add(run)
        await session.commit()

        known = set(
            await session.scalars(select(JobListing.external_id).where(JobListing.source_id == source.id))
        )
        # Sumber dengan batas halaman khusus (mis. Traveloka: daftar lengkap kecil) memakai batasnya sendiri.
        max_pages = connector.max_pages or settings.scrape_pages_per_keyword

        async with new_http_client(settings.http_timeout_seconds) as client:
            for keyword in keywords:
                for page in range(1, max_pages + 1):
                    try:
                        result = await connector.search(client, keyword, page, known)
                    except (httpx.HTTPError, ValueError, KeyError) as exc:
                        msg = f"'{keyword}' hal. {page}: {type(exc).__name__}: {exc}"
                        log.warning("%s %s", connector.name, msg)
                        errors.append(msg)
                        break
                    # Aturan umum: hanya lowongan yang diposting dalam SCRAPE_MAX_AGE_DAYS hari terakhir.
                    cutoff = datetime.now(UTC) - timedelta(days=settings.scrape_max_age_days)
                    result.items = [i for i in result.items if i.posted_at is None or i.posted_at >= cutoff]
                    found += len(result.items)
                    new += await ingest(session, source, result.items, datetime.now(UTC))
                    await session.commit()
                    known.update(i.external_id for i in result.items)
                    await asyncio.sleep(settings.request_delay_seconds)
                    if not result.has_more:
                        break

            # Halaman detail: isi lengkap lowongan baru + perbarui yang sudah lama (bisa diedit/ditutup).
            detail_ok, detail_fail = await backfill(session, source, connector, client)
            if detail_fail:
                errors.append(f"{detail_fail} halaman detail gagal diambil (dicoba lagi nanti)")

        run.finished_at = datetime.now(UTC)
        run.jobs_found = found
        run.jobs_new = new
        run.error = "\n".join(errors) or None
        await session.commit()

    log.info("%s: %d ditemukan, %d baru, %d detail, %d error", connector.name, found, new, detail_ok, len(errors))
    return {"source": connector.name, "found": found, "new": new, "details": detail_ok, "errors": len(errors)}


async def scrape_all() -> list[dict]:
    """Berurutan per sumber: lebih sopan ke situs & menghindari bentrok dedup antar sesi."""
    out = []
    for name, connector in REGISTRY.items():
        try:
            out.append(await run_connector(connector))
        except Exception as exc:  # satu sumber rusak tidak boleh menghentikan yang lain
            log.exception("connector %s gagal total", name)
            out.append({"source": name, "error": repr(exc)})
    return out
