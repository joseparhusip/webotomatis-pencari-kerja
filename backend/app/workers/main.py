import logging

from arq import cron
from arq.connections import RedisSettings

from app.core.config import get_settings
from app.core.db import SessionLocal
from app.services.applications import send_application
from app.services.jobs import close_interrupted_runs
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


async def scrape_all_task(ctx: dict) -> list[dict] | str:
    """Hanya satu scraping boleh berjalan (jadwal otomatis & tombol manual bisa bertabrakan)."""
    redis = ctx["redis"]
    if not await redis.set(SCRAPE_LOCK, "1", ex=SCRAPE_LOCK_TTL, nx=True):
        log.info("Scraping lain masih berjalan — dilewati")
        return "skipped: already running"
    try:
        return await scrape_all()
    finally:
        await redis.delete(SCRAPE_LOCK)


async def send_application_task(ctx: dict, application_id: int) -> str:
    return await send_application(application_id)


class WorkerSettings:
    functions = [scrape_all_task, send_application_task]
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
    max_jobs = 2
