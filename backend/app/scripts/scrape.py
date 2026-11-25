"""Jalankan scraping sekali tanpa worker: `python -m app.scripts.scrape [nama_sumber ...]`."""

import asyncio
import logging
import sys

from app.connectors import REGISTRY
from app.services.scraping import run_connector, scrape_all


async def main(names: list[str]) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    if names:
        from app.core.db import SessionLocal
        from app.services.profile import ensure_targets
        from app.services.targeting import load_target

        unknown = [n for n in names if n not in REGISTRY]
        if unknown:
            raise SystemExit(f"Sumber tidak dikenal: {', '.join(unknown)}. Pilihan: {', '.join(REGISTRY)}")
        async with SessionLocal() as session:  # sumber tertentu saja: target CV aktif tetap harus dimuat
            await ensure_targets(session)
            await load_target(session)
        results = [await run_connector(REGISTRY[n]) for n in names]
    else:
        results = await scrape_all()
    for r in results:
        print(r)


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:]))
