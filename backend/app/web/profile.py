import re
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import get_session
from app.models import CvDocument, LetterEvidence, UserProfile
from app.models.enums import RoleCategory
from app.services import profile as svc
from app.services.letter_template import DEFAULT_EVIDENCE
from app.web.shell import shell_context
from app.web.templating import templates

router = APIRouter(prefix="/profile")
_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")
CATEGORIES = [RoleCategory.FULLSTACK, RoleCategory.FRONTEND, RoleCategory.BACKEND, RoleCategory.MOBILE]


def _back(section: str, msg: str = "", error: bool = False) -> RedirectResponse:
    q = f"?{'err' if error else 'ok'}={quote(msg)}" if msg else ""
    return RedirectResponse(f"/profile{q}#{section}", status_code=303)


def _url(value: str) -> str | None:
    value = value.strip()
    if not value:
        return None
    return value if re.match(r"^https?://", value) else f"https://{value}"


@router.get("", response_class=HTMLResponse)
async def profile_page(request: Request, session: AsyncSession = Depends(get_session)):

    profile = await svc.get_profile(session)
    ctx = {
        "active": "profile",
        "profile": profile,
        "cvs": await svc.list_cvs(session),
        "evidence": await svc.list_evidence(session),
        "default_evidence_count": len(DEFAULT_EVIDENCE),
        "categories": CATEGORIES,
        "ai_ready": get_settings().ai_ready,
        "ok": request.query_params.get("ok"),
        "err": request.query_params.get("err"),
        **await shell_context(session),
    }
    return templates.TemplateResponse(request, "pages/profile.html", ctx)


# ---------- data diri ----------

@router.post("")
async def save_profile(
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


# ---------- CV ----------

async def _cv(session: AsyncSession, cv_id: int) -> CvDocument:
    cv = await session.get(CvDocument, cv_id)
    if cv is None:
        raise HTTPException(404, "CV tidak ditemukan")
    return cv


@router.post("/cv")
async def upload_cv(
    session: AsyncSession = Depends(get_session),
    file: UploadFile = File(...),
    title: str = Form(""),
):
    profile = await svc.get_profile(session)
    if profile is None:
        return _back("data", "Isi data diri dulu sebelum upload CV.", error=True)
    data = await file.read(svc.MAX_CV_BYTES + 1)
    try:
        cv = await svc.save_uploaded_cv(session, profile, file.filename or "cv.pdf", data, title)
    except svc.ProfileError as exc:
        return _back("cv", str(exc), error=True)
    note = "" if cv.parsed_text else " Teksnya tidak terbaca (PDF hasil scan?), AI tidak bisa memakainya."
    return _back("cv", f"CV \"{cv.title}\" terunggah.{note}")


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
async def make_default(cv_id: int, session: AsyncSession = Depends(get_session)):
    cv = await _cv(session, cv_id)
    await svc.set_default_cv(session, cv)
    return _back("cv", f"\"{cv.title}\" jadi CV default.")


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
async def cv_file(cv_id: int, session: AsyncSession = Depends(get_session)):
    cv = await _cv(session, cv_id)
    path = svc.cv_path(cv)
    if not path.is_file():
        raise HTTPException(404, "File CV tidak ada di storage")
    return FileResponse(path, media_type="application/pdf", filename=f"{cv.title}.pdf",
                        content_disposition_type="inline")


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


@router.post("/evidence/import-defaults")
async def import_defaults(session: AsyncSession = Depends(get_session)):
    profile = await svc.get_profile(session)
    if profile is None:
        return _back("data", "Isi data diri dulu.", error=True)
    added = await svc.import_default_texts(session, profile)
    return _back("bukti", f"Contoh bawaan disalin ({added} bukti). Sekarang bisa diedit.")


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
