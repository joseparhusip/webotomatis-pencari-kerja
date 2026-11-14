from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, str_enum
from app.models.enums import ApplicationStatus, ApplyMode, EmailDirection


class Application(TimestampMixin, Base):
    __tablename__ = "application"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("job.id", ondelete="CASCADE"), index=True)
    cv_id: Mapped[int | None] = mapped_column(ForeignKey("cv_document.id", ondelete="SET NULL"))

    job: Mapped["Job"] = relationship(lazy="joined")  # noqa: F821
    cv: Mapped["CvDocument | None"] = relationship(lazy="joined")  # noqa: F821
    cover_letter_text: Mapped[str | None] = mapped_column(Text)
    cover_letter_pdf_path: Mapped[str | None] = mapped_column(String(500))
    email_subject: Mapped[str | None] = mapped_column(String(255))
    sent_to: Mapped[str | None] = mapped_column(String(255))
    apply_mode: Mapped[ApplyMode] = mapped_column(str_enum(ApplyMode))
    status: Mapped[ApplicationStatus] = mapped_column(
        str_enum(ApplicationStatus), default=ApplicationStatus.DRAFT, index=True
    )
    gmail_message_id: Mapped[str | None] = mapped_column(String(120))
    gmail_thread_id: Mapped[str | None] = mapped_column(String(120))
    letter_language: Mapped[str] = mapped_column(String(2), default="id")
    letter_source: Mapped[str | None] = mapped_column(String(20))  # "ai" | "template" | "manual"
    last_error: Mapped[str | None] = mapped_column(Text)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_reply_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    notes: Mapped[str | None] = mapped_column(Text)


class EmailLog(Base):
    __tablename__ = "email_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    application_id: Mapped[int] = mapped_column(
        ForeignKey("application.id", ondelete="CASCADE"), index=True
    )
    direction: Mapped[EmailDirection] = mapped_column(str_enum(EmailDirection))
    subject: Mapped[str | None] = mapped_column(String(255))
    snippet: Mapped[str | None] = mapped_column(Text)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
