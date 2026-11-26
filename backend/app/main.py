import logging
from contextlib import asynccontextmanager
from pathlib import Path

from arq import create_pool
from arq.connections import RedisSettings
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.core.config import get_settings
from app.web.applications import router as applications_router
from app.web.config import router as config_router
from app.web.cv_score import router as cv_score_router
from app.web.profile import router as profile_router
from app.web.routes import router

log = logging.getLogger(__name__)


async def _load_target() -> None:
    """Target CV aktif dipakai saat web menghitung skor (mis. tombol perbarui detail)."""
    from app.core.db import SessionLocal
    from app.services.targeting import load_target

    try:
        async with SessionLocal() as session:
            await load_target(session)
    except Exception:  # DB belum siap: target dimuat ulang saat Profil disimpan
        pass


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        app.state.arq = await create_pool(RedisSettings.from_dsn(get_settings().redis_url))
        await _load_target()
    except Exception:  # web tetap jalan tanpa Redis; tombol scraping akan memberi pesan jelas
        log.warning("Redis tidak tersedia — antrean scraping nonaktif", exc_info=True)
        app.state.arq = None
    yield
    if app.state.arq is not None:
        await app.state.arq.aclose()


app = FastAPI(title="Webotomatis Pencari Kerja", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")
app.include_router(applications_router)
app.include_router(config_router)
app.include_router(cv_score_router)
app.include_router(profile_router)
app.include_router(router)
