"""Status scan CV di Redis, supaya pengguna melihat apa yang sedang dikerjakan.

* Tahap scan (per token dari form): upload → teks PDF / OCR → pemetaan → simpan. Dipakai layar loading.
* Kesimpulan AI (per CV): antre → berjalan → coba lagi (AI sibuk) → selesai / gagal. Dikerjakan worker.
"""

import json
import logging
import time

from app.services.scrape_progress import get_redis

log = logging.getLogger(__name__)

SCAN_KEY = "webotomatis:cv-scan:{}"
AI_KEY = "webotomatis:cv-ai:{}"
SCAN_TTL = 15 * 60
AI_TTL = 24 * 60 * 60
AI_MAX_TRIES = 6

STAGES = {
    "upload": "Mengunggah file",
    "text": "Mengambil teks dari PDF",
    "ocr": "Membaca halaman dengan OCR (PDF berupa gambar)",
    "map": "Memetakan data CV (data diri, pengalaman, pendidikan, skill)",
    "save": "Menyimpan ke profil",
    "done": "Selesai",
}


async def _set(key: str, data: dict, ttl: int) -> None:
    redis = get_redis()
    if redis is None:
        return
    try:
        await redis.set(key, json.dumps(data), ex=ttl)
    except Exception as exc:
        log.debug("status CV tidak tersimpan: %s", exc)


async def _get(key: str) -> dict | None:
    redis = get_redis()
    if redis is None:
        return None
    try:
        raw = await redis.get(key)
    except Exception:
        return None
    return json.loads(raw) if raw else None


def _safe_token(token: str | None) -> str | None:
    token = (token or "").strip()
    return token if token and len(token) <= 64 and token.replace("-", "").isalnum() else None


async def set_stage(token: str | None, stage: str) -> None:
    token = _safe_token(token)
    if token:
        await _set(SCAN_KEY.format(token), {"stage": stage, "label": STAGES.get(stage, stage), "at": time.time()},
                   SCAN_TTL)


async def get_stage(token: str | None) -> dict | None:
    token = _safe_token(token)
    return await _get(SCAN_KEY.format(token)) if token else None


async def set_ai(cv_id: int, state: str, attempt: int = 0, message: str = "", retry_in: int = 0) -> None:
    """state: queued | running | retry | done | failed."""
    await _set(AI_KEY.format(cv_id), {
        "state": state, "attempt": attempt, "max": AI_MAX_TRIES, "message": message,
        "retry_at": time.time() + retry_in if retry_in else 0, "at": time.time(),
    }, AI_TTL)


async def get_ai(cv_id: int | None) -> dict | None:
    if not cv_id:
        return None
    data = await _get(AI_KEY.format(cv_id))
    if data and data.get("retry_at"):
        data["retry_in"] = max(0, int(data["retry_at"] - time.time()))
    return data
