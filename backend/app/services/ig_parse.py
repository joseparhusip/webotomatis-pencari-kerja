"""Baca postingan Instagram info loker (gambar + caption) menjadi data lowongan terstruktur dengan Gemini."""

import asyncio
import json
import logging
import re

from app.core.config import get_settings

log = logging.getLogger(__name__)

PROMPT = """Kamu membaca satu postingan Instagram dari akun info lowongan kerja di Indonesia (gambar + caption).
Tugasmu: ekstrak lowongan kerja yang BENAR-BENAR tertulis di gambar atau caption.

Aturan:
- Jangan mengarang. Isi null / [] bila informasi tidak tertulis.
- "company" = nama perusahaan yang membuka lowongan (bukan nama akun Instagram penyebar info), persis seperti tertulis.
- Satu postingan bisa berisi beberapa posisi: buat satu item per posisi. Persyaratan bersama boleh disalin ke tiap posisi.
- Jika postingan bukan lowongan kerja (promosi, kursus berbayar, meme, ucapan, dsb.), is_job_post = false dan jobs = [].
- Tandai "suspicious" true bila ada tanda penipuan: meminta biaya/transfer, gaji tidak masuk akal, hanya kontak pribadi tanpa perusahaan jelas.
- Gaji dalam rupiah per bulan (angka bulat). Tanggal dalam format YYYY-MM-DD.

Keluarkan HANYA JSON dengan bentuk:
{"is_job_post": true, "jobs": [{
  "title": "...", "company": "...", "location_city": "...",
  "work_mode": "onsite|remote|hybrid|null", "employment_type": "fulltime|contract|internship|freelance|parttime|null",
  "salary_min": null, "salary_max": null, "education": "...", "experience_min_years": null,
  "requirements": ["..."], "responsibilities": ["..."], "benefits": ["..."], "skills": ["..."],
  "email": "...", "whatsapp": "...", "apply_link": "...", "deadline": "YYYY-MM-DD",
  "suspicious": false
}]}"""


class ParseError(Exception):
    pass


async def parse_post(caption: str, images: list[tuple[bytes, str]], username: str) -> dict:
    """images: [(bytes, mime_type)]. Kembalikan dict sesuai PROMPT."""
    from google.genai import errors, types

    s = get_settings()
    parts = [types.Part.from_bytes(data=b, mime_type=mime) for b, mime in images[:4]]
    parts.append(f"Akun Instagram: @{username}\nCaption:\n{(caption or '-')[:6000]}")
    models = [s.gemini_model] + [m for m in s.gemini_fallback_models if m != s.gemini_model]
    last: Exception | None = None
    for model in models:
        try:
            text = await asyncio.wait_for(_call(model, parts), 100)
            return _loads(text)
        except (errors.ServerError, asyncio.TimeoutError, TimeoutError) as exc:
            last = exc
        except errors.ClientError as exc:
            if getattr(exc, "code", None) in (404, 429):
                last = exc
                continue
            raise
    raise ParseError(f"Gemini tidak tersedia: {last}")


async def _call(model: str, parts: list) -> str:
    from google import genai
    from google.genai import types

    from app.services.letters import _thinking

    s = get_settings()
    client = genai.Client(api_key=s.gemini_api_key, http_options=types.HttpOptions(
        timeout=90_000, retry_options=types.HttpRetryOptions(attempts=1)))
    resp = await client.aio.models.generate_content(
        model=model, contents=parts,
        config=types.GenerateContentConfig(
            system_instruction=PROMPT, temperature=0.1, max_output_tokens=8192,
            response_mime_type="application/json", thinking_config=_thinking(model, types),
        ),
    )
    return resp.text or ""


def _loads(text: str) -> dict:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ParseError(f"JSON tidak valid dari Gemini: {exc}") from exc
    if not isinstance(data, dict):
        raise ParseError("bentuk JSON tidak sesuai")
    data.setdefault("is_job_post", bool(data.get("jobs")))
    data["jobs"] = [j for j in data.get("jobs") or [] if isinstance(j, dict)]
    return data
