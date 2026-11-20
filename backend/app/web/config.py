"""Halaman Pengaturan → Konfigurasi: isi API key, SMTP, dsb. dari browser, tanpa mengedit .env."""

import asyncio
import logging
import re
import time
from urllib.parse import quote

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse
from pydantic import ValidationError

from app.core.config import (
    RUNTIME_KEYS,
    SECRET_KEYS,
    env_settings,
    get_settings,
    read_overrides,
    runtime_file,
    write_overrides,
)

log = logging.getLogger(__name__)
router = APIRouter(prefix="/settings/config")

SELECT_LABELS = {
    "gemini_thinking_level": {"minimal": "Sangat cepat", "low": "Cepat (disarankan)",
                              "medium": "Seimbang", "high": "Paling teliti (lambat)"},
}

# Dipakai bila daftar model tidak bisa diambil dari API (key kosong / offline).
FALLBACK_GEMINI_MODELS = ["gemini-3.5-flash", "gemini-3.5-flash-lite", "gemini-3.1-flash-lite",
                          "gemini-2.5-pro", "gemini-2.5-flash", "gemini-2.5-flash-lite"]
_NON_TEXT_MODEL = re.compile(r"image|banana|tts|audio|live|embedding|veo|imagen|robotics|computer-use", re.I)
# Hanya model resmi (pro/flash/flash-lite bernomor versi, bukan preview/eksperimen/kode nama).
_STABLE = re.compile(r"^gemini-\d+(\.\d+)?-(pro|flash|flash-lite)$")
RECOMMENDED_MODEL = "gemini-3.5-flash"
_models_cache: dict = {"key": None, "at": 0.0, "models": []}


def friendly_label(model_id: str, display: str = "") -> str:
    """'gemini-3.5-flash' → 'Gemini 3.5 Flash — cepat & hemat (disarankan)'."""
    name = display if display and display != model_id else model_id.replace("gemini-", "Gemini ").replace("-", " ").title()
    if model_id.endswith("flash-lite"):
        desc = "paling cepat & paling hemat, hasil lebih sederhana"
    elif model_id.endswith("flash"):
        desc = "cepat & hemat"
    elif model_id.endswith("pro"):
        desc = "paling teliti, lebih lambat & cepat habis kuota"
    else:
        desc = ""
    label = f"{name} — {desc}" if desc else name
    return label + (" (disarankan)" if model_id == RECOMMENDED_MODEL else "")
MODELS_TTL_SECONDS = 3600


async def gemini_models() -> tuple[list[tuple[str, str]], bool]:
    """([(id, nama tampilan)], live?) — model teks yang bisa dipakai API key ini, di-cache 1 jam."""
    key = get_settings().gemini_api_key
    if key and _models_cache["key"] == key and time.time() - _models_cache["at"] < MODELS_TTL_SECONDS:
        return _models_cache["models"], True
    if key:
        try:
            from google import genai

            client = genai.Client(api_key=key)

            async def fetch() -> list[tuple[str, str]]:
                out = []
                async for m in await client.aio.models.list():
                    name = (m.name or "").removeprefix("models/")
                    if ("generateContent" in (m.supported_actions or []) and name.startswith("gemini")
                            and not _NON_TEXT_MODEL.search(f"{name} {m.display_name or ''}")
                            and _STABLE.search(name)):
                        out.append((name, m.display_name or name))
                return out

            models = await asyncio.wait_for(fetch(), 8)
            if models:
                models = [(mid, friendly_label(mid, disp)) for mid, disp in models]
                models.sort(key=lambda m: _version_key(m[0]), reverse=True)
                _models_cache.update(key=key, at=time.time(), models=models)
                return models, True
        except Exception as exc:  # key salah / offline — pakai daftar bawaan
            log.warning("Gagal mengambil daftar model Gemini: %s", exc)
    return [(m, friendly_label(m)) for m in FALLBACK_GEMINI_MODELS], False


def _version_key(name: str) -> tuple:
    """Urutkan terbaru dulu: versi angka, lalu pro > flash > flash-lite, preview di belakang."""
    m = re.search(r"gemini-(\d+(?:\.\d+)?)", name)
    version = float(m.group(1)) if m else 0.0
    tier = 3 if "pro" in name else 1 if "lite" in name else 2
    return (version, "preview" not in name and "exp" not in name, tier)

# (judul grup, [(key, label, tipe, petunjuk)]) — tipe: text | secret | int | list | model | models | select:<a>|<b>
GROUPS: list[tuple[str, list[tuple[str, str, str, str]]]] = [
    ("Surat lamaran AI", [
        ("gemini_api_key", "Kunci API Gemini", "secret",
         "Kunci akses ke AI Google, gratis dibuat di aistudio.google.com/apikey. Tanpa kunci ini surat dibuat dari template."),
        ("gemini_model", "Versi AI yang dipakai", "model",
         "Kalau ragu, pilih yang bertanda (disarankan)."),
        ("gemini_fallback_models", "Versi AI cadangan", "models",
         "Dipakai otomatis kalau versi utama sedang sibuk atau jatah harian habis. Centang 1–2 saja."),
        ("gemini_thinking_level", "Seberapa lama AI berpikir", "select:minimal|low|medium|high",
         "Makin lama, makin teliti tetapi makin lambat."),
        ("anthropic_api_key", "Kunci API Claude (opsional)", "secret", "Hanya dipakai kalau kunci Gemini kosong."),
    ]),
    ("Kirim email (Gmail SMTP)", [
        ("smtp_user", "Email Gmail", "text", ""),
        ("smtp_password", "App Password", "secret", "myaccount.google.com/apppasswords"),
        ("smtp_host", "SMTP host", "text", ""),
        ("smtp_port", "SMTP port", "int", ""),
        ("max_emails_per_day", "Maks. email per hari", "int", ""),
        ("min_seconds_between_emails", "Jeda antar email (detik)", "int", ""),
    ]),
    ("Scraping", [
        ("scrape_interval_hours", "Interval scraping (jam)", "int", "Berlaku setelah worker di-restart."),
        ("scrape_pages_per_keyword", "Halaman per keyword", "int", "1 halaman ≈ 30 lowongan."),
        ("scrape_max_age_days", "Umur lowongan maks. (hari)", "int", ""),
        ("serpapi_key", "SerpApi key (Google Jobs)", "secret", "serpapi.com"),
        ("serpapi_max_searches_per_day", "Pencarian SerpApi per hari", "int", ""),
    ]),
    ("Instagram (Meta Graph API)", [
        ("meta_access_token", "Meta access token", "secret", ""),
        ("instagram_business_id", "Instagram business ID", "text", ""),
        ("meta_graph_version", "Versi Graph API", "text", ""),
        ("instagram_max_age_days", "Umur postingan maks. (hari)", "int", ""),
    ]),
]
assert {k for _, fields in GROUPS for k, *_ in fields} <= RUNTIME_KEYS


def _mask(value: str) -> str:
    return "" if not value else ("•" * 8 + value[-4:] if len(value) > 8 else "•" * 8)


async def config_context() -> dict:
    s, env, overrides = get_settings(), env_settings(), read_overrides()
    models, live = await gemini_models()
    known = {m for m, _ in models}
    # Nilai tersimpan yang tidak ada di daftar tetap ditampilkan supaya tidak hilang diam-diam.
    extra = [(m, f"{friendly_label(m)} — tidak tersedia untuk API key ini") for m in [s.gemini_model, *s.gemini_fallback_models]
             if m and m not in known]
    model_options = list(dict.fromkeys(extra + models))
    groups = []
    for title, fields in GROUPS:
        rows = []
        for key, label, kind, hint in fields:
            value = getattr(s, key)
            rows.append({
                "key": key, "label": label, "kind": kind, "hint": hint,
                "options": ([(o, SELECT_LABELS.get(key, {}).get(o, o)) for o in kind.split(":", 1)[1].split("|")]
                            if kind.startswith("select:")
                            else model_options if kind in ("model", "models") else []),
                "selected": list(value) if isinstance(value, list) else [value],
                "value": "" if key in SECRET_KEYS else (", ".join(value) if isinstance(value, list) else value),
                "masked": _mask(value) if key in SECRET_KEYS else "",
                "from_web": key in overrides,
                "differs_env": key in overrides and getattr(env, key) != value,
            })
        groups.append({"title": title, "rows": rows})
    return {"cfg_groups": groups, "cfg_file": str(runtime_file()), "cfg_has_overrides": bool(overrides),
            "cfg_models_live": live}


def _back(msg: str, error: bool = False) -> RedirectResponse:
    return RedirectResponse(f"/settings?{'cfg_err' if error else 'cfg_ok'}={quote(msg)}#konfigurasi", 303)


@router.post("")
async def save_config(request: Request):
    form = await request.form()
    current = read_overrides()
    values = dict(current)
    for _, fields in GROUPS:
        for key, label, kind, _hint in fields:
            if key not in form and f"{key}__clear" not in form and f"{key}__present" not in form:
                continue
            raw = str(form.get(key, "")).strip()
            if key in SECRET_KEYS:
                if form.get(f"{key}__clear") == "on":
                    values[key] = ""
                elif raw:
                    values[key] = raw
                continue  # kosong = tidak diubah
            if kind == "int":
                if not raw:
                    values.pop(key, None)  # kembali ke .env/bawaan
                elif not raw.isdigit():
                    return _back(f"{label} harus angka.", error=True)
                else:
                    values[key] = int(raw)
            elif kind == "models":  # checkbox: urutan dicentang = urutan dicoba
                values[key] = [v for v in dict.fromkeys(str(x).strip() for x in form.getlist(key)) if v]
            elif kind == "list":
                values[key] = [v.strip() for v in raw.split(",") if v.strip()]
            else:
                values[key] = raw
    # Simpan hanya yang berbeda dari .env supaya .env tetap bisa jadi sumber utama.
    env = env_settings()
    values = {k: v for k, v in values.items() if getattr(env, k) != v}
    try:
        write_overrides(values)
    except ValidationError as exc:
        return _back(f"Nilai tidak valid: {exc.errors()[0].get('loc', [''])[0]}", error=True)
    return _back("Konfigurasi tersimpan dan langsung dipakai.")


@router.post("/reset")
async def reset_config():
    runtime_file().unlink(missing_ok=True)
    return _back("Isian dari web dihapus. Kembali memakai nilai .env.")
