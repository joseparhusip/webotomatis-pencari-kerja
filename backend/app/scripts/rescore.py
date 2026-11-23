"""Hitung ulang kategori, level, dan skor semua lowongan setelah aturan di targeting.py diubah.

Sekaligus mengisi kategori asli situs (source_category) untuk lowongan lama dari data mentah yang tersimpan.

`python -m app.scripts.rescore`
"""

import asyncio

from sqlalchemy import select

from app.core.db import SessionLocal
from app.models import Job
from app.services.ingest import rescore


def category_from_raw(source: str, raw: dict) -> str | None:
    if source == "jobstreet":
        for c in raw.get("classifications") or []:
            main = (c.get("classification") or {}).get("description")
            sub = (c.get("subclassification") or {}).get("description")
            if main:
                return f"{main} / {sub}" if sub else main
    if source == "kalibrr":
        return raw.get("function")
    if source == "glints":
        return (raw.get("hierarchicalJobCategory") or {}).get("name")
    if source == "dealls":
        return (raw.get("jobRoleCategorySlug") or "").replace("-", " ") or None
    if source in ("lokerid", "kitalulus"):
        v = raw.get("occupationalCategory")
        return v if isinstance(v, str) else None
    if source == "traveloka":
        return raw.get("department")
    return None


async def main() -> None:
    from app.services.targeting import load_target

    async with SessionLocal() as session:
        await load_target(session)
        jobs = (await session.scalars(select(Job))).all()
        filled = 0
        for job in jobs:
            if not job.source_category:
                for listing in job.listings:
                    cat = category_from_raw(listing.source.name, listing.raw_payload or {})
                    if cat:
                        job.source_category = cat[:200]
                        filled += 1
                        break
            rescore(job)
        await session.commit()
    print(f"{len(jobs)} lowongan dihitung ulang, {filled} kategori asli diisi")


if __name__ == "__main__":
    asyncio.run(main())
