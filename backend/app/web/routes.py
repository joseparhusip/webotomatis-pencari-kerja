import logging
import re
from datetime import UTC, datetime
from urllib.parse import quote, urlencode

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors import REGISTRY
from app.core.db import get_session
from app.models import InstagramAccount, Job, JobListing
from app.models.enums import JobStatus
from app.services.jobs import (
    PAGE_SIZE,
    JobFilters,
    all_sources,
    job_counts,
    latest_runs,
    list_jobs,
    recent_runs,
    search_outside_filters,
    source_counts,
)
from app.services.details import needs_detail, refresh_job
from app.services.scraping import SCRAPE_LOCK
from app.web.config import config_context
from app.web.shell import shell_context
from app.web.templating import templates

log = logging.getLogger(__name__)
router = APIRouter()


def _int_or_none(value: str | None) -> int | None:
    digits = "".join(ch for ch in (value or "") if ch.isdigit())
    return int(digits) if digits else None


def parse_filters(request: Request) -> JobFilters:
    qp = request.query_params
    if "f" not in qp:  # kunjungan pertama → filter sesuai profil target
        return JobFilters.defaults()
    return JobFilters(
        q=qp.get("q", "").strip(),
        roles=qp.getlist("role"),
        modes=qp.getlist("mode"),
        levels=qp.getlist("level"),
        loc=qp.get("loc", ""),
        salary_min=_int_or_none(qp.get("salary_min")),
        min_score=_int_or_none(qp.get("min_score")),
        posted_days=_int_or_none(qp.get("posted")),
        statuses=qp.getlist("status"),
        sources=qp.getlist("source"),
        sort=qp.get("sort", "score"),
        page=_int_or_none(qp.get("page")) or 1,
    )


def _is_htmx(request: Request, target: str | None = None) -> bool:
    if request.headers.get("HX-Request") != "true":
        return False
    return target is None or request.headers.get("HX-Target") == target


@router.get("/")
async def index():
    return RedirectResponse("/jobs")


@router.get("/jobs", response_class=HTMLResponse)
async def jobs_page(
    request: Request,
    f: JobFilters = Depends(parse_filters),
    session: AsyncSession = Depends(get_session),
):
    jobs, total = await list_jobs(session, f)
    ctx = {
        "f": f,
        "jobs": jobs,
        "total": total,
        "page_size": PAGE_SIZE,
        "counts": await job_counts(session),
        "source_options": await source_counts(session, f),
        "outside": (await search_outside_filters(session, f.q)) if f.q and total == 0 else 0,
        "outside_url": "/jobs?" + urlencode([("f", "1"), ("q", f.q), ("loc", ""), ("posted", "")]
                                           + [("status", s) for s in ("new", "seen", "saved", "applied")]),
        "prev_url": f"/jobs?{urlencode(f.to_query(page=f.page - 1))}" if f.page > 1 else None,
        "next_url": f"/jobs?{urlencode(f.to_query(page=f.page + 1))}" if f.page * PAGE_SIZE < total else None,
        **await shell_context(session),
    }
    partial = _is_htmx(request, "job-results")
    ctx["oob_sources"] = partial  # angka per sumber di panel filter ikut diperbarui
    template = "partials/job_results.html" if partial else "pages/jobs.html"
    return templates.TemplateResponse(request, template, ctx)


async def _get_job(session: AsyncSession, job_id: int) -> Job:
    job = await session.get(Job, job_id)
    if job is None:
        raise HTTPException(404, "Lowongan tidak ditemukan")
    return job


@router.get("/jobs/{job_id}", response_class=HTMLResponse)
async def job_detail(request: Request, job_id: int, session: AsyncSession = Depends(get_session)):
    job = await _get_job(session, job_id)
    if job.status == JobStatus.NEW:
        job.status = JobStatus.SEEN
        await session.commit()
    ctx = _panel_ctx(job, loading=needs_detail(job))
    if _is_htmx(request):
        return templates.TemplateResponse(request, "partials/job_panel.html", ctx)
    return templates.TemplateResponse(request, "pages/job_detail.html", {**ctx, **await shell_context(session)})


def _panel_ctx(job: Job, loading: bool = False) -> dict:
    expired = bool(job.expires_at and job.expires_at < datetime.now(UTC))
    return {"job": job, "loading": loading and not expired, "expired": expired}


@router.post("/jobs/{job_id}/refresh", response_class=HTMLResponse)
async def refresh_detail(request: Request, job_id: int, session: AsyncSession = Depends(get_session)):
    """Ambil ulang isi lowongan dari halaman detail situs sumber."""
    job = await _get_job(session, job_id)
    await refresh_job(session, job)
    job = await _get_job(session, job_id)
    resp = templates.TemplateResponse(request, "partials/job_panel.html", _panel_ctx(job))
    resp.headers["HX-Trigger"] = "jobs-changed"
    return resp


@router.post("/jobs/{job_id}/status", response_class=HTMLResponse)
async def set_job_status(
    request: Request,
    job_id: int,
    status: JobStatus = Form(...),
    session: AsyncSession = Depends(get_session),
):
    job = await _get_job(session, job_id)
    job.status = status
    await session.commit()
    resp = templates.TemplateResponse(request, "partials/job_panel.html", _panel_ctx(job))
    resp.headers["HX-Trigger"] = "jobs-changed"
    return resp


@router.post("/scrape", response_class=HTMLResponse)
async def trigger_scrape(request: Request):
    pool = getattr(request.app.state, "arq", None)
    if pool is None:
        msg, ok = "Gagal: worker/Redis tidak terhubung. Jalankan `docker compose up worker redis`.", False
    elif await pool.exists(SCRAPE_LOCK):
        msg, ok = "Scraping sedang berjalan. Hasilnya muncul di riwayat begitu selesai.", True
    else:
        job = await pool.enqueue_job("scrape_all_task", _job_id="scrape-all-manual")
        msg, ok = (("Scraping dijadwalkan. Hasil muncul dalam beberapa menit.", True) if job
                   else ("Scraping sudah berjalan.", True))
    return templates.TemplateResponse(request, "partials/flash.html", {"msg": msg, "ok": ok})


@router.get("/scrape/status", response_class=HTMLResponse)
async def scrape_status(request: Request, session: AsyncSession = Depends(get_session)):
    return templates.TemplateResponse(request, "partials/scrape_status.html", await shell_context(session))


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request, session: AsyncSession = Depends(get_session)):
    from app.core.config import get_settings
    from app.services.applications import emails_sent_today

    s = get_settings()
    runs, runs_page, runs_pages = await recent_runs(session, _int_or_none(request.query_params.get("runs_page")) or 1)
    per_source = dict((await session.execute(
        select(JobListing.source_id, func.count(JobListing.id)).group_by(JobListing.source_id)
    )).all())
    ctx = {
        "active": "settings",
        "connector_names": list(REGISTRY),
        "inactive": [(n, c.disabled_reason()) for n, c in REGISTRY.items() if c.disabled_reason()],
        "sources": await all_sources(session),
        "latest": {r.source_id: r for r in await latest_runs(session)},
        "per_source": per_source,
        "ig_accounts": list(await session.scalars(select(InstagramAccount).order_by(InstagramAccount.username))),
        "ig_ready": not REGISTRY["instagram"].disabled_reason(),
        "ig_reason": REGISTRY["instagram"].disabled_reason(),
        "ig_err": request.query_params.get("ig_err"),
        "ig_ok": request.query_params.get("ig_ok"),
        "cfg_ok": request.query_params.get("cfg_ok"),
        "cfg_err": request.query_params.get("cfg_err"),
        **(await config_context()),
        "runs": runs, "runs_page": runs_page, "runs_pages": runs_pages,
        "cfg": {
            "smtp_ready": s.smtp_ready, "smtp_user": s.smtp_user, "ai_ready": s.ai_ready, "model": s.ai_model, "ai_label": s.ai_label,
            "max_emails": s.max_emails_per_day, "emails_today": await emails_sent_today(session),
            "interval": s.scrape_interval_hours, "pages": s.scrape_pages_per_keyword,
            "gap": s.min_seconds_between_emails,
        },
        **await shell_context(session),
    }
    return templates.TemplateResponse(request, "pages/settings.html", ctx)




_IG_USER = re.compile(r"^[A-Za-z0-9._]{1,30}$")


def _ig_username(raw: str) -> str:
    """'@lokerjakarta', 'instagram.com/lokerjakarta/' → 'lokerjakarta'."""
    v = raw.strip()
    v = re.sub(r"^(https?://)?(www\.)?instagram\.com/", "", v, flags=re.I)
    return v.strip("@/ ").split("/")[0].split("?")[0].lower()


@router.post("/settings/instagram")
async def add_instagram(username: str = Form(""), session: AsyncSession = Depends(get_session)):
    name = _ig_username(username)
    if not _IG_USER.match(name):
        return RedirectResponse(f"/settings?ig_err={quote('Username Instagram tidak valid.')}#instagram", 303)
    if await session.scalar(select(InstagramAccount).where(InstagramAccount.username == name)):
        return RedirectResponse(f"/settings?ig_err={quote('@' + name + ' sudah ada di daftar.')}#instagram", 303)
    session.add(InstagramAccount(username=name))
    await session.commit()
    return RedirectResponse(f"/settings?ig_ok={quote('@' + name + ' ditambahkan.')}#instagram", 303)


@router.post("/settings/instagram/{acc_id}/toggle")
async def toggle_instagram(acc_id: int, session: AsyncSession = Depends(get_session)):
    acc = await session.get(InstagramAccount, acc_id)
    if acc:
        acc.is_active = not acc.is_active
        await session.commit()
    return RedirectResponse("/settings#instagram", 303)


@router.post("/settings/instagram/{acc_id}/delete")
async def delete_instagram(acc_id: int, session: AsyncSession = Depends(get_session)):
    acc = await session.get(InstagramAccount, acc_id)
    if acc:
        await session.delete(acc)
        await session.commit()
    return RedirectResponse("/settings#instagram", 303)
