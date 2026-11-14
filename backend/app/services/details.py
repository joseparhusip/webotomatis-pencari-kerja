"""Ambil halaman detail lowongan di situs sumber supaya isi lowongan sama dengan aslinya."""

import asyncio
import logging
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors import REGISTRY, Connector
from app.connectors.base import JobDetail, new_http_client
from app.core.config import get_settings
from app.models import Job, JobListing, Source
from app.models.enums import JobStatus
from app.services.ingest import rescore

log = logging.getLogger(__name__)

REFRESH_AFTER = timedelta(hours=48)  # detail diperbarui berkala: iklan bisa diedit/ditutup perusahaan
PER_RUN_LIMIT = 400                 # per sumber per scraping (JobStreet kategori IT bisa ratusan per 3 hari)


def apply_detail(job: Job, listing: JobListing, d: JobDetail, now: datetime) -> None:
    listing.detail_fetched_at = now
    listing.detail_error = None
    if d.closed:
        # Ditutup di situs sumber → dianggap kedaluwarsa, otomatis hilang dari daftar.
        job.expires_at = min(job.expires_at or now, now)
        job.detail_fetched_at = now
        return

    # Teks lowongan diganti apa adanya dari halaman detail (termasuk bila kualifikasi kosong
    # karena situs menuliskannya di dalam deskripsi) supaya tidak ada sisa ringkasan lama.
    if d.description is not None or d.requirements is not None:
        job.description = d.description
        job.requirements = d.requirements
    # Halaman detail = sumber kebenaran: daftar kosong berarti situs memang tidak menampilkannya.
    job.required_skills = list(d.required_skills)
    job.benefits = list(d.benefits)
    for field in ("education", "experience_min", "experience_max", "work_mode", "employment_type",
                  "location_city", "location_province", "posted_at", "expires_at"):
        value = getattr(d, field)
        if value is not None:
            setattr(job, field, value)
    if d.salary_min is not None or d.salary_max is not None:
        job.salary_min, job.salary_max, job.salary_currency = d.salary_min, d.salary_max, "IDR"
    job.detail_fetched_at = now
    rescore(job)


async def fetch_for_listing(
    session: AsyncSession, client: httpx.AsyncClient, connector: Connector, listing: JobListing
) -> bool:
    """Ambil & terapkan detail satu listing. True bila berhasil."""
    job = await session.get(Job, listing.job_id)
    now = datetime.now(UTC)
    try:
        detail = await connector.fetch_detail(client, listing.url, listing.external_id, listing.raw_payload or {})
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
        listing.detail_fetched_at = now  # dicoba lagi setelah REFRESH_AFTER
        listing.detail_error = f"{type(exc).__name__}: {exc}"[:500]
        log.warning("detail %s %s gagal: %s", connector.name, listing.external_id, exc)
        await session.commit()
        return False
    if detail is not None:
        apply_detail(job, listing, detail, now)
    await session.commit()
    return detail is not None


async def refresh_job(session: AsyncSession, job: Job) -> bool:
    """Dipakai saat lowongan dibuka di UI: ambil detail dari sumber yang mendukung."""
    settings = get_settings()
    ok = False
    async with new_http_client(settings.http_timeout_seconds) as client:
        for listing in list(job.listings):
            connector = REGISTRY.get(listing.source.name)
            if connector and connector.supports_detail:
                ok = await fetch_for_listing(session, client, connector, listing) or ok
                if ok:
                    break
    await session.refresh(job)
    return ok


def needs_detail(job: Job) -> bool:
    if not any((REGISTRY.get(l.source.name) or Connector).supports_detail for l in job.listings):
        return False
    return job.detail_fetched_at is None or job.detail_fetched_at < datetime.now(UTC) - REFRESH_AFTER


async def backfill(session: AsyncSession, source: Source, connector: Connector,
                   client: httpx.AsyncClient, limit: int = PER_RUN_LIMIT) -> tuple[int, int]:
    """Lengkapi listing yang belum punya detail / detailnya sudah lama. Kembalikan (berhasil, gagal)."""
    if not connector.supports_detail:
        return 0, 0
    now = datetime.now(UTC)
    rows = list(await session.scalars(
        select(JobListing).join(Job, Job.id == JobListing.job_id)
        .where(
            JobListing.source_id == source.id,
            or_(JobListing.detail_fetched_at.is_(None), JobListing.detail_fetched_at < now - REFRESH_AFTER),
            or_(Job.expires_at.is_(None), Job.expires_at > now),
            Job.status != JobStatus.HIDDEN,
        )
        .order_by(JobListing.detail_fetched_at.asc().nulls_first(), Job.match_score.desc().nulls_last())
        .limit(limit)
    ))
    ok = fail = 0
    for listing in rows:
        if await fetch_for_listing(session, client, connector, listing):
            ok += 1
        else:
            fail += 1
        await asyncio.sleep(connector.detail_delay_seconds)
    return ok, fail
