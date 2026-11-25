"""Export lowongan ke Excel (data lowongan rekaan untuk tes)."""

from datetime import UTC, datetime, timedelta
from io import BytesIO

from openpyxl import load_workbook

from app.models import Job, JobListing, Source
from app.models.enums import ExperienceLevel, RoleCategory, WorkMode
from app.web.jobs_export import _owned_checker, analyze, build_jobs_workbook, to_rows

NOW = datetime(2026, 11, 25, 9, 0, tzinfo=UTC)


def _job(i, **kw):
    src = Source(name=kw.pop("source", "jobstreet"), base_url="https://example.com")
    url = kw.pop("url", f"https://id.jobstreet.com/id/job/{i}")
    j = Job(id=i, title=kw.pop("title", f"Backend Developer {i}"), company_name=kw.pop("company", f"PT Contoh {i}"),
            apply_url=url, role_category=kw.pop("cat", RoleCategory.BACKEND),
            experience_level=ExperienceLevel.JUNIOR, work_mode=WorkMode.ONSITE,
            location_city=kw.pop("city", "Jakarta Selatan"), location_province="DKI Jakarta",
            description=kw.pop("description", "Membangun REST API dengan Go dan PostgreSQL."),
            skills=kw.pop("skills", ["Go", "PostgreSQL"]), required_skills=kw.pop("required", ["Go", "Redis"]),
            benefits=[], match_score=kw.pop("score", 80), posted_at=NOW - timedelta(days=1), **kw)
    j.listings = [JobListing(id=i, url=url, external_id=str(i), source=src)]
    return j


def test_isi_kolom_utama_dan_link():
    jobs = [_job(1, salary_min=6_000_000, salary_max=8_000_000), _job(2, source="glints",
            url="https://glints.com/id/opportunities/jobs/x/2", description="x" * 40_000)]
    rows = to_rows(jobs)
    assert rows[0]["company"] == "PT Contoh 1" and rows[0]["salary"] == "Rp 6–8 jt"
    assert rows[0]["sources"] == "JobStreet" and rows[0]["url"] == "https://id.jobstreet.com/id/job/1"
    assert rows[1]["sources"] == "Glints" and rows[1]["salary"] == "—"
    assert len(rows[1]["description"]) < 32_767                         # batas sel Excel aman

    wb = load_workbook(BytesIO(build_jobs_workbook(jobs, ["Go/Gin", "PostgreSQL"], "2 lowongan dipilih", NOW)))
    assert wb.sheetnames == ["Ringkasan & Analisis", "Lowongan"]
    ws = wb["Lowongan"]
    head = [c.value for c in ws[5]][1:]
    for col in ("Posisi", "Perusahaan", "Gaji / bulan", "Sumber web", "Link lowongan", "Deskripsi"):
        assert col in head
    first = {h: c for h, c in zip(head, ws[6][1:])}
    assert first["Perusahaan"].value == "PT Contoh 1"
    assert first["Link lowongan"].hyperlink.target == "https://id.jobstreet.com/id/job/1"
    assert first["Posisi"].hyperlink is not None
    assert len(wb["Ringkasan & Analisis"]._charts) >= 4


def test_analisis_dan_skill_yang_belum_ada():
    jobs = [_job(i, salary_min=5_000_000 * (i % 2 + 1), required=["Go / Golang", "Redis", "Komunikasi"],
                 expires_at=NOW + timedelta(days=3) if i == 1 else None) for i in range(1, 5)]
    d = analyze(to_rows(jobs), ["Go/Gin", "PostgreSQL"], NOW)
    assert d["n"] == 4 and d["with_salary"] == 4 and d["median_salary"] == 7_500_000
    assert dict(d["gap_skills"]) == {"Redis": 4}      # Go sudah ada (Go/Gin), soft skill tidak dihitung
    assert len(d["soon"]) == 1 and d["by_source"] == [("JobStreet", 4)]


def test_pencocokan_skill_alias_dan_tersirat():
    owned = _owned_checker(["React.js", "Go/Gin", "RESTful API"])
    assert owned("JavaScript") and owned("Go / Golang") and owned("REST API")
    assert not owned("Kubernetes") and not owned("Java")


def test_export_kosong():
    wb = load_workbook(BytesIO(build_jobs_workbook([], [], "semua hasil filter", NOW)))
    assert wb["Lowongan"]["B6"].value == "Tidak ada lowongan."
