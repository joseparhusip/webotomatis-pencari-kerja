"""Jalur AI (Gemini) surat lamaran — panggilan API ditiru, tidak memakai kuota."""

import pytest
from google.genai import errors

from app.core.config import get_settings
from app.models import Job, UserProfile
from app.models.enums import RoleCategory
from app.services import letters
from app.services.letters import _short_error, _strip_greetings, generate_letter

BODY = ("Berdasarkan informasi lowongan di Glints, saya mengajukan lamaran untuk posisi Backend Developer.\n\n"
        "Posisi ini membutuhkan Go dan PostgreSQL, sesuai pengalaman saya membangun 44 endpoint REST API.\n\n"
        "Saya terbiasa memodelkan sistem dengan UML dan ERD.\n\n"
        "CV terlampir. Terima kasih atas perhatian Bapak/Ibu.")


def _job() -> Job:
    return Job(title="Backend Developer", company_name="PT Contoh", role_category=RoleCategory.BACKEND,
               location_city="Jakarta Selatan", apply_url="u", dedup_hash="h", skills=[], required_skills=["Go"],
               benefits=[], description="Kami mencari backend developer.")


def _profile() -> UserProfile:
    return UserProfile(full_name="Nama Kandidat", email="kandidat@example.com", skills=["Go"])


@pytest.fixture
def gemini_on(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "gemini_api_key", "test-key")
    monkeypatch.setattr(s, "gemini_model", "gemini-utama")
    monkeypatch.setattr(s, "gemini_fallback_models", ["gemini-cadangan"])


def _server_error() -> errors.ServerError:
    return errors.ServerError(503, {"error": {"code": 503, "message": "high demand", "status": "UNAVAILABLE"}})


@pytest.mark.asyncio
async def test_gemini_letter_gets_system_header_and_signature(gemini_on, monkeypatch):
    async def fake(model, prompt):
        return "Perihal: Lamaran\n\nYth. Bapak/Ibu HRD\n\nDengan hormat,\n\n" + BODY + "\n\nHormat saya,\nNama"
    monkeypatch.setattr(letters, "_gemini_once", fake)
    r = await generate_letter(_job(), _profile(), "teks cv", "id")
    assert r.source == "ai" and r.note is None
    assert r.text.startswith("Perihal: Lamaran Pekerjaan sebagai Backend Developer\n\n"
                             "Yth. Bapak/Ibu Tim Rekrutmen\nPT Contoh\ndi Jakarta Selatan\n\nDengan hormat,\n\nBerdasarkan")
    assert r.text.count("Hormat saya,") == 1 and r.text.count("Dengan hormat,") == 1


@pytest.mark.asyncio
async def test_falls_back_to_next_model_when_busy(gemini_on, monkeypatch):
    called = []

    async def fake(model, prompt):
        called.append(model)
        if model == "gemini-utama":
            raise _server_error()
        return BODY
    monkeypatch.setattr(letters, "_gemini_once", fake)
    r = await generate_letter(_job(), _profile(), "teks cv", "id")
    assert called == ["gemini-utama", "gemini-cadangan"] and r.source == "ai"


@pytest.mark.asyncio
async def test_all_models_fail_uses_template_with_reason(gemini_on, monkeypatch):
    async def fake(model, prompt):
        raise _server_error()
    monkeypatch.setattr(letters, "_gemini_once", fake)
    r = await generate_letter(_job(), _profile(), "teks cv", "id")
    assert r.source == "template" and r.text.startswith("Perihal: Lamaran Pekerjaan sebagai Backend Developer")
    assert r.note.startswith("Gemini gagal") and "template" in r.note


@pytest.mark.asyncio
async def test_no_cv_text_explains_why_ai_not_used(gemini_on):
    r = await generate_letter(_job(), _profile(), None, "id")
    assert r.source == "template" and "teks CV kosong" in r.note


def test_strip_greetings_keeps_body_only():
    raw = "Dear Hiring Team,\n\n" + BODY + "\n\nSincerely,\nNama\n\nnama@example.com"
    assert _strip_greetings(raw) == BODY


def test_short_error_messages():
    assert "API key ditolak" in _short_error(Exception("400 API key not valid"))
    assert "kuota" in _short_error(Exception("429 RESOURCE_EXHAUSTED"))
    assert "model tidak ditemukan" in _short_error(Exception("404 NOT_FOUND model"))
