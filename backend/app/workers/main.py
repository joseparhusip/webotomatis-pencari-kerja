import logging

from arq import cron, func
from arq.connections import RedisSettings

from app.core.config import get_settings
from app.core.db import SessionLocal
from app.services.applications import send_application
from app.services.jobs import close_interrupted_runs
from app.services.scrape_progress import mark_interrupted
from app.services.scraping import SCRAPE_LOCK, SCRAPE_LOCK_TTL, scrape_all

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger(__name__)

settings = get_settings()


async def startup(ctx: dict) -> None:
    async with SessionLocal() as session:
        closed = await close_interrupted_runs(session)
    if closed:
        log.info("%d run scraping yang terputus ditandai 'Terhenti'", closed)
    # Lock dari worker sebelumnya yang mati tidak boleh menahan scraping baru.
    await ctx["redis"].delete(SCRAPE_LOCK)
    await mark_interrupted(ctx["redis"])


# Setengah interval jadwal (maks. 60 mnt) supaya jadwal rutin tidak ikut terlewati.
RECENT_MINUTES = min(60, max(1, settings.scrape_interval_hours) * 30)


async def _ran_recently() -> bool:
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import func, select

    from app.models import ScrapeRun

    async with SessionLocal() as session:
        last = await session.scalar(select(func.max(ScrapeRun.finished_at)).where(ScrapeRun.error.is_(None)))
    return bool(last and datetime.now(UTC) - last < timedelta(minutes=RECENT_MINUTES))


async def scrape_all_task(ctx: dict, trigger: str = "otomatis") -> list[dict] | str:
    """Hanya satu scraping boleh berjalan (jadwal otomatis & tombol manual bisa bertabrakan)."""
    from app.services.scrape_progress import CANCEL_KEY

    redis = ctx["redis"]
    if await redis.exists(CANCEL_KEY) and not await redis.exists(SCRAPE_LOCK):
        # Dibatalkan saat masih antre (tombol Batalkan sudah menandai progresnya).
        await redis.delete(CANCEL_KEY)
        log.info("Scraping dibatalkan sebelum dimulai")
        return "skipped: cancelled"
    if trigger == "otomatis" and await _ran_recently():
        # Worker di-restart (run_at_startup) padahal baru saja scraping: jangan bebani situs lagi.
        log.info("Scraping terakhir < %d menit lalu — scraping otomatis dilewati", RECENT_MINUTES)
        return "skipped: ran recently"
    if not await redis.set(SCRAPE_LOCK, "1", ex=SCRAPE_LOCK_TTL, nx=True):
        log.info("Scraping lain masih berjalan — dilewati")
        return "skipped: already running"
    try:
        return await scrape_all(trigger)
    finally:
        await redis.delete(SCRAPE_LOCK)


async def rescore_all_task(ctx: dict) -> int:
    """Setelah CV/target di Profil berubah: hitung ulang kategori & skor semua lowongan."""
    from app.services.ingest import rescore_all

    async with SessionLocal() as session:
        count = await rescore_all(session)
    log.info("Skor %d lowongan dihitung ulang untuk target CV terbaru", count)
    return count


async def cv_conclude_task(ctx: dict, cv_id: int) -> str:
    """Kesimpulan AI untuk CV (ringkasan, bukti & kalimat surat, usulan posisi) di latar belakang.
    AI sibuk/gagal → dicoba lagi otomatis dengan jeda bertambah (maks. AI_MAX_TRIES)."""
    from arq.worker import Retry

    from app.models import CvDocument
    from app.services import cv_scan
    from app.services.cv_status import AI_MAX_TRIES, set_ai
    from app.services.ingest import rescore_all
    from app.services.profile import apply_conclusions, get_profile

    attempt = ctx.get("job_try", 1)
    async with SessionLocal() as session:
        cv = await session.get(CvDocument, cv_id)
        if cv is None or not cv.scan_data:
            return "cv tidak ada"
        await set_ai(cv_id, "running", attempt)
        scan = cv_scan.CvScan.from_data(cv.scan_data, cv.parsed_text or "")
        try:
            scan = await cv_scan.conclude(scan)
        except Exception as exc:
            reason = "server AI sedang sibuk" if "503" in str(exc) or "UNAVAILABLE" in str(exc) else type(exc).__name__
            if attempt < AI_MAX_TRIES:
                delay = min(600, 30 * 2 ** (attempt - 1))  # 30 dtk, 1, 2, 4, 8 mnt …
                await set_ai(cv_id, "retry", attempt, reason, delay)
                log.warning("Kesimpulan CV %s gagal (%s), dicoba lagi %d dtk", cv_id, reason, delay)
                raise Retry(defer=delay) from exc
            await set_ai(cv_id, "failed", attempt, reason)
            return f"gagal: {reason}"
        cv.scan_data = scan.to_data()
        changed: list[str] = []
        if cv.is_default:  # CV sudah bukan CV utama → simpan saja di CV-nya, Profil tidak diubah
            profile = await get_profile(session)
            if profile is not None:
                changed = await apply_conclusions(session, profile, scan)
        await session.commit()
        if changed:
            await rescore_all(session)  # target pencarian bisa berubah
        await set_ai(cv_id, "done", attempt, ", ".join(changed))
    return "ok"


async def cv_review_task(ctx: dict, review_id: int) -> str:
    """Skor CV: kesimpulan & prioritas perbaikan dari AI (skor tetap dari aturan). Dicoba lagi bila AI sibuk."""
    from arq.worker import Retry

    from app.models import CvReview
    from app.services import cv_score
    from app.services.cv_status import AI_MAX_TRIES, REVIEW_AI_KEY, set_ai

    attempt = ctx.get("job_try", 1)
    async with SessionLocal() as session:
        review = await session.get(CvReview, review_id)
        if review is None or not review.report:
            return "skor cv tidak ada"
        await set_ai(review_id, "running", attempt, key=REVIEW_AI_KEY)
        try:
            # Bagian yang sudah selesai di percobaan sebelumnya tidak diulang.
            if not (review.ai_review or {}).get("ai"):
                review.ai_review = await cv_score.ai_review(review.report, review.text or "")
                await session.commit()
            if review.job_match and not (review.job_match.get("review") or {}).get("ai"):
                from app.services import job_match

                job_review = await job_match.ai_job_review(review.job_match, review.text or "", review.job_text or "")
                review.job_match = {**review.job_match, "review": job_review}
                await session.commit()
        except Exception as exc:
            reason = "server AI sedang sibuk" if "503" in str(exc) or "UNAVAILABLE" in str(exc) else type(exc).__name__
            if attempt < AI_MAX_TRIES:
                delay = min(600, 30 * 2 ** (attempt - 1))
                await set_ai(review_id, "retry", attempt, reason, delay, key=REVIEW_AI_KEY)
                raise Retry(defer=delay) from exc
            await set_ai(review_id, "failed", attempt, reason, key=REVIEW_AI_KEY)
            return f"gagal: {reason}"
        await set_ai(review_id, "done", attempt, key=REVIEW_AI_KEY)
    return "ok"


async def send_application_task(ctx: dict, application_id: int) -> str:
    return await send_application(application_id)


class WorkerSettings:
    functions = [scrape_all_task, send_application_task, rescore_all_task, func(cv_conclude_task, max_tries=6),
                 func(cv_review_task, max_tries=6)]
    on_startup = startup
    cron_jobs = [
        cron(
            scrape_all_task,
            hour=set(range(0, 24, max(1, settings.scrape_interval_hours))),
            minute=0,
            run_at_startup=True,
            unique=True,
        )
    ]
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    job_timeout = 60 * 30
    keep_result = 0  # job id manual bisa langsung dipakai lagi setelah selesai
    max_jobs = 3  # scraping panjang tidak boleh menahan kirim lamaran & kesimpulan CV
