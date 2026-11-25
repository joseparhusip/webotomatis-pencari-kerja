"""Ekspor lowongan (dipilih / hasil filter) ke Excel: daftar lengkap + ringkasan & analisis.

Lembar:
  1. "Ringkasan & Analisis" — angka utama, sebaran per sumber/bidang/lokasi/level/gaji (dengan grafik),
     skill CV yang paling dicari, skill yang sering diminta tapi belum ada di CV, dan kesimpulan.
  2. "Lowongan" — satu baris per lowongan: posisi (link), perusahaan, gaji, sumber web, link, lokasi,
     bidang, level, skor, skill, persyaratan, batas lamar, deskripsi lengkap.
Semua isi berasal dari data lowongan di database (hasil scraping situs sumber) — tidak ada angka karangan.
"""

from __future__ import annotations

import re
from collections import Counter
from datetime import UTC, datetime
from io import BytesIO
from statistics import median

from openpyxl import Workbook
from openpyxl.chart import Reference
from openpyxl.formatting.rule import DataBarRule
from openpyxl.styles import Alignment, Border, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.table import Table, TableStyleInfo

from app.models import Job
from app.services.letter_template import SOURCE_LABELS
from app.web.export import INK, MUTED, NAVY, SOFT, THIN, _bar, _font, _fmt_date, _pct, _section, _table, _title
from app.web.templating import (
    EMPLOYMENT_LABELS, LEVEL_LABELS, MODE_LABELS, ROLE_LABELS, WIB, rupiah_short, short_city,
)

MAX_CELL = 32000          # batas isi satu sel Excel ±32.767 karakter
GREEN = "1E7B3A"
_SOFT_REQ = re.compile(r"komunikasi|communication|kerja ?sama|teamwork|english|bahasa|leadership|problem|analytical|"
                       r"analitis|disiplin|jujur|detail|interpersonal|presentasi|presentation|time management|"
                       r"manajemen waktu|mandiri|independent|proaktif|kreatif|creative|bertanggung", re.I)

COLUMNS = [
    # (judul, lebar, kunci)
    ("No", 5, "no"), ("Posisi", 34, "title"), ("Perusahaan", 28, "company"), ("Gaji / bulan", 18, "salary"),
    ("Sumber web", 16, "sources"), ("Link lowongan", 40, "url"), ("Link sumber lain", 40, "other_urls"),
    ("Lokasi", 22, "location"), ("Bidang", 16, "category"), ("Level", 12, "level"), ("Mode", 9, "mode"),
    ("Jenis kerja", 13, "employment"), ("Skor", 8, "score"), ("Alasan skor", 34, "reason"),
    ("Skill cocok dengan CV", 26, "skills"), ("Skill diminta", 30, "required"), ("Pendidikan", 16, "education"),
    ("Pengalaman", 14, "experience"), ("Diposting", 13, "posted"), ("Batas lamar", 13, "expires"),
    ("Kirim via", 11, "channel"), ("Email HR", 26, "hr_email"), ("Benefit", 30, "benefits"),
    ("Deskripsi", 90, "description"),
]
WRAP = {"title", "company", "reason", "skills", "required", "benefits", "description", "other_urls", "sources"}
CENTER = {"no", "score", "level", "mode", "employment", "posted", "expires", "channel"}


def _naive(dt: datetime | None) -> datetime | None:
    return dt.astimezone(WIB).replace(tzinfo=None) if dt else None


def _experience(j: Job) -> str:
    lo, hi = j.experience_min, j.experience_max
    if lo is None and hi is None:
        return "—"
    if lo is not None and hi is not None and lo != hi:
        return f"{lo}–{hi} tahun"
    return "Fresh graduate" if (lo or 0) == 0 and hi in (None, 0) else f"Min. {lo or hi} tahun"


def to_rows(jobs: list[Job]) -> list[dict]:
    rows = []
    for i, j in enumerate(jobs, 1):
        listings = sorted(j.listings or [], key=lambda li: li.id or 0)
        sources = []
        for li in listings:
            name = SOURCE_LABELS.get(li.source.name, li.source.name) if li.source else ""
            if name and name not in sources:
                sources.append(name)
        url = j.apply_url or (listings[0].url if listings else "")
        others = [li.url for li in listings if li.url and li.url != url]
        desc = "\n\n".join(p for p in (j.description, j.requirements) if p) or ""
        salary = rupiah_short(j.salary_min, j.salary_max)
        if salary != "—" and j.salary_is_estimated:
            salary += " (perkiraan)"
        rows.append({
            "no": i, "id": j.id, "title": j.title, "company": j.company_name, "salary": salary,
            "salary_mid": _mid(j.salary_min, j.salary_max),
            "sources": ", ".join(sources) or "—", "source_list": sources or ["—"], "url": url,
            "other_urls": "\n".join(others),
            "location": ", ".join(x for x in (j.location_city, j.location_province) if x) or "—",
            "city": short_city(j.location_city) if j.location_city else (j.location_province or "Tidak disebut"),
            "category": ROLE_LABELS.get(j.role_category.value if j.role_category else "", "—"),
            "level": LEVEL_LABELS.get(j.experience_level.value if j.experience_level else "unknown", "—"),
            "mode": MODE_LABELS.get(j.work_mode.value if j.work_mode else "", "—"),
            "employment": EMPLOYMENT_LABELS.get(j.employment_type.value if j.employment_type else "", "—"),
            "score": j.match_score, "reason": j.match_reason or "",
            "skills": ", ".join(j.skills or []), "skill_list": list(j.skills or []),
            "required": ", ".join(j.required_skills or []), "required_list": list(j.required_skills or []),
            "education": j.education or "—", "experience": _experience(j),
            "posted": _naive(j.posted_at), "expires": _naive(j.expires_at),
            "channel": "Email" if j.hr_email else "Portal", "hr_email": j.hr_email or "",
            "benefits": ", ".join(j.benefits or []),
            "description": (desc[:MAX_CELL] + " …(dipotong)") if len(desc) > MAX_CELL else desc,
        })
    return rows


def _mid(lo: int | None, hi: int | None) -> int | None:
    vals = [v for v in (lo, hi) if v]
    return int(sum(vals) / len(vals)) if vals else None


def _salary_bucket(mid: int | None) -> str:
    if mid is None:
        return "Tidak disebut"
    for limit, label in ((3_000_000, "< Rp 3 jt"), (5_000_000, "Rp 3–5 jt"), (8_000_000, "Rp 5–8 jt"),
                         (12_000_000, "Rp 8–12 jt"), (20_000_000, "Rp 12–20 jt")):
        if mid < limit:
            return label
    return "≥ Rp 20 jt"


_BUCKET_ORDER = ["< Rp 3 jt", "Rp 3–5 jt", "Rp 5–8 jt", "Rp 8–12 jt", "Rp 12–20 jt", "≥ Rp 20 jt", "Tidak disebut"]


def _juta(v: int) -> str:
    return f"Rp {v / 1_000_000:.1f} jt".replace(".0 jt", " jt").replace(".", ",")


def _owned_checker(cv_skills: list[str]):
    """Skill iklan dianggap SUDAH ada di CV bila sama setelah alias ('Go / Golang' = 'Go/Gin',
    'REST API' = 'RESTful API') atau tersirat ('JavaScript' dari React/Node.js/TypeScript)."""
    from types import SimpleNamespace

    from app.services.letter_template import owned_skills, skill_key
    from app.services.targeting import matched_skills

    parts = [p for s in cv_skills for p in re.split(r"\s*/\s*", s) if p]
    owned = owned_skills(SimpleNamespace(skills=list(cv_skills) + parts))

    def check(required: str) -> bool:
        pieces = [p for p in re.split(r"\s*/\s*", required) if p] or [required]
        if any(skill_key(p) in owned for p in pieces):
            return True
        return bool(matched_skills(required, skills=cv_skills)) if cv_skills else False
    return check


def analyze(rows: list[dict], cv_skills: list[str], now: datetime) -> dict:
    n = len(rows)
    with_salary = [r["salary_mid"] for r in rows if r["salary_mid"]]
    scores = [r["score"] for r in rows if r["score"] is not None]
    is_owned = _owned_checker(cv_skills)
    gap = Counter()
    for r in rows:
        for s in r["required_list"]:
            key = s.strip()
            if key and len(key) <= 30 and not _SOFT_REQ.search(key) and not is_owned(key):
                gap[key] += 1
    soon = [r for r in rows if r["expires"] and 0 <= (r["expires"] - now.astimezone(WIB).replace(tzinfo=None)).days <= 7]
    return {
        "n": n, "with_salary": len(with_salary),
        "median_salary": int(median(with_salary)) if with_salary else None,
        "avg_score": round(sum(scores) / len(scores)) if scores else None,
        "high": sum(1 for s in scores if s >= 70),
        "email": sum(1 for r in rows if r["channel"] == "Email"),
        "soon": soon,
        "by_source": Counter(s for r in rows for s in r["source_list"]).most_common(),
        "by_category": Counter(r["category"] for r in rows).most_common(),
        "by_city": Counter(r["city"] for r in rows).most_common(10),
        "by_level": Counter(r["level"] for r in rows).most_common(),
        "by_mode": Counter(r["mode"] for r in rows).most_common(),
        "by_salary": sorted(Counter(_salary_bucket(r["salary_mid"]) for r in rows).items(),
                            key=lambda kv: _BUCKET_ORDER.index(kv[0])),
        "top_skills": Counter(s for r in rows for s in r["skill_list"]).most_common(10),
        "gap_skills": gap.most_common(10),
        "top_jobs": sorted((r for r in rows if r["score"] is not None), key=lambda r: -r["score"])[:5],
    }


def _insights(d: dict) -> list[str]:
    n = d["n"]
    if not n:
        return ["Tidak ada lowongan yang diekspor."]
    out = []
    if d["with_salary"]:
        out.append(f"{d['with_salary']} dari {n} lowongan ({_pct(d['with_salary'], n)}) mencantumkan gaji; "
                   f"nilai tengahnya {_juta(d['median_salary'])} per bulan.")
    else:
        out.append(f"Tidak ada dari {n} lowongan yang mencantumkan gaji.")
    if d["by_source"]:
        name, c = d["by_source"][0]
        out.append(f"Sumber terbanyak: {name} ({c} lowongan, {_pct(c, n)}).")
    if d["by_city"]:
        name, c = d["by_city"][0]
        out.append(f"Lokasi terbanyak: {name} ({c} lowongan).")
    if d["avg_score"] is not None:
        out.append(f"Skor kecocokan rata-rata {d['avg_score']}/100; {d['high']} lowongan berskor ≥ 70 — "
                   "prioritaskan untuk dilamar lebih dulu.")
    if d["top_skills"]:
        out.append("Skill CV-mu yang paling sering diminta: " +
                   ", ".join(f"{s} ({c}×)" for s, c in d["top_skills"][:5]) + ".")
    if d["gap_skills"]:
        out.append("Sering diminta tapi belum tercantum di CV: " +
                   ", ".join(f"{s} ({c}×)" for s, c in d["gap_skills"][:5]) +
                   " — pertimbangkan dipelajari, atau cantumkan bila sebenarnya sudah dikuasai.")
    if d["soon"]:
        out.append(f"{len(d['soon'])} lowongan ditutup dalam 7 hari ke depan — lihat kolom Batas lamar.")
    if d["email"]:
        out.append(f"{d['email']} lowongan bisa dilamar langsung lewat email HR.")
    return out


def _summary_sheet(ws, rows: list[dict], d: dict, generated: datetime, scope: str) -> None:
    _title(ws, "Ringkasan & Analisis Lowongan",
           f"Dibuat {_fmt_date(generated)}, {generated:%H:%M} WIB · {scope} · {d['n']} lowongan")
    cards = [
        ("Lowongan", d["n"], scope),
        ("Mencantumkan gaji", d["with_salary"], f"{_pct(d['with_salary'], d['n'])} dari total"),
        ("Gaji tengah", _juta(d["median_salary"]) if d["median_salary"] else "—", "per bulan (median)"),
        ("Skor rata-rata", d["avg_score"] if d["avg_score"] is not None else "—", "kecocokan /100"),
        ("Skor ≥ 70", d["high"], "prioritas dilamar"),
        ("Lewat email HR", d["email"], "bisa kirim langsung"),
    ]
    for i, (label, value, sub) in enumerate(cards):
        col = 2 + i * 2
        ws.merge_cells(start_row=5, start_column=col, end_row=5, end_column=col + 1)
        ws.merge_cells(start_row=6, start_column=col, end_row=6, end_column=col + 1)
        ws.merge_cells(start_row=7, start_column=col, end_row=7, end_column=col + 1)
        for r, (v, f) in enumerate(((label, _font(size=9, color=MUTED)), (value, _font(size=20, bold=True, color=NAVY)),
                                    (sub, _font(size=9, color=MUTED))), 5):
            cell = ws.cell(r, col, v)
            cell.font = f
            cell.alignment = Alignment(horizontal="left", vertical="center", indent=1)
        for r in (5, 6, 7):
            for c in (col, col + 1):
                ws.cell(r, c).fill = PatternFill("solid", fgColor=SOFT)
                ws.cell(r, c).border = Border(left=THIN if c == col else None, right=THIN if c == col + 1 else None,
                                              top=THIN if r == 5 else None, bottom=THIN if r == 7 else None)
    ws.row_dimensions[6].height = 30
    for c in range(2, 16):
        ws.column_dimensions[get_column_letter(c)].width = 13
    ws.column_dimensions["B"].width = 26

    _section(ws, "B9", "Analisis")
    row = 10
    for text in _insights(d):
        ws.merge_cells(start_row=row, start_column=2, end_row=row, end_column=13)
        cell = ws.cell(row, 2, f"•  {text}")
        cell.font = _font(size=10)
        cell.alignment = Alignment(wrap_text=True, vertical="top")
        ws.row_dimensions[row].height = 30
        row += 1

    row += 1
    blocks = [
        ("Per sumber web", ["Sumber", "Lowongan", "%"], d["by_source"]),
        ("Per bidang", ["Bidang", "Lowongan", "%"], d["by_category"]),
        ("Lokasi teratas", ["Kota / kab.", "Lowongan", "%"], d["by_city"]),
        ("Rentang gaji (nilai tengah)", ["Rentang", "Lowongan", "%"], d["by_salary"]),
        ("Per level", ["Level", "Lowongan", "%"], d["by_level"]),
        ("Per mode kerja", ["Mode", "Lowongan", "%"], d["by_mode"]),
        ("Skill CV yang paling sering diminta", ["Skill", "Lowongan", "%"], d["top_skills"]),
        ("Diminta tapi belum ada di CV", ["Skill", "Lowongan", "%"], d["gap_skills"]),
    ]
    for title, headers, data in blocks:
        _section(ws, f"B{row}", title)
        top = row + 1
        if not data:
            ws.cell(top, 2, "Tidak ada data.").font = _font(size=10, color=MUTED)
            row = top + 2
            continue
        table_rows = [(name, c, c / d["n"] if d["n"] else 0) for name, c in data]
        last = _table(ws, top, 2, headers, table_rows, pct_col=2)
        cats = Reference(ws, min_col=2, min_row=top + 1, max_row=last)
        vals = Reference(ws, min_col=3, min_row=top, max_row=last)
        _bar(ws, title, cats, vals, f"G{top}", height=max(4.5, 0.75 * len(data) + 2), width=14)
        row = max(last + 2, top + int(max(4.5, 0.75 * len(data) + 2) * 2) + 2)

    if d["top_jobs"]:
        _section(ws, f"B{row}", "5 lowongan dengan skor tertinggi")
        last = _table(ws, row + 1, 2, ["Posisi", "Perusahaan", "Skor", "Gaji"],
                      [(r["title"], r["company"], r["score"], r["salary"]) for r in d["top_jobs"]])
        for i, r in enumerate(d["top_jobs"], row + 2):
            if r["url"]:
                cell = ws.cell(i, 2)
                cell.hyperlink = r["url"]
                cell.font = _font(size=10, color="0B6BCB", underline="single")
        ws.column_dimensions["C"].width = 26
        row = last + 1
    ws.sheet_view.zoomScale = 90


def _list_sheet(ws, rows: list[dict]) -> None:
    _title(ws, "Daftar Lowongan", "Klik posisi atau link untuk membuka lowongan di situs sumbernya. "
                                  "Pakai tombol filter di judul kolom untuk memilah.")
    head = 5
    for c, (h, w, _) in enumerate(COLUMNS, 2):
        cell = ws.cell(head, c, h)
        cell.font = _font(bold=True, color="FFFFFF", size=10)
        cell.fill = PatternFill("solid", fgColor=NAVY)
        cell.alignment = Alignment(vertical="center", wrap_text=True)
        ws.column_dimensions[get_column_letter(c)].width = w
    ws.row_dimensions[head].height = 30
    for i, r in enumerate(rows, head + 1):
        for c, (_, _, key) in enumerate(COLUMNS, 2):
            v = r[key]
            cell = ws.cell(i, c, v if v not in ("", None) else ("—" if key not in ("other_urls", "hr_email", "benefits",
                                                                                  "reason", "skills", "required",
                                                                                  "description", "score") else None))
            cell.font = _font(size=10)
            cell.alignment = Alignment(vertical="top", wrap_text=key in WRAP,
                                       horizontal="center" if key in CENTER else None)
            if key in ("posted", "expires") and isinstance(v, datetime):
                cell.number_format = "d mmm yyyy"
            if key in ("title", "url") and r["url"]:
                cell.hyperlink = r["url"]
                cell.font = _font(size=10, color="0B6BCB", underline="single", bold=key == "title")
            if key == "salary" and v != "—":
                cell.font = _font(size=10, bold=True, color=GREEN)
            if key == "description":
                cell.font = _font(size=9, color=INK)
        ws.row_dimensions[i].height = 75  # deskripsi panjang: baris dibatasi, isi lengkap terlihat saat sel dibuka
    last = head + max(len(rows), 1)
    if not rows:
        ws.cell(head + 1, 2, "Tidak ada lowongan.").font = _font(size=10, color=MUTED)
    tbl = Table(displayName="DaftarLowongan", ref=f"B{head}:{get_column_letter(len(COLUMNS) + 1)}{last}")
    tbl.tableStyleInfo = TableStyleInfo(name="TableStyleLight15", showRowStripes=True)
    ws.add_table(tbl)
    score_col = next(c for c, (_, _, k) in enumerate(COLUMNS, 2) if k == "score")
    if rows:
        L = get_column_letter(score_col)
        ws.conditional_formatting.add(f"{L}{head + 1}:{L}{last}", DataBarRule(
            start_type="num", start_value=0, end_type="num", end_value=100, color="7FA7D9", showValue=True))
    ws.freeze_panes = ws.cell(head + 1, 4)  # header + No/Posisi tetap terlihat saat digulir
    ws.print_title_rows = f"{head}:{head}"
    ws.page_setup.orientation = "landscape"
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True


def build_jobs_workbook(jobs: list[Job], cv_skills: list[str] | None = None, scope: str = "lowongan dipilih",
                        now: datetime | None = None) -> bytes:
    now = now or datetime.now(UTC)
    rows = to_rows(jobs)
    d = analyze(rows, cv_skills or [], now)
    wb = Workbook()
    summary = wb.active
    summary.title = "Ringkasan & Analisis"
    _summary_sheet(summary, rows, d, now.astimezone(WIB), scope)
    _list_sheet(wb.create_sheet("Lowongan"), rows)
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()
