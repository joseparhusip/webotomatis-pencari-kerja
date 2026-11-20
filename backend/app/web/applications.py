import logging

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import get_session
from app.models import Application
from app.models.enums import ApplicationStatus, ApplyMode, JobStatus
from app.services import applications as svc
from app.services.letters import LetterError, can_send, default_subject
from app.services.profile import get_profile, list_cvs
from app.web.shell import shell_context
from app.web.templating import templates

log = logging.getLogger(__name__)
router = APIRouter(prefix="/applications")

APP_STATUS_LABELS = {
    "draft": "Draft", "queued": "Antre", "sent": "Terkirim", "failed": "Gagal", "replied": "Dibalas",
    "interview": "Interview", "offer": "Offer", "rejected": "Ditolak",
}
templates.env.globals["APP_STATUS_LABELS"] = APP_STATUS_LABELS


def _redirect(request: Request, url: str) -> Response:
    if request.headers.get("HX-Request") == "true":
        return Response(status_code=204, headers={"HX-Redirect": url})
    return RedirectResponse(url, status_code=303)


async def _get(session: AsyncSession, app_id: int) -> Application:
    app = await session.get(Application, app_id)
    if app is None:
        raise HTTPException(404, "Lamaran tidak ditemukan")
    return app


LANE_ORDER = [
    (ApplicationStatus.DRAFT, ""), (ApplicationStatus.QUEUED, "dot-info"), (ApplicationStatus.FAILED, "dot-danger"),
    (ApplicationStatus.SENT, ""), (ApplicationStatus.REPLIED, "dot-info"), (ApplicationStatus.INTERVIEW, "dot-ok"),
    (ApplicationStatus.OFFER, "dot-ok"), (ApplicationStatus.REJECTED, ""),
]


async def _lanes(session: AsyncSession) -> list[tuple[str, list[Application], str]]:
    """Kolom papan status. Antre & Gagal hanya muncul bila ada isinya."""
    groups = await svc.tracker(session)
    groups[ApplicationStatus.DRAFT] = await svc.drafts(session)
    lanes = []
    for status, dot in LANE_ORDER:
        items = groups.get(status, [])
        if items or status not in (ApplicationStatus.QUEUED, ApplicationStatus.FAILED):
            lanes.append((status.value, items, dot))
    return lanes


@router.get("", response_class=HTMLResponse)
async def tracker_page(request: Request, session: AsyncSession = Depends(get_session)):

    s = get_settings()
    ctx = {
        "active": "applications",
        "lanes": await _lanes(session),
        "stats": await svc.week_stats(session),
        "emails_today": await svc.emails_sent_today(session),
        "max_emails": s.max_emails_per_day,
        "status_options": [st for st in ApplicationStatus if st != ApplicationStatus.DRAFT],
        **await shell_context(session),
    }
    return templates.TemplateResponse(request, "pages/applications.html", ctx)


@router.get("/export.xlsx")
async def export_xlsx(session: AsyncSession = Depends(get_session)):
    """Rekap semua lamaran (termasuk draft) sebagai file Excel berisi ringkasan, analisis & grafik."""
    from datetime import UTC, datetime

    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    from app.models import Job
    from app.web.export import WIB, build_workbook

    apps = list(await session.scalars(
        select(Application).options(selectinload(Application.job).selectinload(Job.listings))
        .order_by(Application.sent_at.desc().nulls_last(), Application.id.desc())
    ))
    now = datetime.now(UTC)
    data = build_workbook(apps, now)
    name = f"rekap-lamaran-{now.astimezone(WIB):%Y-%m-%d}.xlsx"
    return Response(data, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.post("/prepare")
async def prepare(request: Request, session: AsyncSession = Depends(get_session)):
    form = await request.form()
    job_ids = [int(v) for v in form.getlist("job_id") if str(v).isdigit()]
    if not job_ids:
        return _redirect(request, "/jobs")
    drafts, skipped = await svc.prepare_drafts(session, job_ids)
    if not drafts:
        return _redirect(request, "/applications?skipped=" + str(len(skipped)))
    return _redirect(request, f"/applications/{drafts[0].id}/review")


async def _review_ctx(session: AsyncSession, app: Application) -> dict:
    ids = await svc.draft_ids(session)
    pos = ids.index(app.id) if app.id in ids else -1
    checks = await svc.checks_for(session, app) if app.cover_letter_text is not None else []
    return {
        "app": app,
        "job": app.job,
        "profile": await get_profile(session),
        "cvs": await list_cvs(session),
        "checks": checks,
        "sendable": bool(checks) and can_send(checks),
        "pos": pos + 1,
        "count": len(ids),
        "prev_id": ids[pos - 1] if pos > 0 else None,
        "next_id": ids[pos + 1] if 0 <= pos < len(ids) - 1 else None,
        "ai_ready": get_settings().ai_ready,
        "ai_label": get_settings().ai_label,
        "emails_today": await svc.emails_sent_today(session),
        "max_emails": get_settings().max_emails_per_day,
    }


@router.get("/{app_id}/review", response_class=HTMLResponse)
async def review_page(request: Request, app_id: int, session: AsyncSession = Depends(get_session)):

    app = await _get(session, app_id)
    ctx = {"active": "applications", **await _review_ctx(session, app), **await shell_context(session)}
    return templates.TemplateResponse(request, "pages/review.html", ctx)


@router.post("/{app_id}/generate", response_class=HTMLResponse)
async def generate(request: Request, app_id: int, session: AsyncSession = Depends(get_session)):
    app = await _get(session, app_id)
    error = None
    if await get_profile(session) is None:
        error = "Isi profil dulu: surat butuh nama dan kontak kamu."
    else:
        try:
            error = await svc.fill_letter(session, app)
        except LetterError as exc:
            error = str(exc)
    ctx = await _review_ctx(session, app)
    ctx["error"] = error
    return templates.TemplateResponse(request, "partials/review_editor.html", ctx)


@router.post("/{app_id}", response_class=HTMLResponse)
async def save(
    request: Request,
    app_id: int,
    session: AsyncSession = Depends(get_session),
    cover_letter_text: str = Form(""),
    sent_to: str = Form(""),
    email_subject: str = Form(""),
    apply_mode: ApplyMode = Form(...),
    letter_language: str = Form("id"),
    cv_id: str = Form(""),
):
    app = await _get(session, app_id)
    if app.status not in (ApplicationStatus.DRAFT, ApplicationStatus.FAILED):
        raise HTTPException(409, "Lamaran ini sudah dikirim dan tidak bisa diubah.")
    letter = cover_letter_text.replace("\r\n", "\n")  # textarea browser mengirim CRLF
    if letter.strip() != (app.cover_letter_text or "").strip():
        app.letter_source = "manual"
    app.cover_letter_text = letter
    app.sent_to = sent_to.strip() or None
    app.apply_mode = apply_mode
    app.cv_id = int(cv_id) if cv_id.isdigit() else None
    if letter_language in ("id", "en") and letter_language != app.letter_language:
        app.letter_language = letter_language
        # Subject default ikut bahasa, kecuali sudah diubah manual.
        if email_subject in (default_subject(app.job, await get_profile(session), "id"),
                             default_subject(app.job, await get_profile(session), "en")):
            email_subject = default_subject(app.job, await get_profile(session), letter_language)
    app.email_subject = email_subject.strip()
    await session.commit()
    await session.refresh(app)
    ctx = await _review_ctx(session, app)
    return templates.TemplateResponse(request, "partials/review_checks.html", ctx)


@router.post("/{app_id}/send", response_class=HTMLResponse)
async def send(request: Request, app_id: int, session: AsyncSession = Depends(get_session)):
    app = await _get(session, app_id)
    if app.apply_mode != ApplyMode.EMAIL:
        raise HTTPException(400, "Lamaran mode portal dikirim lewat situs lowongan.")
    if app.status not in (ApplicationStatus.DRAFT, ApplicationStatus.FAILED):
        return templates.TemplateResponse(request, "partials/send_status.html", {"app": app})
    checks = await svc.checks_for(session, app)
    if not can_send(checks):
        ctx = await _review_ctx(session, app)
        return templates.TemplateResponse(request, "partials/review_checks.html", ctx)

    pool = getattr(request.app.state, "arq", None)
    if pool is None:
        app.last_error = "Worker/Redis tidak terhubung."
        await session.commit()
        return templates.TemplateResponse(request, "partials/send_status.html", {"app": app})

    delay = await svc.next_send_delay(session)
    app.status = ApplicationStatus.QUEUED
    app.last_error = None
    await session.commit()
    await pool.enqueue_job("send_application_task", app.id, _job_id=f"send-{app.id}", _defer_by=delay)
    return templates.TemplateResponse(request, "partials/send_status.html", {"app": app, "delay": delay})


@router.get("/{app_id}/status", response_class=HTMLResponse)
async def send_status(request: Request, app_id: int, session: AsyncSession = Depends(get_session)):
    app = await _get(session, app_id)
    await session.refresh(app)
    return templates.TemplateResponse(request, "partials/send_status.html", {"app": app})


@router.post("/{app_id}/mark-applied")
async def mark_applied(request: Request, app_id: int, session: AsyncSession = Depends(get_session)):
    """Mode portal: pengguna sudah apply di situs lowongan."""
    from datetime import UTC, datetime

    app = await _get(session, app_id)
    if app.status in (ApplicationStatus.DRAFT, ApplicationStatus.FAILED):
        app.status = ApplicationStatus.SENT
        app.sent_at = datetime.now(UTC)
        app.job.status = JobStatus.APPLIED
        await session.commit()
    nxt = next(iter(await svc.draft_ids(session)), None)
    return _redirect(request, f"/applications/{nxt}/review" if nxt else "/applications")


@router.post("/{app_id}/skip")
async def skip(request: Request, app_id: int, session: AsyncSession = Depends(get_session)):
    """Lewati = hapus draft. Lowongan tetap ada dan bisa disiapkan lagi."""
    app = await _get(session, app_id)
    ids = await svc.draft_ids(session)
    if app.status in (ApplicationStatus.DRAFT, ApplicationStatus.FAILED):
        await session.delete(app)
        await session.commit()
    remaining = [i for i in ids if i != app_id]
    later = [i for i in remaining if i > app_id]
    nxt = later[0] if later else (remaining[0] if remaining else None)
    return _redirect(request, f"/applications/{nxt}/review" if nxt else "/applications")


@router.post("/{app_id}/status", response_class=HTMLResponse)
async def set_status(
    request: Request,
    app_id: int,
    status: ApplicationStatus = Form(...),
    session: AsyncSession = Depends(get_session),
):
    app = await _get(session, app_id)
    app.status = status
    await session.commit()
    return _redirect(request, "/applications")
