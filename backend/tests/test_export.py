from datetime import UTC, datetime, timedelta
from io import BytesIO
from types import SimpleNamespace as NS

from openpyxl import load_workbook

from app.models.enums import ApplicationStatus as S, ApplyMode, RoleCategory
from app.web.export import analyze, build_workbook, to_rows

NOW = datetime(2026, 11, 20, 5, 0, tzinfo=UTC)


def _app(i, status, days_ago=None, role=RoleCategory.FULLSTACK, score=80, mode=ApplyMode.PORTAL):
    job = NS(title=f"Dev {i}", company_name=f"PT {i}", apply_url=f"https://x/{i}", role_category=role,
             experience_level=None, work_mode=None, employment_type=None, location_city="Jakarta Selatan",
             location_province="DKI Jakarta", salary_min=None, salary_max=None, salary_is_estimated=False,
             match_score=score, listings=[NS(source=NS(name="jobstreet"))])
    return NS(job=job, status=status, sent_at=NOW - timedelta(days=days_ago) if days_ago is not None else None,
              apply_mode=mode, sent_to=None, last_reply_at=None, letter_language="id", letter_source="ai",
              cv=None, notes=None, last_error=None)


def test_analysis_counts_and_follow_up():
    apps = [_app(1, S.SENT, 10), _app(2, S.SENT, 2), _app(3, S.INTERVIEW, 5, role=RoleCategory.BACKEND),
            _app(4, S.REJECTED, 8), _app(5, S.DRAFT)]
    d = analyze(to_rows(apps, NOW))
    k = d["kpi"]
    assert (k["out"], k["answered"], k["positive"], k["waiting"], k["drafts"]) == (4, 2, 1, 2, 1)
    assert [r.title for r in d["follow"]] == ["Dev 1"]  # hanya Terkirim ≥ 7 hari
    assert ("Full Stack", 3, 1) in d["category"]
    assert any("Tingkat balasan 50%" in t for t in d["insights"])
    assert any("1 lamaran sudah ≥ 7 hari" in t for t in d["insights"])


def test_workbook_has_sheets_links_and_charts():
    data = build_workbook([_app(1, S.SENT, 9), _app(2, S.DRAFT)], NOW)
    wb = load_workbook(BytesIO(data))
    assert wb.sheetnames == ["Ringkasan", "Daftar Lamaran", "Perlu Follow-up"]
    ws = wb["Daftar Lamaran"]
    assert ws["E6"].hyperlink.target == "https://x/1"
    assert ws["C6"].value == "Terkirim"


def test_empty_export():
    wb = load_workbook(BytesIO(build_workbook([], NOW)))
    assert wb["Daftar Lamaran"]["B6"].value == "Belum ada lamaran."
