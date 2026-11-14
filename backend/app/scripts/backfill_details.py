"""Ambil halaman detail untuk lowongan yang belum lengkap.

    python -m app.scripts.backfill_details            # semua sumber yang mendukung, maks. 1000 per sumber
    python -m app.scripts.backfill_details glints 50
"""

import asyncio
import logging
import sys

from sqlalchemy import select

from app.connectors import REGISTRY
from app.connectors.base import new_http_client
from app.core.config import get_settings
from app.core.db import SessionLocal
from app.models import Source
from app.services.details import backfill


async def main(names: list[str], limit: int) -> None:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    names = names or [n for n, c in REGISTRY.items() if c.supports_detail]
    async with SessionLocal() as session, new_http_client(get_settings().http_timeout_seconds) as client:
        for name in names:
            source = await session.scalar(select(Source).where(Source.name == name))
            if source is None:
                print(f"{name}: belum pernah di-scrape")
                continue
            ok, fail = await backfill(session, source, REGISTRY[name], client, limit)
            print(f"{name}: {ok} detail diambil, {fail} gagal")


if __name__ == "__main__":
    args = sys.argv[1:]
    limit = int(args.pop()) if args and args[-1].isdigit() else 1000
    asyncio.run(main(args, limit))
