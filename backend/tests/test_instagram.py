"""Instagram: konversi hasil baca Gemini → lowongan, pilihan gambar, dan input username (tanpa API sungguhan)."""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from app.connectors.instagram import _image_urls, _to_jobin, _ts
from app.models.enums import EmploymentType, WorkMode
from app.services.ig_parse import ParseError, _loads
from app.web.routes import _ig_username

MEDIA = {"id": "179", "permalink": "https://www.instagram.com/p/ABC/", "timestamp": "2026-11-13T10:20:30+0000",
         "media_type": "IMAGE", "media_url": "https://cdn/x.jpg", "caption": "Dibuka lowongan…"}


def test_to_jobin_maps_parsed_post():
    job = _to_jobin({
        "title": "Backend Developer", "company": "PT Contoh Digital", "location_city": "Jakarta Selatan",
        "work_mode": "hybrid", "employment_type": "fulltime", "salary_min": "6000000", "salary_max": 9000000,
        "requirements": ["Menguasai Go", "Paham PostgreSQL"], "responsibilities": ["Membangun API"],
        "benefits": ["BPJS"], "skills": ["Go", "PostgreSQL"], "email": "hr@contoh.co.id",
        "deadline": "2026-11-30", "experience_min_years": 1, "suspicious": False,
    }, MEDIA, "akuninfoloker", 0)
    assert job.external_id == "179-0" and job.url == "https://www.instagram.com/p/ABC/"
    assert (job.salary_min, job.salary_max) == (6_000_000, 9_000_000)
    assert job.work_mode is WorkMode.HYBRID and job.employment_type is EmploymentType.FULLTIME
    assert "• Menguasai Go" in job.requirements
    assert "Email: hr@contoh.co.id" in job.description  # dipakai sistem untuk mode kirim email
    assert "@akuninfoloker" in job.description
    assert job.expires_at.astimezone(ZoneInfo("Asia/Jakarta")).day == 30
    assert job.posted_at == datetime(2026, 11, 13, 10, 20, 30, tzinfo=UTC)
    assert job.is_complete


def test_job_without_company_is_skipped():
    assert _to_jobin({"title": "Admin", "company": ""}, MEDIA, "akun", 0) is None


def test_suspicious_post_is_flagged():
    job = _to_jobin({"title": "Admin", "company": "PT X", "suspicious": True}, MEDIA, "akun", 0)
    assert "tanda penipuan" in job.description


def test_image_urls_for_each_media_type():
    assert _image_urls(MEDIA) == ["https://cdn/x.jpg"]
    assert _image_urls({"media_type": "VIDEO", "thumbnail_url": "https://cdn/t.jpg"}) == ["https://cdn/t.jpg"]
    carousel = {"media_type": "CAROUSEL_ALBUM", "children": {"data": [
        {"media_type": "IMAGE", "media_url": f"https://cdn/{i}.jpg"} for i in range(6)]}}
    assert len(_image_urls(carousel)) == 4


def test_timestamp_parsing():
    assert _ts("2026-11-13T10:20:30+0000").tzinfo is not None
    assert _ts("bukan tanggal") is None


def test_gemini_json_parsing():
    assert _loads('```json\n{"is_job_post": false, "jobs": []}\n```') == {"is_job_post": False, "jobs": []}
    assert _loads('{"jobs": [{"title": "A"}, "x"]}')["jobs"] == [{"title": "A"}]
    with pytest.raises(ParseError):
        _loads("bukan json")


def test_instagram_username_input():
    assert _ig_username("@LokerJakarta") == "lokerjakarta"
    assert _ig_username("https://www.instagram.com/info.loker_id/?hl=id") == "info.loker_id"
    assert _ig_username("instagram.com/loker.bekasi/") == "loker.bekasi"
