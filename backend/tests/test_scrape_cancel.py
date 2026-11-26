"""Tombol Batalkan scraping: status progres & penanda run yang dibatalkan (bukan error)."""

import asyncio

from app.services.scrape_progress import Progress, is_cancel_note, summarize


class FakeRedis:
    def __init__(self):
        self.store = {}

    async def set(self, key, value, ex=None):
        self.store[key] = value


def test_progres_dibatalkan():
    p = Progress(FakeRedis(), "manual", {"jobstreet": "JobStreet", "glints": "Glints", "kalibrr": "Kalibrr"})

    async def run():
        await p.source("jobstreet", state="running", phase="Mencari lowongan", done=1, total=4, found=120, new=7)
        await p.source("glints", state="done", phase="Selesai", found=40, new=2)
        await p.finish(cancelled=True)

    asyncio.run(run())
    summary = summarize(p.data, lock_held=False)
    states = {r["name"]: r["state"] for r in summary["rows"]}
    assert summary["cancelled"] and summary["state"] == "done" and summary["percent"] == 100
    assert states == {"jobstreet": "cancelled", "glints": "done", "kalibrr": "cancelled"}
    assert summary["new"] == 9                      # lowongan yang sudah diambil tetap terhitung
    assert summary["done_count"] == summary["total_count"]


def test_run_dibatalkan_bukan_error():
    assert is_cancel_note("Dibatalkan oleh pengguna")
    assert is_cancel_note("Dibatalkan oleh pengguna\n'react' hal. 2: HTTP 500")
    assert not is_cancel_note("Terhenti: worker dimatikan")
    assert not is_cancel_note(None)
