import re
import uuid
from pathlib import Path

from pypdf import PdfReader
from pypdf.errors import PdfReadError
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models import Application, CvDocument, LetterEvidence, UserProfile
from app.models.enums import ApplicationStatus
from app.services.letter_template import Evidence

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


def cv_owner(cv: CvDocument | None) -> str:
    """Nama pemilik CV menurut hasil scan (dinormalisasi), "" bila tidak diketahui."""
    name = ((cv.scan_data or {}).get("fields") or {}).get("full_name") if cv else ""
    return re.sub(r"[^a-z]", "", (name or "").lower())


async def default_cv(session: AsyncSession, role_category: str | None = None) -> CvDocument | None:
    cvs = await list_cvs(session)
    main = next((c for c in cvs if c.is_default), cvs[0] if cvs else None)
    if role_category:
        owner = cv_owner(main)
        for cv in cvs:
            # CV khusus kategori hanya dipakai bila milik orang yang sama dengan CV utama.
            if (cv.role_category and cv.role_category.value == role_category
                    and (not owner or not cv_owner(cv) or cv_owner(cv) == owner)):
                return cv
    return main


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


_FIELD_LABELS = {
    "full_name": "nama", "email": "email", "phone": "no. HP", "address": "domisili",
    "linkedin_url": "LinkedIn", "portfolio_url": "portofolio", "summary": "ringkasan",
}
_MAX_LEN = {"full_name": 120, "email": 255, "phone": 30, "address": 255, "linkedin_url": 255, "portfolio_url": 255}


def apply_cv_fields(profile: UserProfile, fields: dict, overwrite: bool = False) -> list[str]:
    """Isi Profil dari hasil scan CV. Tanpa overwrite hanya kolom kosong yang diisi (skill digabung).

    Mengembalikan label kolom yang berubah.
    """
    changed: list[str] = []
    for key, label in _FIELD_LABELS.items():
        value = (fields.get(key) or "").strip()
        if not value:
            continue
        if key.endswith("_url") and not re.match(r"^https?://", value):
            value = f"https://{value}"
        value = value[: _MAX_LEN[key]] if key in _MAX_LEN else value
        current = getattr(profile, key)
        if (overwrite or not current) and current != value:
            setattr(profile, key, value)
            changed.append(label)
    skills = fields.get("skills") or []
    if skills:
        old = list(profile.skills or [])
        new = list(skills) if overwrite else old + [x for x in skills if x.lower() not in {o.lower() for o in old}]
        if new != old:
            profile.skills = new
            changed.append("skill")
    if "experience_years" in fields:  # pola sederhana tidak mengisi kunci ini bila tidak tertulis eksplisit
        years = int(fields.get("experience_years") or 0)
        if (overwrite or not profile.experience_years) and profile.experience_years != years:
            profile.experience_years = years
            changed.append("lama pengalaman")
    return changed


async def sync_profile_from_cv(session: AsyncSession, profile: UserProfile, cv: CvDocument) -> list[str]:
    """Jadikan CV ini CV utama dan perbarui Profil sesuai isinya (data diri, kalimat surat, bukti CV).

    * CV utama BERGANTI (dokumen lain): Profil disusun ulang sepenuhnya dari CV ini. Yang tidak tertulis di
      CV ini dikosongkan — tidak boleh ada sisa data CV sebelumnya (email, HP, bukti, kalimat surat).
    * CV yang sama dipindai ulang: hanya kolom yang terbaca yang diperbarui; isian manual (mis. email yang
      diketik sendiri karena tidak ada di CV) dipertahankan.
    Tidak commit — pemanggil yang commit.
    """
    from app.services.cv_scan import CvScan, total_experience
    from app.services.cv_targets import derive_targets

    scan = CvScan.from_data(cv.scan_data or {})
    cvs = await list_cvs(session)
    previous_main = next((c for c in cvs if c.is_default), None)
    switched = previous_main is None or previous_main.id != cv.id or not profile.full_name
    # Orang lain? Dibandingkan dari nama, email, dan LinkedIn (cukup satu yang berbeda). Nama saja tidak cukup:
    # nama bisa gagal terbaca dari CV.
    other_person = switched and _different_person(profile, scan.fields)
    summary = total_experience(scan.experiences, scan.projects)
    if summary["counted"]:  # hasil scan lama pun memakai perhitungan dari tanggal di CV (termasuk magang)
        scan.fields = {**scan.fields, "experience_years": summary["years"]}
    for other in cvs:
        other.is_default = other.id == cv.id
    if switched:
        # Profil = isi CV ini. Yang tidak tertulis → kosong (bukan bekas CV sebelumnya).
        for key in ("full_name", "email", "phone", "address", "linkedin_url", "portfolio_url", "summary", "signature"):
            setattr(profile, key, "" if key in ("full_name", "email") else None)
        profile.skills = []
        profile.experience_years = 0
    changed = apply_cv_fields(profile, scan.fields, overwrite=True)
    if switched:
        missing = [label for key, label in (("full_name", "nama"), ("email", "email"), ("phone", "nomor HP"),
                                            ("address", "domisili")) if not getattr(profile, key)]
        if missing:
            changed.append("tidak tertulis di CV, isi manual bila perlu: " + ", ".join(missing))
    if other_person:
        missing = [label for key, label in (("email", "email"), ("phone", "nomor HP"), ("address", "domisili"))
                   if not getattr(profile, key)]
        if missing:
            changed.append("ganti orang — belum ada di CV, isi manual: " + ", ".join(missing))

    letter = scan.letter or {}
    if any(letter.get(k) for k in ("intro_id", "strength_id")) or switched:
        # CV orang lain: kalimat surat orang sebelumnya tidak boleh terbawa (dikosongkan bila CV ini tidak punya).
        for key in ("intro_id", "intro_en", "strength_id", "strength_en"):
            setattr(profile, key, letter.get(key) or None)
        changed.append("kalimat surat")
    if other_person:
        # Bukti yang ditulis manual untuk orang sebelumnya dinonaktifkan (tidak dihapus, bisa diaktifkan lagi).
        await session.execute(update(LetterEvidence).where(LetterEvidence.from_cv.is_(False))
                              .values(is_active=False))

    plan = derive_targets(scan)
    if plan.categories or switched:
        profile.target_categories = plan.categories
        profile.target_keywords = plan.keywords
        changed.append("target pencarian")

    if scan.evidence or switched:  # bukti dari CV sebelumnya tidak boleh tersisa
        await session.execute(delete(LetterEvidence).where(LetterEvidence.from_cv.is_(True)))
        for pos, e in enumerate(scan.evidence):
            session.add(LetterEvidence(
                user_id=profile.id, text_id=e["text_id"], text_en=e.get("text_en") or None,
                categories=list(e.get("categories") or []), skills=list(e.get("skills") or []),
                position=-100 + pos, is_active=True, from_cv=True,  # di atas bukti manual
            ))
        changed.append(f"{len(scan.evidence)} bukti CV")
    return changed


async def apply_conclusions(session: AsyncSession, profile: UserProfile, scan) -> list[str]:
    """Kesimpulan AI (sudah diverifikasi ke teks CV) → Profil. Data diri tidak disentuh. Tidak commit."""
    from app.services.cv_targets import derive_targets

    changed: list[str] = []
    summary = (scan.fields or {}).get("summary")
    if summary and scan.ai:
        profile.summary = summary
        changed.append("ringkasan")
    letter = scan.letter or {}
    if any(letter.get(k) for k in ("intro_id", "intro_en", "strength_id", "strength_en")):
        for key in ("intro_id", "intro_en", "strength_id", "strength_en"):
            setattr(profile, key, letter.get(key) or None)
        changed.append("kalimat surat")
    if scan.evidence:
        await session.execute(delete(LetterEvidence).where(LetterEvidence.from_cv.is_(True)))
        for pos, e in enumerate(scan.evidence):
            session.add(LetterEvidence(
                user_id=profile.id, text_id=e["text_id"], text_en=e.get("text_en") or None,
                categories=list(e.get("categories") or []), skills=list(e.get("skills") or []),
                position=-100 + pos, is_active=True, from_cv=True,
            ))
        changed.append(f"{len(scan.evidence)} bukti CV")
    plan = derive_targets(scan)
    if plan.categories:
        profile.target_categories, profile.target_keywords = plan.categories, plan.keywords
        changed.append("target pencarian")
    return changed


def _different_person(profile: UserProfile, fields: dict) -> bool:
    """Beda orang bila salah satu penanda (nama, email, LinkedIn) ada di keduanya dan berbeda,
    atau bila tidak ada satu pun penanda yang bisa dibandingkan (tidak bisa dipastikan sama)."""
    def name(v):
        return re.sub(r"[^a-z]", "", (v or "").lower())

    def handle(v):
        m = re.search(r"linkedin\.com/in/([^/?#\s]+)", v or "", re.I)
        return m.group(1).lower() if m else ""

    pairs = [(name(profile.full_name), name(fields.get("full_name"))),
             ((profile.email or "").lower(), (fields.get("email") or "").lower()),
             (handle(profile.linkedin_url), handle(fields.get("linkedin_url")))]
    comparable = [(a, b) for a, b in pairs if a and b]
    return not comparable or any(a != b for a, b in comparable)


async def replace_old_cvs(session: AsyncSession, keep: CvDocument) -> tuple[list[str], list[str]]:
    """Hapus semua CV selain `keep` (file di storage ikut terhapus).

    Draf & lamaran gagal yang memakai CV lama dipindah ke CV baru. CV yang sedang dipakai lamaran
    di antrean kirim tidak dihapus. Mengembalikan (judul terhapus, judul dilewati). Commit di sini.
    """
    deleted, skipped, files = [], [], []
    for old in await list_cvs(session):
        if old.id == keep.id:
            continue
        queued = await session.scalar(select(func.count(Application.id)).where(
            Application.cv_id == old.id, Application.status == ApplicationStatus.QUEUED))
        if queued:
            skipped.append(old.title)
            continue
        await session.execute(update(Application).where(
            Application.cv_id == old.id,
            Application.status.in_([ApplicationStatus.DRAFT, ApplicationStatus.FAILED]),
        ).values(cv_id=keep.id))
        files.append(cv_path(old))
        deleted.append(old.title)
        await session.delete(old)
    keep.is_default = True
    await session.commit()
    root = str(Path(get_settings().storage_dir))
    for path in files:
        if path.is_file() and str(path).startswith(root):
            path.unlink()
    return deleted, skipped


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


async def ensure_targets(session: AsyncSession) -> bool:
    """Profil tanpa target (data lama) → turunkan dari scan CV utama. True bila diisi."""
    from app.services.cv_scan import CvScan
    from app.services.cv_targets import derive_targets

    profile = await get_profile(session)
    if profile is None or profile.target_categories:
        return False
    cv = await default_cv(session)
    if cv is None or not cv.scan_data:
        return False
    plan = derive_targets(CvScan.from_data(cv.scan_data))
    if not plan.categories:
        return False
    profile.target_categories, profile.target_keywords = plan.categories, plan.keywords
    await session.commit()
    return True


def split_list(value: str | None) -> list[str]:
    return [s.strip() for s in re.split(r"[,\n]", value or "") if s.strip()]
