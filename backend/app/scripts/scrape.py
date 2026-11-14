"""Jalankan scraping sekali tanpa worker: `python -m app.scripts.scrape [nama_sumber ...]`."""

import asyncio
import logging
import sys

from app.connectors import REGISTRY
from app.services.scraping import run_connector, scrape_all


async def main(names: list[str]) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    if names:
        results = [await run_connector(REGISTRY[n]) for n in names]
    else:
        results = await scrape_all()
    for r in results:
        print(r)


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:]))
