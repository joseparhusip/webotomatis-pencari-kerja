"""Simpan hasil connector ke DB: dedup lintas sumber, klasifikasi, skor sementara."""

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.base import JobIn
from app.models import Job, JobListing, Source
from app.models.enums import ApplyMode
from app.services.parsing import dedup_hash, extract_hr_email
from app.services.targeting import classify_role, detect_level, level_from_years, score_job


async def ingest(session: AsyncSession, source: Source, items: list[JobIn], now: datetime) -> int:
    """Simpan item. Kembalikan jumlah lowongan unik yang baru."""
    new_count = 0
    for item in items:
        if not item.title or not item.company_name:
            continue

        listing = await session.scalar(
            select(JobListing).where(
                JobListing.source_id == source.id, JobListing.external_id == item.external_id
            )
        )
        if listing:
            listing.raw_payload = item.raw
            listing.scraped_at = now
            if item.is_complete:
                # Sumber yang hasil pencariannya sudah lengkap (Kalibrr): ikuti perubahan iklan.
                job = await session.get(Job, listing.job_id)
                _apply_item(job, item, overwrite=True)
                job.detail_fetched_at = listing.detail_fetched_at = now
                rescore(job)
            continue

        h = dedup_hash(item.company_name, item.title, item.location_city)
        try:
            async with session.begin_nested():
                job = await session.scalar(select(Job).where(Job.dedup_hash == h))
                is_new = job is None
                if is_new:
                    job = Job(title=item.title, company_name=item.company_name, apply_url=item.url,
                              dedup_hash=h, skills=[], required_skills=[], benefits=[])
                    session.add(job)
                _apply_item(job, item, overwrite=is_new)
                if item.is_complete:
                    job.detail_fetched_at = now
                rescore(job)
                session.add(
                    JobListing(
                        job=job,
                        source_id=source.id,
                        external_id=item.external_id,
                        url=item.url,
                        raw_payload=item.raw,
                        scraped_at=now,
                        detail_fetched_at=now if item.is_complete else None,
                    )
                )
        except IntegrityError:
            # Proses scraping lain menyimpan lowongan yang sama barusan — lewati, akan tersambung di run berikutnya.
            continue
        if is_new:
            new_count += 1
    return new_count


_SCALARS = ("company_logo_url", "location_city", "location_province", "work_mode", "employment_type",
            "salary_min", "salary_max", "salary_currency", "posted_at", "expires_at",
            "education", "experience_min", "experience_max", "source_category")


def _apply_item(job: Job, item: JobIn, overwrite: bool) -> None:
    """Lowongan baru: isi semua. Lowongan sama dari sumber lain: hanya lengkapi yang kosong,
    kecuali sumber ini punya data lengkap (halaman detail) sementara yang tersimpan belum."""
    complete_upgrade = item.is_complete and job.detail_fetched_at is None
    for field in _SCALARS:
        value = getattr(item, field)
        if value is not None and (overwrite or getattr(job, field) is None):
            setattr(job, field, value)
    if overwrite or complete_upgrade or (item.description and not job.description):
        job.description = item.description
        job.requirements = item.requirements
    if item.required_skills and (overwrite or not job.required_skills):
        job.required_skills = item.required_skills
    if item.benefits and (overwrite or not job.benefits):
        job.benefits = item.benefits
    if item.experience_level and (overwrite or job.experience_level is None):
        job.experience_level = item.experience_level


def score_text(job: Job) -> str:
    return "\n".join(filter(None, [job.title, job.description, job.requirements, ", ".join(job.required_skills or [])]))


def rescore(job: Job) -> None:
    """Kategori, level, skor, dan email HR dihitung ulang dari data yang tersimpan."""
    text = score_text(job)
    job.role_category = classify_role(job.title, text, job.source_category)
    job.experience_level = (level_from_years(job.experience_min) or detect_level(job.title, text)
                            or job.experience_level)
    job.match_score, job.match_reason, job.skills = score_job(job.role_category, job.experience_level, text)
    email = extract_hr_email(job.description, job.requirements)
    if email:
        job.hr_email = email
        job.apply_mode = ApplyMode.EMAIL
