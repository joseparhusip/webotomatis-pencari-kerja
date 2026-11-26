"""Halaman Skor CV: unggah PDF/Word (atau pakai CV utama) → skor per kriteria + kesimpulan AI."""

import logging
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import get_session
from app.models import CvReview, Job
from app.services import cv_score, cv_status, job_match
from app.services import profile as profile_svc
from app.services.cv_status import REVIEW_AI_KEY
from app.web.shell import shell_context
from app.web.templating import templates

log = logging.getLogger(__name__)
router = APIRouter(prefix="/cv-score")
HISTORY = 12

templates.env.globals["CV_DECISIONS"] = cv_score.DECISIONS
templates.env.globals["CV_GROUPS"] = cv_score.GROUPS
templates.env.globals["JOB_DECISIONS"] = job_match.JOB_DECISIONS


def _back(msg: str, error: bool = True) -> RedirectResponse:
    return RedirectResponse(f"/cv-score?{'err' if error else 'ok'}={quote(msg)}", status_code=303)


def _stage_cb(token: str):
    async def cb(stage: str) -> None:
        await cv_status.set_stage(token, stage)
    return cb


async def _job_input(session: AsyncSession, title: str, text: str, job_id: str) -> dict | None:
    """Lowongan yang mau dilamar: dari daftar Lowongan (job_id) atau teks yang ditempel. None = tidak ada."""
    if str(job_id or "").strip().isdigit():
        job = await session.get(Job, int(job_id))
        if job is None:
            raise cv_score.CvScoreError("Lowongan tidak ditemukan di database.")
        title, text, hints = job_match.job_from_db(job)
        return {"id": job.id, "title": title, "text": text, "hints": hints}
    text = (text or "").strip()
    if not text:
        return None
    if len(text) < job_match.MIN_JOB_CHARS:
        raise cv_score.CvScoreError("Teks lowongan terlalu pendek — tempel deskripsi & kualifikasinya, bukan hanya judul.")
    return {"id": None, "title": (title or "").strip(), "text": text[:job_match.MAX_JOB_CHARS], "hints": {}}


async def _apply_job(session: AsyncSession, review: CvReview, scan, job: dict) -> None:
    parsed = job_match.parse_job(job["title"], job["text"], await job_match.load_vocab(session), job["hints"])
    jm = job_match.match_job(scan, parsed)
    jm["review"] = job_match.basic_job_review(jm)
    review.job_id, review.job_title, review.job_text = job["id"], parsed["title"][:255], job["text"]
    review.job_match, review.job_score = jm, jm["score"]


async def _analyze(request: Request, session: AsyncSession, data: bytes, filename: str, source: str,
                   token: str, fields: list[str], job: dict | None = None) -> CvReview:
    kind = cv_score.check_upload(data, filename)
    scan = await cv_score.read_document(data, kind, on_stage=_stage_cb(token))
    if not scan.text:
        raise cv_score.CvScoreError(scan.note or "Teks CV tidak terbaca (gambar terlalu buram?). Unggah file yang lebih jelas.")
    await cv_status.set_stage(token, "score")
    if job and not cv_score.clean_fields(fields):  # bidang pasar mengikuti lowongan yang dilamar
        from app.services.targeting import classify_role

        category = classify_role(job["title"] or job["text"][:100], job["text"]).value
        fields = [category] if category != "other" else []
    market = await cv_score.market_snapshot(session, scan, fields)
    report = cv_score.build_report(scan, kind, market)
    await cv_status.set_stage(token, "save")
    review = CvReview(filename=(filename or "cv")[:200], file_type=kind, source=source, method=scan.method,
                      text=scan.text, scan_data=scan.to_data(), report=report, score=report["score"],
                      ai_review=cv_score.basic_review(report))
    if job:
        await _apply_job(session, review, scan, job)
    session.add(review)
    await session.commit()
    await _queue_ai(request, session, review)
    return review


async def _queue_ai(request: Request, session: AsyncSession, review: CvReview) -> None:
    if not get_settings().ai_ready:
        return
    pool = getattr(request.app.state, "arq", None)
    if pool is not None:
        try:
            if await pool.enqueue_job("cv_review_task", review.id, _job_id=f"cv-review-{review.id}") is not None:
                await cv_status.set_ai(review.id, "queued", key=REVIEW_AI_KEY)
            return
        except Exception as exc:
            log.warning("Antrean AI Skor CV tidak tersedia: %s", exc)
    try:  # tanpa worker/Redis: tunggu di sini
        review.ai_review = await cv_score.ai_review(review.report, review.text or "")
        if review.job_match:
            review.job_match = {**review.job_match, "review": await job_match.ai_job_review(
                review.job_match, review.text or "", review.job_text or "")}
        await session.commit()
    except Exception as exc:
        log.warning("Kesimpulan AI Skor CV gagal: %s", exc)


@router.get("", response_class=HTMLResponse)
async def page(request: Request, ok: str = "", err: str = "", job: int | None = None,
               session: AsyncSession = Depends(get_session)):
    history = (await session.scalars(select(CvReview).order_by(CvReview.id.desc()).limit(HISTORY))).all()
    picked = await session.get(Job, job) if job else None
    return templates.TemplateResponse(request, "pages/cv_score.html", {
        **await shell_context(session), "active": "cvscore", "history": history, "picked_job": picked,
        "max_job_chars": job_match.MAX_JOB_CHARS,
        "main_cv": await profile_svc.default_cv(session), "ok": ok, "err": err,
        "max_mb": cv_score.MAX_BYTES // (1024 * 1024), "ai_ready": get_settings().ai_ready,
    })


@router.post("")
async def upload(request: Request, file: UploadFile = File(...), scan_token: str = Form(""),
                 role: list[str] = Form([]), job_title: str = Form(""), job_text: str = Form(""),
                 job_id: str = Form(""), session: AsyncSession = Depends(get_session)):
    await cv_status.set_stage(scan_token, "upload")
    data = await file.read(cv_score.MAX_BYTES + 1)
    try:
        job = await _job_input(session, job_title, job_text, job_id)
        review = await _analyze(request, session, data, file.filename or "cv", "upload", scan_token, role, job)
    except cv_score.CvScoreError as exc:
        await session.rollback()
        return _back(str(exc))
    finally:
        await cv_status.set_stage(scan_token, "done")
    return RedirectResponse(f"/cv-score/{review.id}", status_code=303)


@router.post("/from-profile")
async def from_profile(request: Request, scan_token: str = Form(""), role: list[str] = Form([]),
                       job_title: str = Form(""), job_text: str = Form(""), job_id: str = Form(""),
                       session: AsyncSession = Depends(get_session)):
    await cv_status.set_stage(scan_token, "upload")
    cv = await profile_svc.default_cv(session)
    path = profile_svc.cv_path(cv) if cv else None
    if not path or not path.exists():
        await cv_status.set_stage(scan_token, "done")
        return _back("Belum ada CV utama di Profil.")
    try:
        job = await _job_input(session, job_title, job_text, job_id)
        review = await _analyze(request, session, path.read_bytes(), cv.title, "profile", scan_token, role, job)
    except cv_score.CvScoreError as exc:
        await session.rollback()
        return _back(str(exc))
    finally:
        await cv_status.set_stage(scan_token, "done")
    return RedirectResponse(f"/cv-score/{review.id}", status_code=303)


async def _get(session: AsyncSession, review_id: int) -> CvReview:
    review = await session.get(CvReview, review_id)
    if review is None or not review.report:
        raise HTTPException(404, "Hasil skor CV tidak ditemukan")
    return review


def _ai_context(review: CvReview, status: dict | None) -> dict:
    labels = {c["key"]: c["label"] for c in review.report["criteria"]}
    jm = review.job_match
    if jm:
        labels.update({c["key"]: c["label"] for c in jm["criteria"]})
    return {"review": review, "rv": review.ai_review or cv_score.basic_review(review.report),
            "jm": jm, "jrv": (jm or {}).get("review") or (job_match.basic_job_review(jm) if jm else None),
            "jex": job_match.job_explain(jm) if jm else None,
            "ai_status": status, "crit_labels": labels, "ai_ready": get_settings().ai_ready,
            "ai_label": get_settings().ai_label}


@router.get("/{review_id}", response_class=HTMLResponse)
async def detail(review_id: int, request: Request, err: str = "", session: AsyncSession = Depends(get_session)):
    review = await _get(session, review_id)
    status = await cv_status.get_ai(review.id, key=REVIEW_AI_KEY)
    market = review.report.get("market") or {}
    return templates.TemplateResponse(request, "pages/cv_score_detail.html", {
        **await shell_context(session), "active": "cvscore", "r": review.report,
        "ex": cv_score.explain(review.report), "chosen_fields": market.get("categories") or [],
        "field_mode": market.get("mode", "auto"), "detected_fields": market.get("detected") or [], "err": err,
        **_ai_context(review, status),
    })


@router.get("/{review_id}/job-ai", response_class=HTMLResponse)
async def job_ai_part(review_id: int, request: Request, session: AsyncSession = Depends(get_session)):
    review = await _get(session, review_id)
    status = await cv_status.get_ai(review.id, key=REVIEW_AI_KEY)
    return templates.TemplateResponse(request, "partials/cv_job_ai.html", _ai_context(review, status))


@router.post("/{review_id}/job")
async def change_job(review_id: int, request: Request, job_title: str = Form(""), job_text: str = Form(""),
                     job_id: str = Form(""), scan_token: str = Form(""), session: AsyncSession = Depends(get_session)):
    """Cek CV yang sama terhadap lowongan lain (tanpa unggah ulang)."""
    from app.services.cv_scan import CvScan

    review = await _get(session, review_id)
    try:
        job = await _job_input(session, job_title, job_text, job_id)
    except cv_score.CvScoreError as exc:
        return RedirectResponse(f"/cv-score/{review.id}?err={quote(str(exc))}#lowongan", status_code=303)
    if job is None:
        return RedirectResponse(f"/cv-score/{review.id}?err={quote('Tempel teks lowongan dulu.')}#lowongan",
                                status_code=303)
    await cv_status.set_stage(scan_token, "score")
    scan = CvScan.from_data(review.scan_data or {}, review.text or "")
    scan.method = review.method
    await _apply_job(session, review, scan, job)
    await cv_status.set_stage(scan_token, "save")
    await session.commit()
    await cv_status.set_stage(scan_token, "done")
    await _queue_ai(request, session, review)
    return RedirectResponse(f"/cv-score/{review.id}#lowongan", status_code=303)


@router.post("/{review_id}/field")
async def change_field(review_id: int, request: Request, role: list[str] = Form([]), scan_token: str = Form(""),
                       session: AsyncSession = Depends(get_session)):
    """Nilai ulang untuk bidang lain dari teks yang sudah terbaca (tanpa unggah ulang). Skor & kesimpulan dihitung ulang."""
    from app.services.cv_scan import CvScan

    review = await _get(session, review_id)
    scan = CvScan.from_data(review.scan_data or {}, review.text or "")
    scan.method = review.method
    await cv_status.set_stage(scan_token, "score")
    market = await cv_score.market_snapshot(session, scan, role)
    review.report = cv_score.build_report(scan, review.file_type, market)
    await cv_status.set_stage(scan_token, "save")
    review.score = review.report["score"]
    review.ai_review = cv_score.basic_review(review.report)
    await session.commit()
    await cv_status.set_stage(scan_token, "done")
    await _queue_ai(request, session, review)
    return RedirectResponse(f"/cv-score/{review.id}", status_code=303)


@router.get("/{review_id}/ai", response_class=HTMLResponse)
async def ai_part(review_id: int, request: Request, session: AsyncSession = Depends(get_session)):
    review = await _get(session, review_id)
    status = await cv_status.get_ai(review.id, key=REVIEW_AI_KEY)
    return templates.TemplateResponse(request, "partials/cv_score_ai.html", _ai_context(review, status))


@router.post("/{review_id}/ai", response_class=HTMLResponse)
async def ai_again(review_id: int, request: Request, session: AsyncSession = Depends(get_session)):
    review = await _get(session, review_id)
    await _queue_ai(request, session, review)
    status = await cv_status.get_ai(review.id, key=REVIEW_AI_KEY)
    return templates.TemplateResponse(request, "partials/cv_score_ai.html", _ai_context(review, status))


@router.post("/{review_id}/delete")
async def remove(review_id: int, session: AsyncSession = Depends(get_session)):
    await session.execute(delete(CvReview).where(CvReview.id == review_id))
    await session.commit()
    return _back("Hasil skor CV dihapus.", error=False)
