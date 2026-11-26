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
    hidden_matches,
    processed_hidden_count,
    region_counts,
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
        "processed_hidden": await processed_hidden_count(session, f),
        "hidden": (await hidden_matches(session, f)) if f.q and total == 0 else [],
        "outside_url": "/jobs?" + urlencode([("f", "1"), ("q", f.q), ("loc", ""), ("posted", "")]
                                           + [("status", s) for s in ("new", "seen", "saved", "applied")]),
        "prev_url": f"/jobs?{urlencode(f.to_query(page=f.page - 1))}" if f.page > 1 else None,
        "next_url": f"/jobs?{urlencode(f.to_query(page=f.page + 1))}" if f.page * PAGE_SIZE < total else None,
        **await shell_context(session),
        **await progress_ctx(request),
    }
    partial = _is_htmx(request, "job-results")
    ctx["oob_sources"] = partial  # angka per sumber di panel filter ikut diperbarui
    if not partial:  # dropdown lokasi hanya ada di halaman penuh
        from app.services.wilayah import grouped_options
        ctx["region_groups"] = grouped_options(await region_counts(session))
    template = "partials/job_results.html" if partial else "pages/jobs.html"
    return templates.TemplateResponse(request, template, ctx)


async def _get_job(session: AsyncSession, job_id: int) -> Job:
    job = await session.get(Job, job_id)
    if job is None:
        raise HTTPException(404, "Lowongan tidak ditemukan")
    return job


@router.post("/jobs/mark-applied", response_class=HTMLResponse)
async def mark_jobs_applied(request: Request, session: AsyncSession = Depends(get_session)):
    """Tandai lowongan sudah dilamar di situsnya (JobStreet, Glints, …) tanpa lewat aplikasi.
    Draft yang sudah ada ikut jadi Terkirim; yang belum punya lamaran dicatat baru. Lowongan hilang dari
    daftar Lowongan dan tercatat di menu Lamaran (kolom Terkirim)."""
    from app.models import Application
    from app.models.enums import ApplicationStatus, ApplyMode
    from app.services.profile import default_cv, get_profile

    form = await request.form()
    ids = sorted({int(v) for v in form.getlist("job_id") if str(v).isdigit()})
    if not ids:
        return templates.TemplateResponse(request, "partials/flash.html",
                                          {"msg": "Pilih lowongan dulu.", "ok": False})
    now = datetime.now(UTC)
    profile = await get_profile(session)
    cv = await default_cv(session)
    existing = {a.job_id: a for a in await session.scalars(select(Application).where(Application.job_id.in_(ids)))}
    done = 0
    for job in await session.scalars(select(Job).where(Job.id.in_(ids))):
        app = existing.get(job.id)
        if app is None:
            session.add(Application(
                job_id=job.id, cv_id=cv.id if cv else None, apply_mode=ApplyMode.PORTAL,
                status=ApplicationStatus.SENT, sent_at=now,
                letter_language=(profile.preferred_language if profile else "id") or "id",
                notes="Dilamar langsung di situs lowongan (ditandai dari daftar Lowongan).",
            ))
        elif app.status in (ApplicationStatus.DRAFT, ApplicationStatus.FAILED):  # antrean email tidak diganggu
            app.status, app.sent_at = ApplicationStatus.SENT, now
            app.apply_mode = ApplyMode.PORTAL
        job.status = JobStatus.APPLIED
        done += 1
    await session.commit()
    resp = templates.TemplateResponse(request, "partials/flash.html", {
        "msg": f"{done} lowongan ditandai sudah dilamar — tidak tampil lagi di sini, lihat di menu Lamaran (Terkirim).",
        "ok": True})
    resp.headers["HX-Trigger"] = "jobs-changed, close-drawer"
    return resp


EXPORT_LIMIT = 2000


@router.get("/jobs/export.xlsx")
async def export_jobs_xlsx(request: Request, session: AsyncSession = Depends(get_session)):
    """Excel lowongan: yang dipilih (?ids=1,2,3) atau, tanpa ids, semua hasil filter yang sedang aktif.
    Harus didaftarkan sebelum /jobs/{job_id}."""
    from fastapi.responses import Response
    from sqlalchemy import select

    from app.models import Job
    from app.services.jobs import build_query
    from app.services.profile import get_profile
    from app.web.jobs_export import build_jobs_workbook
    from app.web.templating import WIB

    ids = [int(x) for x in re.split(r"[,\s]+", request.query_params.get("ids", "")) if x.isdigit()][:EXPORT_LIMIT]
    order = (Job.match_score.desc().nulls_last(), Job.posted_at.desc().nulls_last(), Job.id.desc())
    if ids:
        jobs = list(await session.scalars(select(Job).where(Job.id.in_(ids)).order_by(*order)))
        scope, kind = f"{len(jobs)} lowongan dipilih", "terpilih"
    else:
        f = parse_filters(request)
        jobs = list(await session.scalars(build_query(f).order_by(*order).limit(EXPORT_LIMIT)))
        scope, kind = "semua hasil filter" + (f" (maks. {EXPORT_LIMIT})" if len(jobs) >= EXPORT_LIMIT else ""), "filter"
    profile = await get_profile(session)
    now = datetime.now(UTC)
    data = build_jobs_workbook(jobs, list(profile.skills or []) if profile else [], scope, now)
    name = f"lowongan-{kind}-{now.astimezone(WIB):%Y-%m-%d-%H%M}.xlsx"
    return Response(data, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


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


async def progress_ctx(request: Request) -> dict:
    """Progres scraping dari Redis (ditulis worker) untuk chip topbar & panel progres."""
    from datetime import UTC, datetime

    from app.services.scrape_progress import read_progress, summarize

    pool = getattr(request.app.state, "arq", None)
    if pool is None:
        return {"prog": None, "now": datetime.now(UTC)}
    try:
        lock = bool(await pool.exists(SCRAPE_LOCK))
    except Exception:
        lock = False
    from app.services.scrape_progress import CANCEL_KEY

    prog = summarize(await read_progress(pool), lock)
    if prog and prog["state"] in ("queued", "running"):
        try:
            prog["cancelling"] = bool(await pool.exists(CANCEL_KEY))
        except Exception:
            prog["cancelling"] = False
    return {"prog": prog, "now": datetime.now(UTC)}


@router.post("/scrape", response_class=HTMLResponse)
async def trigger_scrape(request: Request):
    import json

    from app.connectors import REGISTRY
    from app.services.letter_template import SOURCE_LABELS
    from app.services.scrape_progress import PROGRESS_KEY, PROGRESS_TTL, new_state

    pool = getattr(request.app.state, "arq", None)
    if pool is None:
        msg, ok = "Gagal: worker/Redis tidak terhubung. Jalankan `docker compose up worker redis`.", False
    elif await pool.exists(SCRAPE_LOCK):
        msg, ok = "Scraping sedang berjalan — lihat progresnya di bawah.", True
    else:
        from app.services.scrape_progress import CANCEL_KEY

        await pool.delete(CANCEL_KEY)  # sisa tombol Batalkan sebelumnya tidak boleh menghentikan scraping baru
        # Langsung tampilkan "Memulai…" sebelum worker mengambil tugasnya.
        state = new_state("manual", {n: SOURCE_LABELS.get(n, n) for n in REGISTRY}, state="queued")
        await pool.set(PROGRESS_KEY, json.dumps(state), ex=PROGRESS_TTL)
        job = await pool.enqueue_job("scrape_all_task", "manual", _job_id="scrape-all-manual")
        msg, ok = (("Scraping dimulai. Progresnya tampil langsung di bawah & di pojok kanan atas.", True) if job
                   else ("Scraping sudah berjalan.", True))
    resp = templates.TemplateResponse(request, "partials/flash.html", {"msg": msg, "ok": ok})
    resp.headers["HX-Trigger"] = "scrape-started"
    return resp


@router.post("/scrape/cancel", response_class=HTMLResponse)
async def cancel_scrape(request: Request):
    """Hentikan scraping yang sedang berjalan. Worker berhenti di titik aman berikutnya (sebelum halaman/keyword/
    detail berikutnya); lowongan yang sudah tersimpan tetap ada."""
    import json

    from app.services.scrape_progress import (
        CANCEL_KEY, CANCEL_TTL, PROGRESS_KEY, PROGRESS_TTL, _now, read_progress,
    )

    pool = getattr(request.app.state, "arq", None)
    data = await read_progress(pool) if pool is not None else None
    if pool is None or not data or data.get("state") not in ("queued", "running"):
        msg, ok = "Tidak ada scraping yang sedang berjalan.", True
    else:
        await pool.set(CANCEL_KEY, "1", ex=CANCEL_TTL)
        if data["state"] == "queued" and not await pool.exists(SCRAPE_LOCK):
            # Belum diambil worker: langsung tandai batal (worker akan melewatinya saat mulai).
            data.update(state="done", finished_at=_now(), cancelled=True)
            for src in data["sources"].values():
                src.update(state="cancelled", phase="Dibatalkan")
            await pool.set(PROGRESS_KEY, json.dumps(data), ex=PROGRESS_TTL)
            msg = "Scraping dibatalkan sebelum dimulai."
        else:
            msg = "Membatalkan scraping… berhenti setelah permintaan yang sedang berjalan selesai (beberapa detik)."
        ok = True
    resp = templates.TemplateResponse(request, "partials/flash.html", {"msg": msg, "ok": ok})
    resp.headers["HX-Trigger"] = "scrape-started"  # segarkan panel progres & chip
    return resp


@router.get("/scrape/status", response_class=HTMLResponse)
async def scrape_status(request: Request, session: AsyncSession = Depends(get_session)):
    ctx = {**await shell_context(session), **await progress_ctx(request)}
    resp = templates.TemplateResponse(request, "partials/scrape_status.html", ctx)
    prog = ctx["prog"]
    if request.query_params.get("was") and not (prog and prog["state"] in ("queued", "running")):
        # Baru saja selesai: segarkan daftar lowongan & panel progres.
        resp.headers["HX-Trigger"] = "scrape-finished, jobs-changed"
    return resp


@router.get("/scrape/progress", response_class=HTMLResponse)
async def scrape_progress(request: Request):
    ctx = {**await progress_ctx(request), "compact": bool(request.query_params.get("compact"))}
    return templates.TemplateResponse(request, "partials/scrape_progress.html", ctx)


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
        **await progress_ctx(request),
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
