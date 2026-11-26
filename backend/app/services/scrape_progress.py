"""Progres scraping yang sedang berjalan, disimpan di Redis supaya web bisa menampilkannya (worker ≠ web).

Bentuk data:
    {"state": "queued|running|done", "trigger": "manual|otomatis", "started_at": iso, "finished_at": iso|None,
     "sources": {name: {"label", "state": "waiting|running|done|skipped|error", "phase", "done", "total",
                        "found", "new", "details", "note"}}}
"""
from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

log = logging.getLogger(__name__)

PROGRESS_KEY = "webotomatis:scrape-progress"
CANCEL_KEY = "webotomatis:scrape-cancel"   # diset tombol "Batalkan"; worker berhenti di titik aman berikutnya
CANCEL_TTL = 60 * 60
CANCELLED_NOTE = "Dibatalkan"              # awalan ScrapeRun.error untuk run yang dihentikan pengguna (bukan error)


class ScrapeCancelled(Exception):
    """Pengguna menekan Batalkan."""


async def cancel_requested(redis) -> bool:
    if redis is None:
        return False
    try:
        return bool(await redis.exists(CANCEL_KEY))
    except Exception:
        return False


def is_cancel_note(error: str | None) -> bool:
    return bool(error) and error.startswith(CANCELLED_NOTE)
PROGRESS_TTL = 60 * 60 * 12
SKIP_KEY = "webotomatis:scrape-skip:{}"  # sorted set: external_id lowongan lama (skor = waktu posting)
SKIP_TTL = 60 * 60 * 24 * 30

_redis = None


def get_redis():
    """Klien Redis bersama untuk proses ini (None bila Redis tidak bisa dipakai)."""
    global _redis
    if _redis is None:
        try:
            from redis.asyncio import from_url

            from app.core.config import get_settings

            _redis = from_url(get_settings().redis_url, decode_responses=True)
        except Exception as exc:  # pragma: no cover - Redis opsional untuk skrip CLI
            log.warning("Redis tidak tersedia untuk progres scraping: %s", exc)
    return _redis


def _now() -> str:
    return datetime.now(UTC).isoformat()


def new_state(trigger: str, sources: dict[str, str], state: str = "running") -> dict:
    return {
        "state": state, "trigger": trigger, "started_at": _now(), "finished_at": None,
        "sources": {name: {"label": label, "state": "waiting", "phase": "Menunggu giliran", "done": 0,
                           "total": 0, "found": 0, "new": 0, "details": 0, "note": ""}
                    for name, label in sources.items()},
    }


class Progress:
    """Dipakai worker: menyimpan seluruh status ke Redis setiap kali ada perubahan."""

    def __init__(self, redis, trigger: str, sources: dict[str, str]):
        self.redis = redis
        self.data = new_state(trigger, sources)

    async def save(self) -> None:
        if self.redis is None:
            return
        try:
            await self.redis.set(PROGRESS_KEY, json.dumps(self.data), ex=PROGRESS_TTL)
        except Exception as exc:  # progres tidak boleh menggagalkan scraping
            log.warning("Gagal menyimpan progres scraping: %s", exc)

    async def source(self, name: str, **fields) -> None:
        self.data["sources"].setdefault(name, {"label": name, "state": "waiting", "phase": "", "done": 0,
                                               "total": 0, "found": 0, "new": 0, "details": 0, "note": ""})
        self.data["sources"][name].update(fields)
        await self.save()

    async def finish(self, cancelled: bool = False) -> None:
        self.data["state"] = "done"
        self.data["finished_at"] = _now()
        if cancelled:
            self.data["cancelled"] = True
            for src in self.data["sources"].values():
                if src["state"] in ("waiting", "running"):
                    src.update(state="cancelled", phase="Dibatalkan")
        await self.save()


class NullProgress(Progress):
    def __init__(self):
        super().__init__(None, "manual", {})


async def read_progress(redis) -> dict | None:
    if redis is None:
        return None
    try:
        raw = await redis.get(PROGRESS_KEY)
    except Exception:
        return None
    return json.loads(raw) if raw else None


async def mark_interrupted(redis) -> None:
    """Worker mati di tengah jalan: progres lama jangan terlihat 'berjalan' selamanya."""
    data = await read_progress(redis)
    if data and data.get("state") != "done":
        data["state"] = "done"
        data["finished_at"] = _now()
        for s in data["sources"].values():
            if s["state"] in ("waiting", "running"):
                s.update(state="error", phase="Terhenti", note="worker dimulai ulang")
        await redis.set(PROGRESS_KEY, json.dumps(data), ex=PROGRESS_TTL)


# ---------- ringkasan untuk tampilan ----------

def source_percent(s: dict) -> int:
    if s["state"] in ("done", "skipped", "error", "cancelled"):
        return 100
    if s["state"] == "waiting":
        return 0
    frac = s["done"] / s["total"] if s["total"] else 0
    # Fase cari ≈ 70% pekerjaan, fase detail ≈ 30%.
    return round(70 + 30 * frac) if s["phase"].startswith("Mengambil detail") else round(70 * frac)


def summarize(data: dict | None, lock_held: bool) -> dict | None:
    if not data:
        return None
    now = datetime.now(UTC)
    started = datetime.fromisoformat(data["started_at"])
    finished = datetime.fromisoformat(data["finished_at"]) if data.get("finished_at") else None
    state = data["state"]
    # Worker tidak memegang lock lagi tapi status masih 'running' → terhenti tanpa sempat mencatat.
    if state == "running" and not lock_held and (now - started).total_seconds() > 30:
        state = "done"
    stalled = state == "queued" and (now - started).total_seconds() > 120
    rows = []
    for name, s in data["sources"].items():
        rows.append({**s, "name": name, "percent": source_percent(s)})
    counted = [r for r in rows if r["state"] != "skipped"]
    percent = round(sum(r["percent"] for r in counted) / len(counted)) if counted else (100 if state == "done" else 0)
    if state == "done":
        percent = 100
    elapsed = int(((finished or now) - started).total_seconds())
    return {
        "state": state, "stalled": stalled, "trigger": data.get("trigger", "otomatis"), "percent": percent,
        "rows": rows, "elapsed": elapsed, "finished_at": finished, "started_at": started,
        "cancelled": bool(data.get("cancelled")),
        "done_count": sum(r["state"] in ("done", "error", "cancelled") for r in counted), "total_count": len(counted),
        "running_names": [r["label"] for r in rows if r["state"] == "running"],
        "found": sum(r["found"] for r in rows), "new": sum(r["new"] for r in rows),
        "errors": sum(r["state"] == "error" for r in rows),
    }


def duration_text(seconds: int) -> str:
    m, s = divmod(max(0, seconds), 60)
    return f"{m} mnt {s} dtk" if m else f"{s} dtk"
