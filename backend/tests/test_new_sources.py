"""Parser LinkedIn (halaman publik) & Karirhub Kemnaker (payload Next.js). HTML di sini sintetis, meniru struktur situs."""

import json
from datetime import UTC, datetime

from app.connectors import REGISTRY
from app.connectors.karirhub import find_vacancy, parse_list, split_region
from app.connectors.linkedin import LinkedInConnector, parse_detail, parse_search_cards, split_linkedin_location
from app.models.enums import EmploymentType, WorkMode

LI_CARD = """
<li><div class="base-card base-search-card job-search-card" data-entity-urn="urn:li:jobPosting:4000000001">
  <a class="base-card__full-link" href="https://id.linkedin.com/jobs/view/data-analyst-at-pt-contoh-4000000001?position=1&amp;refId=x"></a>
  <div class="search-entity-media"><img class="artdeco-entity-image" data-delayed-url="https://media.example/logo.png" alt></div>
  <div class="base-search-card__info">
    <h3 class="base-search-card__title">  Data Analyst  </h3>
    <h4 class="base-search-card__subtitle"><a class="hidden-nested-link" href="#"> PT Contoh Data </a></h4>
    <div class="base-search-card__metadata">
      <span class="job-search-card__location"> South Jakarta, Jakarta, Indonesia </span>
      <span class="job-search-card__salary-info"> IDR 8,000,000 - IDR 10,000,000 per month </span>
      <time class="job-search-card__listdate" datetime="2026-11-21"> 5 days ago </time>
    </div></div></div></li>
<li><div class="base-card base-search-card" data-entity-urn="urn:li:jobPosting:4000000002">
  <a class="base-card__full-link" href="https://id.linkedin.com/jobs/view/x-4000000002"></a>
  <div class="base-search-card__info"><h3 class="base-search-card__title">Backend Engineer</h3>
  <h4 class="base-search-card__subtitle">Startup Contoh</h4>
  <div class="base-search-card__metadata"><span class="job-search-card__location">Indonesia (Remote)</span></div></div></div></li>
"""

LI_DETAIL = """
<section class="show-more-less-html"><div class="show-more-less-html__markup">
  <strong>Responsibilities</strong><ul><li>Membuat dashboard</li><li>Menulis query SQL</li></ul></div></section>
<ul class="description__job-criteria-list">
  <li class="description__job-criteria-item"><h3 class="description__job-criteria-subheader"> Seniority level </h3>
    <span class="description__job-criteria-text"> Entry level </span></li>
  <li class="description__job-criteria-item"><h3 class="description__job-criteria-subheader"> Employment type </h3>
    <span class="description__job-criteria-text"> Full-time </span></li>
</ul>
"""


def test_linkedin_kartu_pencarian():
    cards = parse_search_cards(LI_CARD)
    assert [c["id"] for c in cards] == ["4000000001", "4000000002"]
    job = LinkedInConnector()._normalize(cards[0])
    assert job.title == "Data Analyst" and job.company_name == "PT Contoh Data"
    assert job.url == "https://id.linkedin.com/jobs/view/data-analyst-at-pt-contoh-4000000001"  # tanpa parameter pelacak
    assert (job.location_city, job.location_province) == ("Jakarta Selatan", "Jakarta")
    assert (job.salary_min, job.salary_max) == (8_000_000, 10_000_000)
    assert job.posted_at == datetime(2026, 11, 21, tzinfo=UTC)
    assert job.company_logo_url == "https://media.example/logo.png"

    remote = LinkedInConnector()._normalize(cards[1])
    assert remote.work_mode == WorkMode.REMOTE and remote.location_city is None and remote.salary_min is None


def test_linkedin_lokasi():
    assert split_linkedin_location("Jakarta Metropolitan Area") == ("Jakarta", None, False)
    assert split_linkedin_location("Indonesia") == (None, None, False)


def test_linkedin_detail():
    d = parse_detail(LI_DETAIL)
    assert d.employment_type == EmploymentType.FULLTIME and not d.closed
    assert "• Membuat dashboard" in d.description and "• Menulis query SQL" in d.description
    assert parse_detail("<div class='closed-job'>No longer accepting applications</div>").closed


def _next_page(payload: str, links: list[str] = ()) -> str:
    """Halaman Next.js sintetis: payload RSC dipecah ke beberapa self.__next_f.push seperti aslinya."""
    half = len(payload) // 2
    pushes = "".join(f"<script>self.__next_f.push([1,{json.dumps(part)}])</script>"
                     for part in (payload[:half], payload[half:]))
    anchors = "".join(f'<a href="{href}">x</a>' for href in links)
    return f"<html><body>{anchors}{pushes}</body></html>"


KH_ID = "01a00000-0000-7000-8000-000000000001"
KH_ROW = {"id": KH_ID, "title": "Staf Administrasi", "companyName": "PT Contoh Makmur", "companyLogoUri": None,
          "cityName": "Kota Bandung, Jawa Barat", "jobTypeName": "Full Time", "jobFunctionName": "Administrasi",
          "skills": ["Microsoft Excel", "Arsip"], "showSalary": True, "minSalaryAmount": 4500000,
          "maxSalaryAmount": 5500000, "minSalaryCurrency": "IDR", "publishedAt": 1795688734, "expiresAt": 1800810000}


def test_karirhub_daftar():
    hidden = {**KH_ROW, "id": "01a00000-0000-7000-8000-000000000002", "showSalary": False, "cityName": None}
    payload = '1:["$","div",null,{"data":' + json.dumps([KH_ROW, hidden]) + ',"perPage":18,"to":18,"total":40}]'
    html = _next_page(payload, [f"/lowongan-dalam-negeri/lowongan/staf-administrasi-{KH_ID}"])
    rows, total, paths = parse_list(html)
    assert total == 40 and len(rows) == 2
    job = REGISTRY["karirhub"]._normalize(rows[0], paths.get(KH_ID))
    assert job.url.endswith(f"/lowongan/staf-administrasi-{KH_ID}")
    assert (job.salary_min, job.salary_max, job.location_city) == (4_500_000, 5_500_000, "Bandung")
    assert job.employment_type == EmploymentType.FULLTIME and job.source_category == "Administrasi"
    assert job.required_skills == ["Microsoft Excel", "Arsip"]
    assert job.posted_at.tzinfo is not None and job.expires_at > job.posted_at
    other = REGISTRY["karirhub"]._normalize(rows[1], None)
    assert other.salary_min is None and other.location_city is None   # gaji disembunyikan perusahaan → tidak ditebak


def test_karirhub_detail():
    vac = {"id": KH_ID, "status": "published", "description": "<ul><li>Input data</li></ul>",
           "qualification": "<p>Minimal SMA</p>", "minYearExperience": 1, "expiresAt": "2027-01-25",
           "region": {"name": "Kabupaten Bogor, Jawa Barat"}, "jobType": {"name": "Contract"},
           "minEducation": {"name": "SMA/SMK"}, "showSalary": False}
    html = _next_page('5:["$","main",null,{"vacancy":' + json.dumps(vac) + "}]")
    v = find_vacancy(html, KH_ID)
    assert v and v["qualification"] == "<p>Minimal SMA</p>"
    assert split_region("Kota Adm. Jakarta Barat, DKI Jakarta") == ("Jakarta Barat", "DKI Jakarta")
    assert split_region("Kabupaten Bogor, Jawa Barat") == ("Kab. Bogor", "Jawa Barat")


def test_sumber_terdaftar():
    assert {"linkedin", "karirhub"} <= set(REGISTRY)
    assert REGISTRY["karirhub"].keywords(["admin", "data"]) == [""]
