import asyncio
from datetime import UTC, datetime, timedelta

from app.services.scrape_progress import Progress, duration_text, new_state, source_percent, summarize


def test_percent_per_phase():
    base = {"state": "running", "phase": "Mencari lowongan", "done": 8, "total": 16}
    assert source_percent(base) == 35
    assert source_percent({**base, "phase": "Mengambil detail lowongan", "done": 50, "total": 100}) == 85
    assert source_percent({**base, "state": "waiting"}) == 0
    assert source_percent({**base, "state": "done"}) == 100


def test_summary_ignores_skipped_and_detects_dead_worker():
    data = new_state("manual", {"a": "A", "b": "B", "c": "C"})
    data["sources"]["a"].update(state="done", found=10, new=2)
    data["sources"]["b"].update(state="running", phase="Mencari lowongan", done=1, total=2, found=4, new=1)
    data["sources"]["c"].update(state="skipped")
    s = summarize(data, lock_held=True)
    assert (s["state"], s["percent"], s["done_count"], s["total_count"], s["new"]) == ("running", 68, 1, 2, 3)
    data["started_at"] = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
    assert summarize(data, lock_held=False)["state"] == "done"  # lock hilang = worker berhenti


def test_progress_writes_json():
    class FakeRedis:
        def __init__(self): self.v = None
        async def set(self, k, v, ex=None): self.v = v

    r = FakeRedis()
    p = Progress(r, "manual", {"a": "A"})
    asyncio.run(p.source("a", state="running", found=3))
    assert '"found": 3' in r.v
    asyncio.run(p.finish())
    assert '"state": "done"' in r.v


def test_duration_text():
    assert duration_text(45) == "45 dtk"
    assert duration_text(372) == "6 mnt 12 dtk"
