from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class InstagramAccount(TimestampMixin, Base):
    """Akun Instagram info loker yang dipantau (diatur di halaman Pengaturan)."""

    __tablename__ = "instagram_account"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(60), unique=True)
    is_active: Mapped[bool] = mapped_column(default=True)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    jobs_found: Mapped[int] = mapped_column(Integer, default=0)


class InstagramPost(Base):
    """Postingan yang sudah dibaca AI — supaya tidak diproses (dan dibayar) dua kali."""

    __tablename__ = "instagram_post"

    media_id: Mapped[str] = mapped_column(String(60), primary_key=True)
    username: Mapped[str] = mapped_column(String(60), index=True)
    permalink: Mapped[str | None] = mapped_column(String(300))
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    processed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    jobs_found: Mapped[int] = mapped_column(Integer, default=0)
    note: Mapped[str | None] = mapped_column(Text)  # mis. "bukan lowongan" / alasan gagal
