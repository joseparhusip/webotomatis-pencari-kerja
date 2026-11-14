"""Audit kelengkapan: semua lowongan kategori IT JobStreet (N hari terakhir) harus ada di database.

    python -m app.scripts.audit_jobstreet
"""

import asyncio
import sys

from sqlalchemy import select

from app.connectors.base import new_http_client
from app.connectors.jobstreet import JobStreetConnector
from app.core.config import get_settings
from app.core.db import SessionLocal
from app.models import Job, JobListing, Source


async def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    s = get_settings()
    conn = JobStreetConnector()
    live: dict[str, str] = {}
    async with new_http_client(30) as client:
        for page in range(1, conn.max_pages + 1):
            result = await conn.search(client, conn.ALL_IT, page, set())
            for item in result.items:
                live[item.external_id] = f"{item.title} — {item.company_name}"
            if not result.has_more:
                break
    async with SessionLocal() as session:
        rows = await session.execute(
            select(JobListing.external_id, Job.role_category, Job.description.is_not(None))
            .join(Source, Source.id == JobListing.source_id).join(Job, Job.id == JobListing.job_id)
            .where(Source.name == "jobstreet", JobListing.external_id.in_(list(live)))
        )
        stored = {ext: (cat.value, has_desc) for ext, cat, has_desc in rows.all()}
    missing = [live[k] for k in live if k not in stored]
    no_desc = sum(1 for _, d in stored.values() if not d)
    non_it = [live[k] for k, (cat, _) in stored.items() if cat == "other"]
    print(f"JobStreet kategori IT, {s.scrape_max_age_days} hari terakhir: {len(live)} lowongan")
    print(f"  ada di database : {len(stored)} ({100 * len(stored) / max(1, len(live)):.1f}%)")
    print(f"  deskripsi lengkap: {len(stored) - no_desc}/{len(stored)}")
    print(f"  tidak ada        : {len(missing)}")
    for m in missing[:10]:
        print("    -", m)
    print(f"  tersimpan tapi dikategorikan Non-IT: {len(non_it)}")
    for m in non_it[:10]:
        print("    -", m)


if __name__ == "__main__":
    asyncio.run(main())
