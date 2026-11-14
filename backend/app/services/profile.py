import re
import uuid
from pathlib import Path

from pypdf import PdfReader
from pypdf.errors import PdfReadError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models import Application, CvDocument, LetterEvidence, UserProfile
from app.models.enums import ApplicationStatus
from app.services.letter_template import (
    DEFAULT_EVIDENCE,
    DEFAULT_INTRO_EN,
    DEFAULT_INTRO_ID,
    DEFAULT_STRENGTH_EN,
    DEFAULT_STRENGTH_ID,
    Evidence,
)

MAX_CV_BYTES = 5 * 1024 * 1024
_SAFE_NAME = re.compile(r"[^A-Za-z0-9 ._-]+")


class ProfileError(Exception):
    pass


async def get_profile(session: AsyncSession) -> UserProfile | None:
    """Aplikasi single-user: profil pertama adalah pemiliknya."""
    return await session.scalar(select(UserProfile).order_by(UserProfile.id).limit(1))


# ---------- CV ----------

async def list_cvs(session: AsyncSession) -> list[CvDocument]:
    return list(await session.scalars(select(CvDocument).order_by(CvDocument.is_default.desc(), CvDocument.id)))


async def default_cv(session: AsyncSession, role_category: str | None = None) -> CvDocument | None:
    cvs = await list_cvs(session)
    if role_category:
        for cv in cvs:
            if cv.role_category and cv.role_category.value == role_category:
                return cv
    return next((c for c in cvs if c.is_default), cvs[0] if cvs else None)


def cv_path(cv: CvDocument) -> Path:
    """file_path disimpan relatif terhadap STORAGE_DIR supaya sama di Docker & lokal."""
    p = Path(cv.file_path)
    return p if p.is_absolute() else Path(get_settings().storage_dir) / p


def extract_pdf_text(path: Path) -> str:
    reader = PdfReader(str(path))
    return "\n".join((page.extract_text() or "") for page in reader.pages).strip()


async def save_uploaded_cv(
    session: AsyncSession, profile: UserProfile, filename: str, data: bytes, title: str
) -> CvDocument:
    if not data:
        raise ProfileError("File kosong.")
    if len(data) > MAX_CV_BYTES:
        raise ProfileError(f"File terlalu besar ({len(data) // 1024} KB). Maksimal 5 MB.")
    if not data.startswith(b"%PDF"):
        raise ProfileError("File harus PDF.")

    safe = _SAFE_NAME.sub("", Path(filename or "cv.pdf").stem).strip() or "cv"
    rel = f"cv/{uuid.uuid4().hex[:8]}-{safe[:60]}.pdf"
    target = Path(get_settings().storage_dir) / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    try:
        text = extract_pdf_text(target)
    except (PdfReadError, ValueError) as exc:
        target.unlink(missing_ok=True)
        raise ProfileError(f"PDF tidak bisa dibaca: {exc}") from exc

    has_default = await session.scalar(select(func.count(CvDocument.id)).where(CvDocument.is_default))
    cv = CvDocument(user_id=profile.id, title=title.strip() or safe, file_path=rel,
                    parsed_text=text, is_default=not has_default)
    session.add(cv)
    await session.commit()
    return cv


async def set_default_cv(session: AsyncSession, cv: CvDocument) -> None:
    for other in await list_cvs(session):
        other.is_default = other.id == cv.id
    await session.commit()


async def delete_cv(session: AsyncSession, cv: CvDocument) -> None:
    queued = await session.scalar(
        select(func.count(Application.id)).where(
            Application.cv_id == cv.id, Application.status == ApplicationStatus.QUEUED
        )
    )
    if queued:
        raise ProfileError("CV ini sedang dipakai lamaran yang ada di antrean kirim.")
    path = cv_path(cv)
    was_default = cv.is_default
    await session.delete(cv)
    await session.flush()
    if was_default:
        remaining = await list_cvs(session)
        if remaining:
            remaining[0].is_default = True
    await session.commit()
    if path.is_file() and str(path).startswith(str(Path(get_settings().storage_dir))):
        path.unlink()


# ---------- Bukti CV untuk surat template ----------

async def list_evidence(session: AsyncSession) -> list[LetterEvidence]:
    return list(await session.scalars(select(LetterEvidence).order_by(LetterEvidence.position, LetterEvidence.id)))


async def evidence_pool(session: AsyncSession) -> list[Evidence] | None:
    """Bukti aktif dari Profil; None bila Profil belum punya bukti (template memakai contoh bawaan)."""
    rows = await list_evidence(session)
    if not rows:
        return None
    return [
        Evidence(r.text_id, r.text_en or "", frozenset(r.categories or []), frozenset(r.skills or []))
        for r in rows if r.is_active
    ]


async def import_default_texts(session: AsyncSession, profile: UserProfile) -> int:
    """Salin contoh bawaan ke Profil supaya bisa diedit. Tidak menimpa isian yang sudah ada."""
    profile.intro_id = profile.intro_id or DEFAULT_INTRO_ID
    profile.intro_en = profile.intro_en or DEFAULT_INTRO_EN
    profile.strength_id = profile.strength_id or DEFAULT_STRENGTH_ID
    profile.strength_en = profile.strength_en or DEFAULT_STRENGTH_EN
    added = 0
    if not await list_evidence(session):
        for pos, e in enumerate(DEFAULT_EVIDENCE):
            session.add(LetterEvidence(
                user_id=profile.id, text_id=e.id, text_en=e.en, categories=sorted(e.categories),
                skills=sorted(e.skills), position=pos, is_active=True,
            ))
            added += 1
    await session.commit()
    return added


def split_list(value: str | None) -> list[str]:
    return [s.strip() for s in re.split(r"[,\n]", value or "") if s.strip()]
