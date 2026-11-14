"""Tes normalisasi connector dengan potongan struktur data yang dikembalikan masing-masing situs."""

import json

from app.connectors.dealls import DeallsConnector
from app.connectors.glints import GlintsConnector
from app.connectors.jsonld import find_jobposting, item_list_urls, jobposting_to_jobin
from app.models.enums import EmploymentType, ExperienceLevel, WorkMode

JOBPOSTING = {
    "@context": "https://schema.org",
    "@type": "JobPosting",
    "title": "Back-End Engineer",
    "datePosted": "2026-08-20",
    "description": "<p>Membangun API.</p>",
    "qualifications": "<ul><li>Node.js</li><li>PostgreSQL</li></ul>",
    "employmentType": "CONTRACTOR",
    "experienceRequirements": {"@type": "OccupationalExperienceRequirements", "monthsOfExperience": 36},
    "hiringOrganization": {"@type": "Organization", "name": "Contoh Labs"},
    "jobLocation": {"@type": "Place", "address": {"addressLocality": "Jakarta Selatan", "addressRegion": "DKI Jakarta"}},
    "baseSalary": {"currency": "IDR", "value": {"minValue": 4000000, "maxValue": 6000000, "unitText": "MONTH"}},
}


def _html(*nodes: dict) -> str:
    return "".join(f'<script type="application/ld+json">{json.dumps(n)}</script>' for n in nodes)


def test_jobposting_jsonld():
    posting = find_jobposting(_html({"@type": "BreadcrumbList"}, JOBPOSTING))
    job = jobposting_to_jobin(posting, external_id="x", url="https://contoh/x")
    assert job.company_name == "Contoh Labs" and job.location_city == "Jakarta Selatan"
    assert (job.salary_min, job.salary_max) == (4_000_000, 6_000_000)
    assert job.employment_type is EmploymentType.CONTRACT
    assert job.experience_level is ExperienceLevel.MID
    assert "• Node.js" in job.requirements
    assert job.posted_at.year == 2026


def test_item_list_urls():
    page = {"@type": "CollectionPage", "mainEntity": {"@type": "ItemList", "itemListElement": [
        {"@type": "ListItem", "url": "https://www.loker.id/a.html"}, {"@type": "ListItem", "url": "https://www.loker.id/b.html"}]}}
    assert item_list_urls(_html(page)) == ["https://www.loker.id/a.html", "https://www.loker.id/b.html"]


def test_glints_normalize_hierarchical_location_and_hidden_salary():
    job = GlintsConnector()._normalize({
        "id": "abc", "title": "Backend Engineer", "workArrangementOption": "HYBRID", "type": "FULL_TIME",
        "createdAt": "2026-11-07T02:50:44Z", "shouldShowSalary": False,
        "salaries": [{"salaryMode": "MONTH", "minAmount": 3000000, "maxAmount": 3700000, "CurrencyCode": "IDR"}],
        "company": {"name": "PT Contoh", "brandName": "Contoh"}, "minYearsOfExperience": 1, "maxYearsOfExperience": 2,
        "location": {"name": "Setiabudi", "level": 4, "administrativeLevelName": "District", "parents": [
            {"formattedName": "Jakarta Selatan", "level": 3, "administrativeLevelName": "City",
             "parents": [{"formattedName": "DKI Jakarta", "level": 2}]}]},
    })
    assert (job.location_city, job.location_province) == ("Jakarta Selatan", "DKI Jakarta")
    assert job.salary_min is None  # perusahaan menyembunyikan gaji
    assert job.work_mode is WorkMode.HYBRID and job.experience_level is ExperienceLevel.JUNIOR
    assert job.url == "https://glints.com/id/opportunities/jobs/backend-engineer/abc"


def test_dealls_drops_recruiter_personal_data():
    job = DeallsConnector()._normalize({
        "id": "1", "slug": "golang-developer-10", "role": "Golang Developer", "workplaceType": "remote",
        "employmentTypes": ["fullTime"], "salaryRange": {"start": 7000000, "end": 9000000},
        "company": {"name": "PT Contoh", "slug": "pt-contoh"}, "city": {"name": "Jakarta Pusat"},
        "author": {"email": "recruiter@contoh.co.id", "name": "Nama Orang"},
        "publishedAt": "2026-10-02T08:52:05.310Z",
    })
    assert "author" not in job.raw
    assert job.url == "https://dealls.com/loker/golang-developer-10~pt-contoh"
    assert job.description is None  # deskripsi asli diambil dari halaman detail
    assert (job.salary_min, job.salary_max) == (7_000_000, 9_000_000) and job.work_mode is WorkMode.REMOTE
