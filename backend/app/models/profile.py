from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, str_enum
from app.models.enums import RoleCategory


class UserProfile(TimestampMixin, Base):
    """Data diri untuk surat lamaran. Diisi lewat halaman Profil, bukan di-hardcode di repo."""

    __tablename__ = "user_profile"

    id: Mapped[int] = mapped_column(primary_key=True)
    full_name: Mapped[str] = mapped_column(String(120))
    email: Mapped[str] = mapped_column(String(255))
    phone: Mapped[str | None] = mapped_column(String(30))
    address: Mapped[str | None] = mapped_column(String(255))
    linkedin_url: Mapped[str | None] = mapped_column(String(255))
    portfolio_url: Mapped[str | None] = mapped_column(String(255))
    summary: Mapped[str | None] = mapped_column(Text)
    skills: Mapped[list[str]] = mapped_column(ARRAY(String(60)), default=list)
    experience_years: Mapped[int] = mapped_column(Integer, default=0)
    # Target pencarian dari CV aktif (bisa diedit di Profil): bidang lowongan & keyword scraping.
    target_categories: Mapped[list[str]] = mapped_column(ARRAY(String(20)), default=list, server_default="{}")
    target_keywords: Mapped[list[str]] = mapped_column(ARRAY(String(80)), default=list, server_default="{}")
    preferred_language: Mapped[str] = mapped_column(String(2), default="id")
    signature: Mapped[str | None] = mapped_column(Text)

    # Kalimat untuk surat lamaran template (paragraf pembuka & penutup).
    intro_id: Mapped[str | None] = mapped_column(Text)
    intro_en: Mapped[str | None] = mapped_column(Text)
    strength_id: Mapped[str | None] = mapped_column(Text)
    strength_en: Mapped[str | None] = mapped_column(Text)


class LetterEvidence(TimestampMixin, Base):
    """Satu bukti konkret dari CV yang bisa dipakai di surat lamaran template."""

    __tablename__ = "letter_evidence"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user_profile.id", ondelete="CASCADE"), index=True)
    text_id: Mapped[str] = mapped_column(Text)
    text_en: Mapped[str | None] = mapped_column(Text)
    categories: Mapped[list[str]] = mapped_column(ARRAY(String(20)), default=list)
    skills: Mapped[list[str]] = mapped_column(ARRAY(String(60)), default=list)
    position: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(default=True)
    # True = dibuat otomatis dari scan CV (diganti saat CV utama berganti); False = ditulis manual.
    from_cv: Mapped[bool] = mapped_column(default=False, server_default="false")


class CvDocument(TimestampMixin, Base):
    __tablename__ = "cv_document"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user_profile.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(String(120))
    role_category: Mapped[RoleCategory | None] = mapped_column(str_enum(RoleCategory))
    file_path: Mapped[str] = mapped_column(String(500))
    parsed_text: Mapped[str | None] = mapped_column(Text)
    is_default: Mapped[bool] = mapped_column(default=False)
    # Hasil scan CV (data diri, pengalaman, proyek, pendidikan, bukti, kalimat surat) — lihat services/cv_scan.py
    scan_data: Mapped[dict | None] = mapped_column(JSONB)


class CvReview(TimestampMixin, Base):
    """Hasil Skor CV. File-nya tidak disimpan — hanya teks yang terbaca, hasil penilaian, dan kesimpulan AI."""

    __tablename__ = "cv_review"

    id: Mapped[int] = mapped_column(primary_key=True)
    filename: Mapped[str] = mapped_column(String(200))
    file_type: Mapped[str] = mapped_column(String(8))          # pdf | docx
    source: Mapped[str] = mapped_column(String(20), default="upload")  # upload | profile (CV utama)
    method: Mapped[str] = mapped_column(String(10), default="text")    # text | ocr
    text: Mapped[str | None] = mapped_column(Text)
    scan_data: Mapped[dict | None] = mapped_column(JSONB)      # hasil pemetaan CV (CvScan.to_data)
    report: Mapped[dict | None] = mapped_column(JSONB)         # skor per kriteria (services/cv_score.py)
    ai_review: Mapped[dict | None] = mapped_column(JSONB)      # kesimpulan AI yang lolos pengecekan
    score: Mapped[int | None] = mapped_column(Integer)
    # Kecocokan dengan satu lowongan (opsional): teks yang ditempel / lowongan dari daftar, hasil pencocokan & skornya.
    job_id: Mapped[int | None] = mapped_column(ForeignKey("job.id", ondelete="SET NULL"))
    job_title: Mapped[str | None] = mapped_column(String(255))
    job_text: Mapped[str | None] = mapped_column(Text)
    job_match: Mapped[dict | None] = mapped_column(JSONB)
    job_score: Mapped[int | None] = mapped_column(Integer)
