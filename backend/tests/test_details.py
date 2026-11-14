"""Halaman detail: isi lowongan harus sama dengan situs sumber (diuji dengan respons tiruan)."""

import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from app.connectors.base import JobDetail
from app.connectors.dealls import DeallsConnector
from app.connectors.glints import GlintsConnector
from app.connectors.jobstreet import JobStreetConnector
from app.connectors.jsonld import jobposting_to_jobin
from app.models import Job, JobListing
from app.models.enums import ExperienceLevel, RoleCategory, WorkMode
from app.services.details import apply_detail
from app.services.parsing import education_label, render_draftjs


def _client(html: str, status: int = 200) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(lambda req: httpx.Response(status, text=html)))


def _next(data: dict) -> str:
    return f'<html><script id="__NEXT_DATA__" type="application/json">{json.dumps(data)}</script></html>'


DRAFT = json.dumps({"blocks": [
    {"type": "unstyled", "text": "Tentang peran ini.", "depth": 0},
    {"type": "unordered-list-item", "text": "Node.js", "depth": 0},
    {"type": "unordered-list-item", "text": "PostgreSQL", "depth": 0},
    {"type": "unstyled", "text": "", "depth": 0},
    {"type": "ordered-list-item", "text": "Langkah satu", "depth": 0},
    {"type": "ordered-list-item", "text": "Langkah dua", "depth": 0},
]})


def test_render_draftjs_keeps_lists():
    assert render_draftjs(DRAFT) == "Tentang peran ini.\n• Node.js\n• PostgreSQL\n\n1. Langkah satu\n2. Langkah dua"
    assert render_draftjs(None) is None and render_draftjs("bukan json") is None


def test_education_labels():
    assert education_label("BACHELOR_DEGREE") == "Minimal Sarjana (S1)"
    assert education_label("bachelor degree") == "Minimal Sarjana (S1)"
    assert education_label("DIPLOMA") == "Minimal Diploma (D3)"
    assert education_label("SESUATU_LAIN") is None


def test_glints_listing_has_no_made_up_description():
    job = GlintsConnector()._normalize({
        "id": "x", "title": "Programmer", "company": {"name": "PT Contoh"}, "minYearsOfExperience": 1,
        "maxYearsOfExperience": 3, "educationLevel": "BACHELOR_DEGREE",
        "skills": [{"skill": {"name": "Node.js"}}, {"skill": {"name": "React.js"}}],
    })
    assert job.description is None  # dulu berisi "Pengalaman: …" buatan connector
    assert job.required_skills == ["Node.js", "React.js"]
    assert (job.experience_min, job.experience_max, job.education) == (1, 3, "Minimal Sarjana (S1)")


@pytest.mark.asyncio
async def test_glints_detail_matches_source():
    page = _next({"props": {"pageProps": {"initialData": {"data": {
        "id": "abc", "status": "OPEN", "descriptionJsonString": DRAFT, "workArrangementOption": "HYBRID",
        "minYearsOfExperience": 1, "maxYearsOfExperience": 3, "educationLevel": "BACHELOR_DEGREE",
        "skills": [{"mustHave": True, "skill": {"name": "Node.js"}}, {"mustHave": True, "skill": {"name": "C++"}}],
        "shouldShowBenefits": True, "benefits": [{"title": "Health Insurance"}, {"title": "THR"}],
        "shouldShowSalary": True,
        "salaries": [{"salaryMode": "MONTH", "minAmount": 4000000, "maxAmount": 6000000, "CurrencyCode": "IDR"}],
        "expiryDate": "2099-12-11", "createdAt": "2026-11-10T02:28:52Z",
    }}}}})
    async with _client(page) as c:
        d = await GlintsConnector().fetch_detail(c, "https://glints.com/x", "abc", {})
    assert not d.closed
    assert d.description.startswith("Tentang peran ini.\n• Node.js")
    assert d.required_skills == ["Node.js", "C++"] and d.benefits == ["Health Insurance", "THR"]
    assert (d.experience_min, d.experience_max, d.education) == (1, 3, "Minimal Sarjana (S1)")
    assert (d.salary_min, d.salary_max, d.work_mode) == (4_000_000, 6_000_000, WorkMode.HYBRID)


@pytest.mark.asyncio
async def test_glints_detail_closed_and_404():
    page = _next({"props": {"pageProps": {"initialData": {"data": {"id": "abc", "status": "CLOSED"}}}}})
    async with _client(page) as c:
        assert (await GlintsConnector().fetch_detail(c, "https://glints.com/x", "abc", {})).closed
    async with _client("", status=404) as c:
        assert (await GlintsConnector().fetch_detail(c, "https://glints.com/x", "abc", {})).closed


@pytest.mark.asyncio
async def test_jobstreet_detail_full_content():
    redux = {"jobdetails": {"result": {"job": {
        "id": "95", "isExpired": False,
        "content": "<p>Tanggung jawab:</p><ul><li>Bangun API</li><li>Tulis tes</li></ul>",
        "salary": {"label": "Rp 8.000.000 – Rp 12.000.000 per month"}, "workTypes": {"label": "Full time"},
        "location": {"label": "Jakarta Selatan, Jakarta Raya"},
        "expiresAt": {"dateTimeUtc": "2099-01-01T00:00:00.000Z"},
        "listedAt": {"dateTimeUtc": "2026-11-13T10:10:18.000Z"},
    }}}}
    html = "<script>window.SEEK_REDUX_DATA = " + json.dumps(redux) + ";\n</script>"
    async with _client(html) as c:
        d = await JobStreetConnector().fetch_detail(c, "u", "95", {})
    assert "• Bangun API" in d.description and "• Tulis tes" in d.description
    assert (d.salary_min, d.salary_max) == (8_000_000, 12_000_000)
    assert d.location_city == "Jakarta Selatan" and not d.closed


@pytest.mark.asyncio
async def test_jobstreet_expired():
    redux = {"jobdetails": {"result": {"job": {"id": "95", "isExpired": True, "content": "<p>x</p>"}}}}
    html = "<script>window.SEEK_REDUX_DATA = " + json.dumps(redux) + ";\n</script>"
    async with _client(html) as c:
        assert (await JobStreetConnector().fetch_detail(c, "u", "95", {})).closed


@pytest.mark.asyncio
async def test_dealls_detail_requirements_and_skills_from_search():
    page = _next({"props": {"pageProps": {"dehydratedState": {"queries": [
        {"queryKey": ["/v1/job-portal/job/slug", "fs-83"], "state": {"data": {"data": {
            "id": "d1", "status": "active", "closed": False,
            "responsibilities": "<ol><li>Mengembangkan aplikasi web</li></ol>",
            "requirements": "<ol><li>Minimal D3/S1</li></ol>", "workplaceType": "onSite"}}}}]}}}})
    raw = {"slug": "fs-83", "company": {"slug": "pt-x"}, "skills": [{"name": "Laravel"}]}
    async with _client(page) as c:
        d = await DeallsConnector().fetch_detail(c, "u", "d1", raw)
    assert "Mengembangkan aplikasi web" in d.description and "Minimal D3/S1" in d.requirements
    assert d.required_skills == [] and d.work_mode is WorkMode.ONSITE  # tidak tampil di halaman Dealls


def test_jsonld_rejects_paragraph_skills():
    job = jobposting_to_jobin({
        "@type": "JobPosting", "title": "Mobile", "hiringOrganization": {"name": "PT X"},
        "skills": "1. Terbiasa menggunakan flutter dan bahasa dart\n2. Mampu menggunakan Git",
    }, external_id="1", url="u")
    assert job.required_skills == []


def test_jsonld_structured_fields():
    job = jobposting_to_jobin({
        "@type": "JobPosting", "title": "Backend", "hiringOrganization": {"name": "PT X"},
        "skills": "Go, PostgreSQL", "jobBenefits": "BPJS, THR",
        "educationRequirements": {"credentialCategory": "bachelor degree"},
        "experienceRequirements": {"monthsOfExperience": 24},
    }, external_id="1", url="u")
    assert job.required_skills == ["Go", "PostgreSQL"] and job.benefits == ["BPJS", "THR"]
    assert job.education == "Minimal Sarjana (S1)" and job.experience_min == 2 and job.is_complete


def _job() -> Job:
    return Job(title="Programmer", company_name="PT X", apply_url="u", dedup_hash="h",
               description="Pengalaman: 1–3 tahun\nSkill: Go", requirements="lama",
               skills=[], required_skills=[], benefits=[], role_category=RoleCategory.OTHER)


def test_apply_detail_replaces_summary_with_source_text():
    job, listing, now = _job(), JobListing(), datetime.now(UTC)
    apply_detail(job, listing, JobDetail(description="Teks asli dari situs", requirements=None,
                                         required_skills=["Go", "PostgreSQL"], experience_min=1, experience_max=3), now)
    assert job.description == "Teks asli dari situs" and job.requirements is None
    assert job.experience_level is ExperienceLevel.JUNIOR and job.detail_fetched_at == now
    assert "Go" in job.skills  # skor dihitung ulang dari skill asli


def test_apply_detail_closed_hides_job():
    job, listing, now = _job(), JobListing(), datetime.now(UTC)
    job.expires_at = now + timedelta(days=30)
    apply_detail(job, listing, JobDetail(closed=True), now)
    assert job.expires_at == now and job.description == "Pengalaman: 1–3 tahun\nSkill: Go"


def test_date_only_deadline_is_end_of_day_in_wib():
    from zoneinfo import ZoneInfo

    from app.connectors.glints import _date
    from app.connectors.jsonld import _dt

    wib = ZoneInfo("Asia/Jakarta")
    for d in (_date("2026-12-11"), _dt("2026-12-11", end_of_day=True)):
        local = d.astimezone(wib)
        assert (local.year, local.month, local.day, local.hour, local.minute) == (2026, 12, 11, 23, 59)
