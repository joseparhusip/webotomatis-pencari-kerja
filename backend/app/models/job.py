from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, str_enum
from app.models.enums import (
    ApplyMode,
    EmploymentType,
    ExperienceLevel,
    JobStatus,
    RoleCategory,
    SourceType,
    WorkMode,
)


class Source(Base):
    __tablename__ = "source"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(40), unique=True)
    base_url: Mapped[str] = mapped_column(String(255))
    type: Mapped[SourceType] = mapped_column(str_enum(SourceType))
    is_active: Mapped[bool] = mapped_column(default=True)
    rate_limit_per_min: Mapped[int] = mapped_column(Integer, default=20)


class Job(TimestampMixin, Base):
    """Lowongan hasil dedup — satu baris per lowongan unik, apa pun sumbernya."""

    __tablename__ = "job"
    __table_args__ = (
        Index("ix_job_posted_at", "posted_at"),
        Index("ix_job_match_score", "match_score"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(255))
    company_name: Mapped[str] = mapped_column(String(255))
    company_logo_url: Mapped[str | None] = mapped_column(String(500))
    role_category: Mapped[RoleCategory] = mapped_column(
        str_enum(RoleCategory), default=RoleCategory.OTHER, index=True
    )

    location_city: Mapped[str | None] = mapped_column(String(120))
    location_province: Mapped[str | None] = mapped_column(String(120))
    work_mode: Mapped[WorkMode | None] = mapped_column(str_enum(WorkMode))
    employment_type: Mapped[EmploymentType | None] = mapped_column(str_enum(EmploymentType))
    experience_level: Mapped[ExperienceLevel | None] = mapped_column(str_enum(ExperienceLevel))

    salary_min: Mapped[int | None] = mapped_column(BigInteger)
    salary_max: Mapped[int | None] = mapped_column(BigInteger)
    salary_currency: Mapped[str | None] = mapped_column(String(3))
    salary_is_estimated: Mapped[bool] = mapped_column(default=False)

    description: Mapped[str | None] = mapped_column(Text)
    requirements: Mapped[str | None] = mapped_column(Text)
    skills: Mapped[list[str]] = mapped_column(ARRAY(String(60)), default=list)  # skill CV yang cocok (untuk skor)

    # Data apa adanya dari situs sumber (halaman detail).
    required_skills: Mapped[list[str]] = mapped_column(ARRAY(String(80)), default=list, server_default="{}")
    benefits: Mapped[list[str]] = mapped_column(ARRAY(String(120)), default=list, server_default="{}")
    education: Mapped[str | None] = mapped_column(String(80))
    source_category: Mapped[str | None] = mapped_column(String(200))  # kategori asli dari situs (petunjuk klasifikasi)
    experience_min: Mapped[int | None] = mapped_column(Integer)
    experience_max: Mapped[int | None] = mapped_column(Integer)
    detail_fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    hr_email: Mapped[str | None] = mapped_column(String(255))
    hr_whatsapp: Mapped[str | None] = mapped_column(String(30))
    apply_url: Mapped[str] = mapped_column(String(1000))
    apply_mode: Mapped[ApplyMode] = mapped_column(str_enum(ApplyMode), default=ApplyMode.PORTAL)

    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    match_score: Mapped[int | None] = mapped_column(Integer)
    match_reason: Mapped[str | None] = mapped_column(Text)

    dedup_hash: Mapped[str] = mapped_column(String(40), unique=True)
    status: Mapped[JobStatus] = mapped_column(
        str_enum(JobStatus), default=JobStatus.NEW, index=True
    )

    listings: Mapped[list["JobListing"]] = relationship(
        back_populates="job", cascade="all, delete-orphan", lazy="selectin"
    )


class JobListing(Base):
    """Data mentah per sumber. Satu Job bisa punya beberapa listing (JobStreet + Kalibrr, dst)."""

    __tablename__ = "job_listing"
    __table_args__ = (UniqueConstraint("source_id", "external_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("job.id", ondelete="CASCADE"), index=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("source.id"))
    external_id: Mapped[str] = mapped_column(String(120))
    url: Mapped[str] = mapped_column(String(1000))
    raw_payload: Mapped[dict] = mapped_column(JSONB)
    scraped_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    detail_fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    detail_error: Mapped[str | None] = mapped_column(Text)

    job: Mapped[Job] = relationship(back_populates="listings")
    source: Mapped[Source] = relationship(lazy="joined")


class ScrapeRun(Base):
    __tablename__ = "scrape_run"

    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("source.id"))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    jobs_found: Mapped[int] = mapped_column(Integer, default=0)
    jobs_new: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)

    source: Mapped[Source] = relationship(lazy="joined")


class SavedFilter(Base):
    __tablename__ = "saved_filter"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user_profile.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(80))
    query: Mapped[dict] = mapped_column(JSONB)
    notify: Mapped[bool] = mapped_column(default=False)
