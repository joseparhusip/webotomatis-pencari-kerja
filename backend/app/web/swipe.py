"""Halaman Swipe: tinjau lowongan satu per satu — geser kanan = cocok (simpan), kiri = lewati (sembunyikan)."""

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.models import Job
from app.services import swipe
from app.services.profile import get_profile
from app.web.shell import shell_context
from app.web.templating import templates

router = APIRouter(prefix="/swipe")


async def _stage_ctx(session: AsyncSession, job: Job | None = None, last: int | None = None) -> dict:
    """Isi panggung swipe: kartu (lowongan teratas antrean bila tidak ditentukan), sisa antrean, id keputusan terakhir."""
    if job is None:
        job = await swipe.next_job(session)
    profile = await get_profile(session)
    my_skills = list(profile.skills or []) if profile else []
    return {
        "job": job,
        "remaining": await swipe.queue_count(session),
        "last": last,
        "flags": swipe.green_flags(job, my_skills) if job else [],
        "source_url": swipe.source_url(job) if job else None,
    }


@router.get("", response_class=HTMLResponse)
async def swipe_page(request: Request, session: AsyncSession = Depends(get_session)):
    ctx = {"active": "swipe", **await _stage_ctx(session), **await shell_context(session)}
    return templates.TemplateResponse(request, "pages/swipe.html", ctx)


async def _get(session: AsyncSession, job_id: int) -> Job:
    job = await session.get(Job, job_id)
    if job is None:
        raise HTTPException(404, "Lowongan tidak ditemukan")
    return job


async def _decide(request: Request, session: AsyncSession, job_id: int, action: str) -> HTMLResponse:
    job = await _get(session, job_id)
    changed = swipe.decide(job, action)
    if changed:
        await session.commit()
    if action == "undo":
        # Kartu yang diurungkan tampil lagi; tidak ada keputusan lain yang bisa diurungkan.
        ctx = await _stage_ctx(session, job=job if changed else None)
    else:
        ctx = await _stage_ctx(session, last=job.id if changed else None)
    resp = templates.TemplateResponse(request, "partials/swipe_stage.html", ctx)
    resp.headers["HX-Trigger"] = "jobs-changed"
    return resp


@router.post("/{job_id}/like", response_class=HTMLResponse)
async def like(request: Request, job_id: int, session: AsyncSession = Depends(get_session)):
    return await _decide(request, session, job_id, "like")


@router.post("/{job_id}/nope", response_class=HTMLResponse)
async def nope(request: Request, job_id: int, session: AsyncSession = Depends(get_session)):
    return await _decide(request, session, job_id, "nope")


@router.post("/{job_id}/undo", response_class=HTMLResponse)
async def undo(request: Request, job_id: int, session: AsyncSession = Depends(get_session)):
    return await _decide(request, session, job_id, "undo")
