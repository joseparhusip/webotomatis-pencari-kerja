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
from app.services.letter_template import SOURCE_LABELS
from app.services.scrape_progress import SKIP_KEY, SKIP_TTL, NullProgress, Progress, get_redis
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


async def _old_ids(redis, name: str, cutoff: datetime) -> set[str]:
    """Lowongan yang sudah pernah dibuka & ternyata lebih lama dari batas umur → tidak perlu dibuka lagi."""
    if redis is None:
        return set()
    try:
        return set(await redis.zrangebyscore(SKIP_KEY.format(name), "-inf", cutoff.timestamp()))
    except Exception:
        return set()


async def _remember_old(redis, name: str, items) -> None:
    if redis is None or not items:
        return
    try:
        key = SKIP_KEY.format(name)
        await redis.zadd(key, {i.external_id: i.posted_at.timestamp() for i in items})
        await redis.expire(key, SKIP_TTL)
    except Exception as exc:
        log.warning("Gagal menyimpan cache lowongan lama %s: %s", name, exc)


async def run_connector(connector: Connector, keywords: list[str] | None = None,
                        progress: Progress | None = None) -> dict:
    settings = get_settings()
    progress = progress or NullProgress()
    name = connector.name
    found = new = detail_ok = 0
    errors: list[str] = []

    async def skip(reason: str) -> dict:
        await progress.source(name, state="skipped", phase="Dilewati", note=reason)
        return {"source": name, "skipped": reason}

    if reason := connector.disabled_reason():
        log.info("%s dilewati: %s", name, reason)
        return await skip(reason)

    async with SessionLocal() as session:
        source = await ensure_source(session, connector)
        if not source.is_active:
            return await skip("sumber dinonaktifkan")
        if connector.min_interval_hours:
            last = await session.scalar(
                select(func.max(ScrapeRun.started_at)).where(
                    ScrapeRun.source_id == source.id, ScrapeRun.error.is_(None)
                )
            )
            if last and datetime.now(UTC) - last < timedelta(hours=connector.min_interval_hours):
                return await skip(f"menunggu jadwal (tiap {connector.min_interval_hours:g} jam)")
        keywords = keywords or await connector.targets(session, SEARCH_KEYWORDS)
        if not keywords:
            return await skip("tidak ada target (mis. belum ada akun Instagram)")
        run = ScrapeRun(source_id=source.id, started_at=datetime.now(UTC))
        session.add(run)
        await session.commit()

        known = set(
            await session.scalars(select(JobListing.external_id).where(JobListing.source_id == source.id))
        )
        redis = get_redis()
        cutoff = datetime.now(UTC) - timedelta(days=settings.scrape_max_age_days)
        old = await _old_ids(redis, name, cutoff)
        # Sumber dengan batas halaman khusus (mis. Traveloka: daftar lengkap kecil) memakai batasnya sendiri.
        max_pages = connector.max_pages or settings.scrape_pages_per_keyword
        await progress.source(name, state="running", phase="Mencari lowongan", done=0, total=len(keywords))

        blocked = False  # situs membalas 429/403: berhenti mengakses situs ini sampai scraping berikutnya
        async with new_http_client(settings.http_timeout_seconds) as client:
            for k_index, keyword in enumerate(keywords, 1):
                if blocked:
                    break
                phase = f'Mencari lowongan · "{keyword}"' if len(keywords) > 1 else "Mencari lowongan"
                await progress.source(name, phase=phase)
                for page in range(1, max_pages + 1):
                    try:
                        result = await connector.search(client, keyword, page, known | old)
                    except httpx.HTTPStatusError as exc:
                        if exc.response.status_code in (403, 429):
                            blocked = True
                            msg = (f"situs membatasi akses (HTTP {exc.response.status_code}) di '{keyword}' — "
                                   "sisa keyword dilanjutkan pada scraping berikutnya")
                        else:
                            msg = f"'{keyword}' hal. {page}: HTTP {exc.response.status_code}"
                        log.warning("%s %s", name, msg)
                        errors.append(msg)
                        break
                    except (httpx.HTTPError, ValueError, KeyError) as exc:
                        msg = f"'{keyword}' hal. {page}: {type(exc).__name__}: {exc}"
                        log.warning("%s %s", name, msg)
                        errors.append(msg)
                        await asyncio.sleep(settings.request_delay_seconds)
                        break
                    # Aturan umum: hanya lowongan yang diposting dalam SCRAPE_MAX_AGE_DAYS hari terakhir.
                    too_old = [i for i in result.items if i.posted_at is not None and i.posted_at < cutoff]
                    await _remember_old(redis, name, too_old)
                    old.update(i.external_id for i in too_old)
                    result.items = [i for i in result.items if i.posted_at is None or i.posted_at >= cutoff]
                    found += len(result.items)
                    new += await ingest(session, source, result.items, datetime.now(UTC))
                    await session.commit()
                    known.update(i.external_id for i in result.items)
                    await progress.source(name, found=found, new=new)
                    if not result.has_more:
                        break
                    await asyncio.sleep(settings.request_delay_seconds)
                await progress.source(name, done=k_index)

            # Halaman detail: isi lengkap lowongan baru + perbarui yang sudah lama (bisa diedit/ditutup).
            async def on_detail(done: int, total: int) -> None:
                await progress.source(name, phase="Mengambil detail lowongan", done=done, total=total, details=done)

            detail_ok, detail_fail = (0, 0) if blocked else await backfill(
                session, source, connector, client, on_progress=on_detail)
            if detail_fail:
                errors.append(f"{detail_fail} halaman detail gagal diambil (dicoba lagi nanti)")

        run.finished_at = datetime.now(UTC)
        run.jobs_found = found
        run.jobs_new = new
        run.error = "\n".join(errors) or None
        await session.commit()

    await progress.source(name, state="done", phase="Selesai", found=found, new=new, details=detail_ok,
                          note=f"{len(errors)} kendala" if errors else "")
    log.info("%s: %d ditemukan, %d baru, %d detail, %d error", name, found, new, detail_ok, len(errors))
    return {"source": name, "found": found, "new": new, "details": detail_ok, "errors": len(errors)}


async def scrape_all(trigger: str = "otomatis") -> list[dict]:
    """Semua sumber berjalan bersamaan (situs berbeda), tetapi tiap situs tetap diakses berurutan
    dengan jeda — jadi total waktu ≈ sumber paling lama, bukan jumlah semuanya."""
    progress = Progress(get_redis(), trigger, {n: SOURCE_LABELS.get(n, n) for n in REGISTRY})
    await progress.save()

    async def one(name: str, connector: Connector) -> dict:
        try:
            return await run_connector(connector, progress=progress)
        except Exception as exc:  # satu sumber rusak tidak boleh menghentikan yang lain
            log.exception("connector %s gagal total", name)
            await progress.source(name, state="error", phase="Gagal", note=type(exc).__name__)
            return {"source": name, "error": repr(exc)}

    try:
        return list(await asyncio.gather(*(one(n, c) for n, c in REGISTRY.items())))
    finally:
        await progress.finish()
