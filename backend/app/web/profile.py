import re
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import get_session
from app.models import CvDocument, LetterEvidence, UserProfile
from app.models.enums import RoleCategory
from app.services import cv_scan, cv_status
from app.services.cv_targets import derive_targets
from app.services import profile as svc
from app.web.shell import shell_context
from app.web.templating import templates

router = APIRouter(prefix="/profile")
_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")
CATEGORIES = [c for c in RoleCategory if c != RoleCategory.OTHER]


def _back(section: str, msg: str = "", error: bool = False) -> RedirectResponse:
    q = f"?{'err' if error else 'ok'}={quote(msg)}" if msg else ""
    return RedirectResponse(f"/profile{q}#{section}", status_code=303)


async def _retarget(request: Request, session: AsyncSession) -> None:
    """Target CV berubah: muat ulang di proses web, lalu hitung ulang skor semua lowongan di worker."""
    from app.services.ingest import rescore_all
    from app.services.targeting import load_target

    await load_target(session)
    pool = getattr(request.app.state, "arq", None)
    if pool is not None:
        try:
            await pool.enqueue_job("rescore_all_task", _job_id="rescore-all")
            return
        except Exception:
            pass
    await rescore_all(session)  # worker/Redis tidak ada: hitung di sini


def _url(value: str) -> str | None:
    value = value.strip()
    if not value:
        return None
    return value if re.match(r"^https?://", value) else f"https://{value}"


@router.get("", response_class=HTMLResponse)
async def profile_page(request: Request, session: AsyncSession = Depends(get_session)):

    profile = await svc.get_profile(session)
    cvs = await svc.list_cvs(session)
    main_cv = next((c for c in cvs if c.is_default), None)
    scan = cv_scan.CvScan.from_data(main_cv.scan_data) if main_cv and main_cv.scan_data else None
    summary = cv_scan.total_experience(scan.experiences, scan.projects) if scan else None
    ctx = {
        "exp_summary": summary,
        "months_text": cv_scan.months_text,
        "active": "profile",
        "profile": profile,
        "cvs": cvs,
        "main_cv": main_cv,
        "cv_scan": cv_scan.CvScan.from_data(main_cv.scan_data) if main_cv and main_cv.scan_data else None,
        "evidence": await svc.list_evidence(session),
        "categories": CATEGORIES,
        "target_plan": derive_targets(scan) if scan else None,
        "ai_ready": get_settings().ai_ready,
        "ai_status": await cv_status.get_ai(main_cv.id if main_cv else None),
        "ok": request.query_params.get("ok"),
        "err": request.query_params.get("err"),
        **await shell_context(session),
    }
    return templates.TemplateResponse(request, "pages/profile.html", ctx)


# ---------- data diri ----------

@router.post("")
async def save_profile(
    request: Request,
    session: AsyncSession = Depends(get_session),
    full_name: str = Form(""),
    email: str = Form(""),
    phone: str = Form(""),
    address: str = Form(""),
    linkedin_url: str = Form(""),
    portfolio_url: str = Form(""),
    summary: str = Form(""),
    skills: str = Form(""),
    experience_years: str = Form("0"),
    preferred_language: str = Form("id"),
):
    if not full_name.strip():
        return _back("data", "Nama wajib diisi.", error=True)
    if not _EMAIL_RE.match(email.strip()):
        return _back("data", "Email tidak valid.", error=True)

    profile = await svc.get_profile(session)
    if profile is None:
        profile = UserProfile(full_name=full_name.strip(), email=email.strip())
        session.add(profile)
    profile.full_name = full_name.strip()
    profile.email = email.strip()
    profile.phone = phone.strip() or None
    profile.address = address.strip() or None
    profile.linkedin_url = _url(linkedin_url)
    profile.portfolio_url = _url(portfolio_url)
    profile.summary = summary.strip() or None
    profile.skills = svc.split_list(skills)
    profile.experience_years = int(experience_years) if experience_years.strip().isdigit() else 0
    profile.preferred_language = preferred_language if preferred_language in ("id", "en") else "id"
    await session.commit()
    await _retarget(request, session)  # skill/pengalaman/domisili ikut menentukan skor & filter
    return _back("data", "Data diri tersimpan.")


@router.post("/letter-texts")
async def save_letter_texts(
    session: AsyncSession = Depends(get_session),
    intro_id: str = Form(""),
    intro_en: str = Form(""),
    strength_id: str = Form(""),
    strength_en: str = Form(""),
):
    profile = await svc.get_profile(session)
    if profile is None:
        return _back("data", "Isi data diri dulu.", error=True)
    profile.intro_id = intro_id.strip() or None
    profile.intro_en = intro_en.strip() or None
    profile.strength_id = strength_id.strip() or None
    profile.strength_en = strength_en.strip() or None
    await session.commit()
    return _back("surat", "Kalimat surat tersimpan.")


# ---------- target pencarian ----------

@router.post("/targets")
async def save_targets(request: Request, session: AsyncSession = Depends(get_session)):
    form = await request.form()
    profile = await svc.get_profile(session)
    if profile is None:
        return _back("data", "Isi data diri dulu.", error=True)
    allowed = {c.value for c in CATEGORIES}
    cats = [c for c in form.getlist("categories") if c in allowed]
    keywords = []
    for k in svc.split_list(str(form.get("keywords") or "")):
        k = k.lower()[:80]
        if k not in keywords:
            keywords.append(k)
    if not cats:
        return _back("target", "Pilih minimal satu bidang.", error=True)
    profile.target_categories, profile.target_keywords = cats, keywords[:20]
    await session.commit()
    await _retarget(request, session)
    return _back("target", "Target pencarian tersimpan. Skor lowongan dihitung ulang; lowongan baru ikut scraping berikutnya.")


@router.post("/targets/from-cv")
async def targets_from_cv(request: Request, session: AsyncSession = Depends(get_session)):
    profile = await svc.get_profile(session)
    cv = await svc.default_cv(session)
    if profile is None or cv is None or not cv.scan_data:
        return _back("target", "Belum ada CV utama yang sudah dipindai.", error=True)
    plan = derive_targets(cv_scan.CvScan.from_data(cv.scan_data))
    if not plan.categories:
        return _back("target", "Bidang tidak terbaca dari CV. Pilih bidang secara manual.", error=True)
    profile.target_categories, profile.target_keywords = plan.categories, plan.keywords
    await session.commit()
    await _retarget(request, session)
    return _back("target", "Target dihitung ulang dari CV utama.")


# ---------- CV ----------

async def _cv(session: AsyncSession, cv_id: int) -> CvDocument:
    cv = await session.get(CvDocument, cv_id)
    if cv is None:
        raise HTTPException(404, "CV tidak ditemukan")
    return cv


def _scan_message(scan: cv_scan.CvScan, changed: list[str] | None) -> str:
    how = "teks diambil dengan OCR" if scan.method == "ocr" else "teks PDF"
    how += {"gemini": ", disimpulkan Gemini", "anthropic": ", disimpulkan Claude"}.get(scan.ai, "")
    parts = []
    if changed is not None:
        parts.append(f"Profil diperbarui dari CV ({how}): {', '.join(changed)}." if changed
                     else f"Profil sudah sesuai CV ({how}).")
    if not scan.text:
        parts.append("Teks CV tidak terbaca, AI surat tidak bisa memakainya.")
    if scan.note:
        parts.append(scan.note)
    return " ".join(parts)


def _stage_cb(token: str):
    async def cb(stage: str) -> None:
        await cv_status.set_stage(token, stage)
    return cb


def _keep_previous_conclusions(cv: CvDocument, scan: cv_scan.CvScan) -> None:
    """CV yang sama dipindai ulang: kesimpulan AI lama (bukti, kalimat surat, usulan posisi, ringkasan) tetap
    dipakai sampai kesimpulan baru selesai disusun di latar belakang — tidak dikosongkan."""
    previous = cv.scan_data or {}
    prev_ai = previous.get("ai") or (previous.get("method") if previous.get("method") in ("gemini", "anthropic") else "")
    if prev_ai and scan.text:
        old = cv_scan.CvScan.from_data(previous)
        scan.evidence, scan.letter, scan.targets = old.evidence, old.letter, old.targets
        if old.fields.get("summary"):
            scan.fields["summary"] = old.fields["summary"]
        scan.ai = prev_ai


async def _read_file(cv: CvDocument, token: str = "") -> cv_scan.CvScan:
    path = svc.cv_path(cv)
    try:
        text_layer = svc.extract_pdf_text(path)
    except Exception:
        text_layer = ""
    scan = await cv_scan.read_cv(path.read_bytes(), text_layer, on_stage=_stage_cb(token))
    _keep_previous_conclusions(cv, scan)
    cv.parsed_text = scan.text or cv.parsed_text
    cv.scan_data = scan.to_data()
    return scan


async def _queue_conclusions(request: Request, session: AsyncSession, cv: CvDocument,
                             scan: cv_scan.CvScan) -> str:
    """Kesimpulan AI disusun worker di latar belakang (dicoba lagi otomatis bila AI sibuk)."""
    s = get_settings()
    if not scan.text:
        return ""
    if not s.ai_ready:
        return ("AI belum aktif: ringkasan Bahasa Indonesia, bukti & kalimat surat tidak dibuat. "
                "Isi API key di Pengaturan.")
    pool = getattr(request.app.state, "arq", None)
    if pool is not None:
        try:
            job = await pool.enqueue_job("cv_conclude_task", cv.id, _job_id=f"cv-conclude-{cv.id}")
            if job is not None:
                await cv_status.set_ai(cv.id, "queued")
            return f"{s.ai_label} menyusun ringkasan, bukti & kalimat surat di latar belakang; Profil diperbarui otomatis."
        except Exception:
            pass
    # Tanpa worker/Redis: susun di sini (menunggu).
    try:
        scan = await cv_scan.conclude(scan)
    except Exception as exc:
        return f"{s.ai_label} gagal menyusun kesimpulan ({type(exc).__name__}). Klik Pindai ulang nanti."
    cv.scan_data = scan.to_data()
    if cv.is_default:
        profile = await svc.get_profile(session)
        if profile is not None:
            await svc.apply_conclusions(session, profile, scan)
    await session.commit()
    return f"Kesimpulan disusun {s.ai_label}."


def _done(profile: UserProfile, msg: str) -> RedirectResponse:
    if not profile.full_name or not profile.email:
        return _back("data", msg + " Lengkapi nama & email di Data diri.", error=True)
    return _back("data", msg)


@router.post("/cv")
async def upload_cv(
    request: Request,
    session: AsyncSession = Depends(get_session),
    file: UploadFile = File(...),
    title: str = Form(""),
    make_active: str = Form(""),
    replace_old: str = Form(""),
    scan_token: str = Form(""),
):
    await cv_status.set_stage(scan_token, "upload")
    data = await file.read(svc.MAX_CV_BYTES + 1)
    profile = await svc.get_profile(session)
    created = profile is None
    if created:  # CV pertama boleh diunggah sebelum data diri diisi — profil dibuat dari CV
        profile = UserProfile(full_name="", email="", skills=[])
        session.add(profile)
        await session.flush()
    try:
        cv = await svc.save_uploaded_cv(session, profile, file.filename or "cv.pdf", data, title)
    except svc.ProfileError as exc:
        await session.rollback()
        await cv_status.set_stage(scan_token, "done")
        return _back("cv", str(exc), error=True)

    scan = await cv_scan.read_cv(data, cv.parsed_text or "", on_stage=_stage_cb(scan_token))
    cv.parsed_text = scan.text or None
    cv.scan_data = scan.to_data()
    await cv_status.set_stage(scan_token, "save")
    activate = created or make_active == "on" or replace_old == "on"
    changed = await svc.sync_profile_from_cv(session, profile, cv) if activate else None
    await session.commit()
    if activate:
        await _retarget(request, session)

    msg = f"CV \"{cv.title}\" tersimpan di storage{' dan jadi CV utama' if activate else ''}. "
    if replace_old == "on":
        deleted, skipped = await svc.replace_old_cvs(session, cv)
        if deleted:
            msg += f"CV lama dihapus: {', '.join(deleted)}. "
        if skipped:
            msg += f"Tidak dihapus karena dipakai lamaran di antrean: {', '.join(skipped)}. "
    msg += _scan_message(scan, changed) + " " + await _queue_conclusions(request, session, cv, scan)
    await cv_status.set_stage(scan_token, "done")
    return _done(profile, msg.strip())


@router.post("/cv/{cv_id}/scan")
async def rescan_cv(cv_id: int, request: Request, session: AsyncSession = Depends(get_session)):
    """Baca ulang CV. Bila CV ini CV utama, Profil ikut diperbarui; kesimpulan AI disusun ulang di latar belakang."""
    token = str((await request.form()).get("scan_token") or "")
    cv = await _cv(session, cv_id)
    profile = await svc.get_profile(session)
    if profile is None or not svc.cv_path(cv).is_file():
        return _back("cv", "File CV tidak ada di storage.", error=True)
    scan = await _read_file(cv, token)
    await cv_status.set_stage(token, "save")
    changed = await svc.sync_profile_from_cv(session, profile, cv) if cv.is_default else None
    await session.commit()
    if cv.is_default:
        await _retarget(request, session)
    msg = f"CV \"{cv.title}\" dipindai ulang. " + _scan_message(scan, changed) + " " + \
        await _queue_conclusions(request, session, cv, scan)
    await cv_status.set_stage(token, "done")
    return _done(profile, msg.strip())


@router.post("/cv/{cv_id}")
async def update_cv(
    cv_id: int,
    session: AsyncSession = Depends(get_session),
    title: str = Form(""),
    role_category: str = Form(""),
):
    cv = await _cv(session, cv_id)
    cv.title = title.strip() or cv.title
    cv.role_category = RoleCategory(role_category) if role_category in {c.value for c in CATEGORIES} else None
    await session.commit()
    return _back("cv", f"CV \"{cv.title}\" tersimpan.")


@router.post("/cv/{cv_id}/default")
async def make_default(cv_id: int, request: Request, session: AsyncSession = Depends(get_session)):
    """Pakai CV ini: jadi CV utama dan Profil diperbarui sesuai isinya."""
    token = str((await request.form()).get("scan_token") or "")
    cv = await _cv(session, cv_id)
    profile = await svc.get_profile(session)
    if profile is None:
        return _back("data", "Isi data diri dulu.", error=True)
    scan = None
    if not cv.scan_data and svc.cv_path(cv).is_file():  # CV lama yang belum pernah dipindai
        scan = await _read_file(cv, token)
    await cv_status.set_stage(token, "save")
    changed = await svc.sync_profile_from_cv(session, profile, cv)
    await session.commit()
    await _retarget(request, session)
    msg = f"\"{cv.title}\" jadi CV utama. "
    current = scan or cv_scan.CvScan.from_data(cv.scan_data or {}, cv.parsed_text or "")
    if scan is not None:
        msg += _scan_message(scan, changed)
    else:
        msg += f"Profil diperbarui: {', '.join(changed)}." if changed else "Profil sudah sesuai CV ini."
    if not current.ai:  # CV ini belum punya kesimpulan AI → susun di latar belakang
        msg += " " + await _queue_conclusions(request, session, cv, current)
    await cv_status.set_stage(token, "done")
    return _done(profile, msg.strip())


@router.get("/scan-status")
async def scan_status(token: str = ""):
    """Tahap scan yang sedang berjalan (untuk layar loading)."""
    from fastapi.responses import JSONResponse

    return JSONResponse(await cv_status.get_stage(token) or {})


@router.get("/ai-status", response_class=HTMLResponse)
async def ai_status(request: Request, session: AsyncSession = Depends(get_session)):
    """Status kesimpulan AI untuk CV utama. Selesai setelah ditunggu → muat ulang halaman Profil."""
    cv = await svc.default_cv(session)
    status = await cv_status.get_ai(cv.id if cv else None)
    resp = templates.TemplateResponse(request, "partials/cv_ai_status.html", {"ai_status": status, "main_cv": cv})
    if request.query_params.get("was") and status and status.get("state") in ("done", "failed"):
        from app.services.targeting import load_target

        await load_target(session)  # worker mengubah target pencarian → muat ulang di proses web
        resp.headers["HX-Refresh"] = "true"
    return resp


@router.post("/cv/{cv_id}/conclude")
async def conclude_again(cv_id: int, request: Request, session: AsyncSession = Depends(get_session)):
    """Coba lagi menyusun kesimpulan AI (setelah semua percobaan otomatis gagal)."""
    cv = await _cv(session, cv_id)
    scan = cv_scan.CvScan.from_data(cv.scan_data or {}, cv.parsed_text or "")
    msg = await _queue_conclusions(request, session, cv, scan)
    return _back("data", msg or "Teks CV kosong, pindai ulang CV dulu.")


@router.post("/cv/{cv_id}/delete")
async def remove_cv(cv_id: int, session: AsyncSession = Depends(get_session)):
    cv = await _cv(session, cv_id)
    title = cv.title
    try:
        await svc.delete_cv(session, cv)
    except svc.ProfileError as exc:
        return _back("cv", str(exc), error=True)
    return _back("cv", f"CV \"{title}\" dihapus.")


@router.get("/cv/{cv_id}/file")
async def cv_file(cv_id: int, download: str = "", session: AsyncSession = Depends(get_session)):
    """PDF CV: tampil di dalam pop-up (inline), atau diunduh dengan ?download=1."""
    cv = await _cv(session, cv_id)
    path = svc.cv_path(cv)
    if not path.is_file():
        raise HTTPException(404, "File CV tidak ada di storage")
    return FileResponse(path, media_type="application/pdf", filename=f"{cv.title}.pdf",
                        content_disposition_type="attachment" if download else "inline")


# ---------- bukti CV ----------

def _categories(values: list[str]) -> list[str]:
    allowed = {c.value for c in CATEGORIES}
    return [v for v in values if v in allowed]


@router.post("/evidence")
async def add_evidence(request: Request, session: AsyncSession = Depends(get_session)):
    profile = await svc.get_profile(session)
    if profile is None:
        return _back("data", "Isi data diri dulu.", error=True)
    form = await request.form()
    text_id = str(form.get("text_id", "")).strip()
    if not text_id:
        return _back("bukti", "Teks bukti (Indonesia) wajib diisi.", error=True)
    existing = await svc.list_evidence(session)
    session.add(LetterEvidence(
        user_id=profile.id, text_id=text_id, text_en=str(form.get("text_en", "")).strip() or None,
        categories=_categories(form.getlist("categories")), skills=svc.split_list(str(form.get("skills", ""))),
        position=(max((e.position for e in existing), default=-1) + 1), is_active=True,
    ))
    await session.commit()
    return _back("bukti", "Bukti ditambahkan.")


@router.post("/evidence/{ev_id}")
async def update_evidence(ev_id: int, request: Request, session: AsyncSession = Depends(get_session)):
    ev = await session.get(LetterEvidence, ev_id)
    if ev is None:
        raise HTTPException(404, "Bukti tidak ditemukan")
    form = await request.form()
    text_id = str(form.get("text_id", "")).strip()
    if not text_id:
        return _back("bukti", "Teks bukti (Indonesia) tidak boleh kosong.", error=True)
    ev.text_id = text_id
    ev.text_en = str(form.get("text_en", "")).strip() or None
    ev.categories = _categories(form.getlist("categories"))
    ev.skills = svc.split_list(str(form.get("skills", "")))
    ev.is_active = form.get("is_active") == "on"
    await session.commit()
    return _back(f"bukti-{ev.id}", "Bukti tersimpan.")


@router.post("/evidence/{ev_id}/delete")
async def delete_evidence(ev_id: int, session: AsyncSession = Depends(get_session)):
    ev = await session.get(LetterEvidence, ev_id)
    if ev is not None:
        await session.delete(ev)
        await session.commit()
    return _back("bukti", "Bukti dihapus.")
