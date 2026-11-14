"""Data untuk kerangka halaman (topbar): angka di navigasi, status scraping, inisial profil."""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Application, Job
from app.models.enums import ApplicationStatus, JobStatus
from app.services.jobs import JobFilters, build_query, latest_runs
from app.services.profile import get_profile
from app.web.templating import initials


async def shell_context(session: AsyncSession) -> dict:
    runs = await latest_runs(session)
    finished = [r.finished_at for r in runs if r.finished_at]

    f = JobFilters.defaults()
    f.statuses = [JobStatus.NEW.value]
    new_jobs = await session.scalar(select(func.count()).select_from(build_query(f).subquery())) or 0
    drafts = await session.scalar(
        select(func.count(Application.id)).where(
            Application.status.in_([ApplicationStatus.DRAFT, ApplicationStatus.FAILED])
        )
    ) or 0
    profile = await get_profile(session)
    return {
        "last_scrape": max(finished) if finished else None,
        "running": any(r.finished_at is None for r in runs),
        "failed_runs": [r for r in runs if r.error],
        "source_count": len(runs),
        "nav_new_jobs": new_jobs,
        "nav_drafts": drafts,
        "me_initials": initials(profile.full_name if profile else None),
        "me_name": profile.full_name if profile else None,
    }
