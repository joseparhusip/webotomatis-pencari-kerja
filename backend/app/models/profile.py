from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import ARRAY
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


class CvDocument(TimestampMixin, Base):
    __tablename__ = "cv_document"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user_profile.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(String(120))
    role_category: Mapped[RoleCategory | None] = mapped_column(str_enum(RoleCategory))
    file_path: Mapped[str] = mapped_column(String(500))
    parsed_text: Mapped[str | None] = mapped_column(Text)
    is_default: Mapped[bool] = mapped_column(default=False)
