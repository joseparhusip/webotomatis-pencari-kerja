"""Export lamaran ke Excel: ringkasan + analisis + grafik, daftar lengkap, dan daftar perlu follow-up.

Semua angka & kalimat analisis dihitung dari data lamaran — tidak ada yang dikarang.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from io import BytesIO

from openpyxl import Workbook
from openpyxl.chart import BarChart, DoughnutChart, Reference
from openpyxl.chart.label import DataLabelList
from openpyxl.formatting.rule import DataBarRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.table import Table, TableStyleInfo

from app.models import Application
from app.models.enums import ApplicationStatus as S
from app.services.letter_template import SOURCE_LABELS
from app.web.templating import (
    BULAN, EMPLOYMENT_LABELS, LEVEL_LABELS, MODE_LABELS, ROLE_LABELS, WIB, rupiah_short,
)

STATUS_LABELS = {
    "draft": "Draft", "queued": "Antre", "sent": "Terkirim", "failed": "Gagal", "replied": "Dibalas",
    "interview": "Interview", "offer": "Offer", "rejected": "Ditolak",
}
# Lamaran yang benar-benar sudah keluar ke perusahaan.
OUT = {S.SENT, S.REPLIED, S.INTERVIEW, S.OFFER, S.REJECTED}
ANSWERED = {S.REPLIED, S.INTERVIEW, S.OFFER, S.REJECTED}
POSITIVE = {S.INTERVIEW, S.OFFER}
FOLLOW_UP_DAYS = 7

# Warna (latar, teks) per status.
STATUS_COLORS = {
    "draft": ("EEF0F3", "4B5563"), "queued": ("E0EDFF", "1D4ED8"), "sent": ("E8EEF7", "1F3A5F"),
    "failed": ("FDE2E1", "B42318"), "replied": ("DCF0FF", "0B6BCB"), "interview": ("DDF5E4", "1E7B3A"),
    "offer": ("C9EFD5", "135C2B"), "rejected": ("F3E8E8", "8A2B2B"),
}
NAVY, INK, MUTED, LINE, SOFT = "1F3A5F", "1F2937", "6B7280", "D9DEE5", "F5F7FA"
FONT = "Calibri"
THIN = Side(style="thin", color=LINE)


@dataclass
class Row:
    no: int
    status: str
    sent_at: datetime | None  # WIB, naif (Excel tidak kenal zona waktu)
    title: str
    company: str
    url: str
    category: str
    level: str
    mode: str
    employment: str
    location: str
    salary: str
    source: str
    score: int | None
    channel: str
    sent_to: str
    days_waiting: int | None
    last_reply: datetime | None
    language: str
    letter: str
    cv: str
    notes: str
    error: str


def _naive(dt: datetime | None) -> datetime | None:
    return dt.astimezone(WIB).replace(tzinfo=None) if dt else None


def _sources(job) -> str:
    names = sorted({SOURCE_LABELS.get(li.source.name, li.source.name) for li in (job.listings or []) if li.source})
    return ", ".join(names) or "—"


def to_rows(apps: list[Application], now: datetime | None = None) -> list[Row]:
    now = now or datetime.now(UTC)
    rows = []
    for i, a in enumerate(apps, 1):
        j = a.job
        loc = ", ".join(x for x in (j.location_city, j.location_province) if x) or "—"
        salary = rupiah_short(j.salary_min, j.salary_max)
        if salary != "—" and j.salary_is_estimated:
            salary += " (perkiraan)"
        waiting = (now - a.sent_at).days if a.sent_at and a.status == S.SENT else None
        rows.append(Row(
            no=i, status=a.status.value, sent_at=_naive(a.sent_at), title=j.title, company=j.company_name,
            url=j.apply_url or "", category=ROLE_LABELS.get(j.role_category.value if j.role_category else "", "—"),
            level=LEVEL_LABELS.get(j.experience_level.value if j.experience_level else "unknown", "—"),
            mode=MODE_LABELS.get(j.work_mode.value if j.work_mode else "", "—"),
            employment=EMPLOYMENT_LABELS.get(j.employment_type.value if j.employment_type else "", "—"),
            location=loc, salary=salary, source=_sources(j), score=j.match_score,
            channel="Email" if a.apply_mode.value == "email" else "Portal", sent_to=a.sent_to or "",
            days_waiting=waiting, last_reply=_naive(a.last_reply_at),
            language="Inggris" if a.letter_language == "en" else "Indonesia",
            letter={"ai": "AI", "template": "Template", "manual": "Diedit manual"}.get(a.letter_source or "", "—"),
            cv=a.cv.title if a.cv else "—",
            notes=a.notes or "", error=a.last_error or "",
        ))
    return rows


# ---------- analisis ----------

def _pct(a: int, b: int) -> str:
    return f"{round(a * 100 / b)}%" if b else "—"


def _fmt_date(d: datetime) -> str:
    return f"{d.day} {BULAN[d.month - 1]} {d.year}"


def analyze(rows: list[Row]) -> dict:
    out = [r for r in rows if r.status in OUT]
    answered = [r for r in out if r.status in ANSWERED]
    positive = [r for r in out if r.status in POSITIVE]
    drafts = [r for r in rows if r.status == S.DRAFT]
    failed = [r for r in rows if r.status == S.FAILED]
    follow = sorted([r for r in rows if r.days_waiting is not None and r.days_waiting >= FOLLOW_UP_DAYS],
                    key=lambda r: -r.days_waiting)
    scores = [r.score for r in out if r.score is not None]

    weeks: Counter = Counter()
    for r in out:
        if r.sent_at:
            weeks[(r.sent_at - timedelta(days=r.sent_at.weekday())).date()] += 1

    def breakdown(key) -> list[tuple[str, int, int]]:
        total, ans = Counter(), Counter()
        for r in out:
            for k in [s.strip() for s in key(r).split(",")]:
                total[k] += 1
                ans[k] += r.status in ANSWERED
        return [(k, n, ans[k]) for k, n in total.most_common()]

    k = {
        "out": len(out), "answered": len(answered), "positive": len(positive),
        "offer": sum(r.status == S.OFFER for r in out), "rejected": sum(r.status == S.REJECTED for r in out),
        "waiting": sum(r.status == S.SENT for r in out), "drafts": len(drafts), "failed": len(failed),
        "avg_score": round(sum(scores) / len(scores)) if scores else None,
    }
    data = {
        "kpi": k, "follow": follow, "weeks": sorted(weeks.items()),
        "status": [(STATUS_LABELS[s], n) for s in STATUS_LABELS
                   if (n := sum(r.status == s for r in rows))],
        "category": breakdown(lambda r: r.category), "source": breakdown(lambda r: r.source),
        "mode": breakdown(lambda r: r.mode), "level": breakdown(lambda r: r.level),
        "channel": breakdown(lambda r: r.channel),
        "location": breakdown(lambda r: r.location.split(",")[0])[:10],
    }
    data["insights"] = _insights(rows, out, data)
    return data


def _insights(rows: list[Row], out: list[Row], d: dict) -> list[str]:
    k, tips = d["kpi"], []
    if not out:
        tips.append("Belum ada lamaran yang terkirim. Pilih lowongan di halaman Lowongan lalu klik \"Siapkan lamaran\".")
        if k["drafts"]:
            tips.append(f"Ada {k['drafts']} draft menunggu di-review & dikirim.")
        return tips

    dates = [r.sent_at for r in out if r.sent_at]
    if dates:
        first, last = min(dates), max(dates)
        n_weeks = max(1, ((last - first).days // 7) + 1)
        tips.append(f"{k['out']} lamaran terkirim antara {_fmt_date(first)} dan {_fmt_date(last)} "
                    f"— rata-rata {k['out'] / n_weeks:.1f} lamaran per minggu.".replace(".0 ", " "))
    if k["answered"]:
        tips.append(f"Tingkat balasan {_pct(k['answered'], k['out'])} ({k['answered']} dari {k['out']} lamaran). "
                    f"Interview/offer: {k['positive']} ({_pct(k['positive'], k['out'])}).")
    else:
        tips.append(f"Belum ada balasan dari {k['out']} lamaran. Status di aplikasi diubah manual saat HR membalas "
                    "— pastikan sudah diperbarui.")

    if d["follow"]:
        tips.append(f"{len(d['follow'])} lamaran sudah ≥ {FOLLOW_UP_DAYS} hari tanpa kabar "
                    f"(terlama {d['follow'][0].days_waiting} hari) — lihat lembar \"Perlu Follow-up\".")

    cat = d["category"]
    if cat:
        top, n, _ = cat[0]
        tips.append(f"Kategori paling banyak dilamar: {top} ({n} lamaran, {_pct(n, k['out'])}).")
    best = [(name, n, a) for name, n, a in cat if n >= 3 and a]
    if best:
        name, n, a = max(best, key=lambda x: x[2] / x[1])
        tips.append(f"Balasan terbaik per kategori: {name} — {a} dari {n} dibalas ({_pct(a, n)}).")
    src = d["source"]
    if src:
        name, n, a = src[0]
        tips.append(f"Sumber lowongan terbanyak: {name} ({n} lamaran"
                    + (f", {a} dibalas" if a else "") + ").")
    ch = dict((name, (n, a)) for name, n, a in d["channel"])
    if "Email" in ch and "Portal" in ch:
        (ne, ae), (np, ap) = ch["Email"], ch["Portal"]
        tips.append(f"Lewat email: {ne} lamaran ({_pct(ae, ne)} dibalas); lewat portal: {np} ({_pct(ap, np)} dibalas).")
    elif ch:
        name, (n, _) = next(iter(ch.items()))
        tips.append(f"Semua lamaran dikirim lewat {name.lower()}.")

    if k["avg_score"] is not None:
        tips.append(f"Rata-rata skor kecocokan lowongan yang dilamar: {k['avg_score']}/100.")
        ans = [r.score for r in out if r.status in ANSWERED and r.score is not None]
        no = [r.score for r in out if r.status not in ANSWERED and r.score is not None]
        if len(ans) >= 2 and len(no) >= 2:
            tips.append(f"Skor rata-rata yang dibalas {round(sum(ans) / len(ans))} vs yang belum dibalas "
                        f"{round(sum(no) / len(no))}.")
    if k["drafts"]:
        tips.append(f"Masih ada {k['drafts']} draft yang belum dikirim.")
    if k["failed"]:
        tips.append(f"{k['failed']} lamaran gagal terkirim — buka halaman Lamaran untuk memperbaiki.")
    return tips


# ---------- penulisan workbook ----------

def _font(**kw) -> Font:
    return Font(name=FONT, color=kw.pop("color", INK), **kw)


def _title(ws, text: str, sub: str) -> None:
    ws["B2"] = text
    ws["B2"].font = _font(size=18, bold=True, color=NAVY)
    ws["B3"] = sub
    ws["B3"].font = _font(size=10, color=MUTED)
    ws.sheet_view.showGridLines = False
    ws.column_dimensions["A"].width = 2


def _section(ws, cell: str, text: str) -> None:
    ws[cell] = text
    ws[cell].font = _font(size=12, bold=True, color=NAVY)


def _table(ws, top: int, left: int, headers: list[str], rows: list[tuple], pct_col: int | None = None) -> int:
    """Tabel kecil berformat di lembar ringkasan. Mengembalikan baris terakhir."""
    for c, h in enumerate(headers):
        cell = ws.cell(top, left + c, h)
        cell.font = _font(bold=True, color="FFFFFF", size=10)
        cell.fill = PatternFill("solid", fgColor=NAVY)
        cell.alignment = Alignment(horizontal="left" if c == 0 else "center", vertical="center")
    for i, row in enumerate(rows, 1):
        for c, v in enumerate(row):
            cell = ws.cell(top + i, left + c, v)
            cell.font = _font(size=10)
            cell.border = Border(bottom=THIN)
            if i % 2 == 0:
                cell.fill = PatternFill("solid", fgColor=SOFT)
            if c:
                cell.alignment = Alignment(horizontal="center")
            if pct_col is not None and c == pct_col:
                cell.number_format = "0%"
    return top + len(rows)


def _bar(ws, title: str, cats: Reference, vals: Reference, anchor: str, horizontal: bool = True,
         height: float = 7, width: float = 15) -> None:
    ch = BarChart()
    ch.type = "bar" if horizontal else "col"
    ch.title = title
    ch.style = 10
    ch.add_data(vals, titles_from_data=True)
    ch.set_categories(cats)
    ch.legend = None
    ch.height, ch.width = height, width
    ch.y_axis.majorGridlines = None
    ch.y_axis.delete = True
    ch.x_axis.delete = False
    if horizontal:
        ch.x_axis.scaling.orientation = "maxMin"  # urutan sama dengan tabel (terbesar di atas)
    s = ch.series[0]
    s.graphicalProperties.solidFill = NAVY
    s.graphicalProperties.line.solidFill = NAVY
    ch.dataLabels = DataLabelList()
    ch.dataLabels.showVal = True
    ch.gapWidth = 60
    ws.add_chart(ch, anchor)


def _summary_sheet(ws, rows: list[Row], d: dict, generated: datetime) -> None:
    k = d["kpi"]
    _title(ws, "Ringkasan & Analisis Lamaran", f"Dibuat {_fmt_date(generated)}, {generated:%H:%M} WIB · "
           f"{len(rows)} lamaran (termasuk draft)")

    cards = [
        ("Terkirim", k["out"], "lamaran keluar"),
        ("Dibalas", k["answered"], f"{_pct(k['answered'], k['out'])} dari terkirim"),
        ("Interview / offer", k["positive"], f"{_pct(k['positive'], k['out'])} dari terkirim"),
        ("Menunggu kabar", k["waiting"], f"{len(d['follow'])} sudah ≥ {FOLLOW_UP_DAYS} hari"),
        ("Draft", k["drafts"], "belum dikirim"),
        ("Skor rata-rata", k["avg_score"] if k["avg_score"] is not None else "—", "kecocokan /100"),
    ]
    col = 2
    for label, value, sub in cards:
        for r in (5, 6, 7):
            ws.cell(r, col).fill = PatternFill("solid", fgColor=SOFT)
            ws.cell(r, col + 1).fill = PatternFill("solid", fgColor=SOFT)
        ws.merge_cells(start_row=5, start_column=col, end_row=5, end_column=col + 1)
        ws.merge_cells(start_row=6, start_column=col, end_row=6, end_column=col + 1)
        ws.merge_cells(start_row=7, start_column=col, end_row=7, end_column=col + 1)
        ws.cell(5, col, label).font = _font(size=9, bold=True, color=MUTED)
        ws.cell(6, col, value).font = _font(size=22, bold=True, color=NAVY)
        ws.cell(6, col).alignment = Alignment(horizontal="left")
        ws.cell(7, col, sub).font = _font(size=9, color=MUTED)
        ws.cell(5, col).border = Border(top=Side(style="medium", color=NAVY))
        ws.cell(5, col + 1).border = Border(top=Side(style="medium", color=NAVY))
        ws.cell(6, col).border = Border(left=Side(style="thin", color="FFFFFF"))
        col += 2
    ws.row_dimensions[6].height = 32
    for c in range(3, col):
        ws.column_dimensions[get_column_letter(c)].width = 12

    _section(ws, "B9", "Analisis")
    r = 10
    for tip in d["insights"]:
        ws.merge_cells(start_row=r, start_column=2, end_row=r, end_column=18)
        cell = ws.cell(r, 2, f"•  {tip}")
        cell.font = _font(size=10)
        cell.alignment = Alignment(wrap_text=True, vertical="top")
        ws.row_dimensions[r].height = 30 if len(tip) > 120 else 18
        r += 1

    # Tabel data + grafik di sebelahnya. Grafik di kolom J ke kanan.
    r += 1
    blocks = [
        ("Status lamaran", ["Status", "Jumlah"], d["status"], False),
        ("Lamaran per minggu", ["Minggu mulai", "Terkirim"],
         [(f"{w.day} {BULAN[w.month - 1]} {w.year}", n) for w, n in d["weeks"]], False),
        ("Per kategori posisi", ["Kategori", "Terkirim", "Dibalas", "% dibalas"], d["category"], True),
        ("Per sumber lowongan", ["Sumber", "Terkirim", "Dibalas", "% dibalas"], d["source"], True),
        ("Per level", ["Level", "Terkirim", "Dibalas", "% dibalas"], d["level"], True),
        ("Per mode kerja", ["Mode", "Terkirim", "Dibalas", "% dibalas"], d["mode"], True),
        ("Per cara melamar", ["Cara", "Terkirim", "Dibalas", "% dibalas"], d["channel"], True),
        ("Lokasi teratas", ["Kota", "Terkirim", "Dibalas", "% dibalas"], d["location"], True),
    ]
    for title, headers, data, with_rate in blocks:
        if not data:
            continue
        _section(ws, f"B{r}", title)
        top = r + 1
        body = [(n, t, a, a / t if t else 0) for n, t, a in data] if with_rate else data
        last = _table(ws, top, 2, headers, body, pct_col=3 if with_rate else None)
        n = len(body)
        if title == "Lamaran per minggu":
            _bar(ws, title, Reference(ws, min_col=2, min_row=top + 1, max_row=last),
                 Reference(ws, min_col=3, min_row=top, max_row=last), f"J{r}", horizontal=False,
                 height=max(6, 6), width=18)
        elif title == "Status lamaran":
            ch = DoughnutChart()
            ch.title = title
            ch.add_data(Reference(ws, min_col=3, min_row=top, max_row=last), titles_from_data=True)
            ch.set_categories(Reference(ws, min_col=2, min_row=top + 1, max_row=last))
            ch.dataLabels = DataLabelList()
            ch.dataLabels.showVal = True
            ch.height, ch.width = 7, 12
            ch.holeSize = 55
            from openpyxl.chart.series import DataPoint
            labels = [ws.cell(top + i, 2).value for i in range(1, n + 1)]
            back = {v: k for k, v in STATUS_LABELS.items()}
            for i, lab in enumerate(labels):
                pt = DataPoint(idx=i)
                pt.graphicalProperties.solidFill = STATUS_COLORS[back[lab]][1]
                ch.series[0].dPt.append(pt)
            ws.add_chart(ch, f"J{r}")
        else:
            _bar(ws, title, Reference(ws, min_col=2, min_row=top + 1, max_row=last),
                 Reference(ws, min_col=3, min_row=top, max_row=last), f"J{r}",
                 height=max(5, 0.75 * n + 2.5))
        # beri ruang untuk grafik
        chart_rows = 15 if title in ("Lamaran per minggu", "Status lamaran") else max(11, int(1.5 * n + 5))
        r = max(last, r + chart_rows) + 2
    ws.column_dimensions["B"].width = 24


LIST_COLUMNS = [
    # (judul, lebar, atribut)
    ("No", 5, "no"), ("Status", 12, "status"), ("Tanggal kirim", 14, "sent_at"), ("Posisi", 38, "title"),
    ("Perusahaan", 30, "company"), ("Kategori", 13, "category"), ("Level", 12, "level"), ("Mode", 9, "mode"),
    ("Jenis kerja", 12, "employment"), ("Lokasi", 24, "location"), ("Gaji", 20, "salary"),
    ("Sumber", 14, "source"), ("Skor", 8, "score"), ("Cara melamar", 12, "channel"),
    ("Dikirim ke", 26, "sent_to"), ("Menunggu (hari)", 11, "days_waiting"), ("Balasan terakhir", 14, "last_reply"),
    ("Bahasa surat", 11, "language"), ("Surat dibuat", 13, "letter"), ("CV", 24, "cv"), ("Catatan", 30, "notes"),
    ("Kendala", 30, "error"),
]


def _list_sheet(ws, rows: list[Row], name: str, title: str, sub: str, columns=LIST_COLUMNS,
                empty: str = "Belum ada lamaran.") -> None:
    _title(ws, title, sub)
    head = 5
    for c, (h, w, _) in enumerate(columns, 2):
        cell = ws.cell(head, c, h)
        cell.font = _font(bold=True, color="FFFFFF", size=10)
        cell.fill = PatternFill("solid", fgColor=NAVY)
        cell.alignment = Alignment(vertical="center", wrap_text=True)
        ws.column_dimensions[get_column_letter(c)].width = w
    ws.row_dimensions[head].height = 30
    for i, row in enumerate(rows, head + 1):
        for c, (_, _, attr) in enumerate(columns, 2):
            v = getattr(row, attr)
            if attr == "status":
                v = STATUS_LABELS.get(v, v)
            cell = ws.cell(i, c, v if v not in ("", None) else ("—" if attr not in ("notes", "error", "sent_to",
                                                                                     "days_waiting", "score") else None))
            cell.font = _font(size=10)
            cell.alignment = Alignment(vertical="top", wrap_text=attr in ("title", "company", "notes", "error"))
            if attr in ("sent_at", "last_reply") and isinstance(v, datetime):
                cell.number_format = "d mmm yyyy"
                cell.alignment = Alignment(horizontal="center", vertical="top")
            elif attr in ("no", "score", "days_waiting", "mode", "level", "channel", "language"):
                cell.alignment = Alignment(horizontal="center", vertical="top")
            if attr == "title" and row.url:
                cell.hyperlink = row.url
                cell.font = _font(size=10, color="0B6BCB", underline="single")
            if attr == "status":
                bg, fg = STATUS_COLORS.get(row.status, (SOFT, INK))
                cell.fill = PatternFill("solid", fgColor=bg)
                cell.font = _font(size=10, bold=True, color=fg)
                cell.alignment = Alignment(horizontal="center", vertical="top")
            if attr == "days_waiting" and isinstance(v, int) and v >= FOLLOW_UP_DAYS:
                cell.font = _font(size=10, bold=True, color="B42318")
    last_row = head + max(len(rows), 1)
    if not rows:
        ws.cell(head + 1, 2, empty).font = _font(size=10, color=MUTED)
    ref = f"B{head}:{get_column_letter(len(columns) + 1)}{last_row}"
    tbl = Table(displayName=name, ref=ref)
    tbl.tableStyleInfo = TableStyleInfo(name="TableStyleLight15", showRowStripes=True)
    ws.add_table(tbl)
    score_col = next((c for c, (_, _, a) in enumerate(columns, 2) if a == "score"), None)
    if score_col and rows:
        L = get_column_letter(score_col)
        ws.conditional_formatting.add(f"{L}{head + 1}:{L}{last_row}",
                                      DataBarRule(start_type="num", start_value=0, end_type="num", end_value=100,
                                                  color="7FA7D9", showValue=True))
    ws.freeze_panes = ws.cell(head + 1, 6)  # header + No/Status/Tanggal/Posisi tetap terlihat
    ws.print_title_rows = f"{head}:{head}"
    ws.page_setup.orientation = "landscape"
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True


def build_workbook(apps: list[Application], now: datetime | None = None) -> bytes:
    now = now or datetime.now(UTC)
    rows = to_rows(apps, now)
    d = analyze(rows)
    generated = now.astimezone(WIB)

    wb = Workbook()
    ws = wb.active
    ws.title = "Ringkasan"
    _summary_sheet(ws, rows, d, generated)

    _list_sheet(wb.create_sheet("Daftar Lamaran"), rows, "DaftarLamaran", "Daftar Lamaran",
                "Klik judul posisi untuk membuka lowongan. Pakai tombol filter di judul kolom untuk memilah.")

    follow_cols = [c for c in LIST_COLUMNS if c[2] in (
        "no", "status", "sent_at", "days_waiting", "title", "company", "channel", "sent_to", "source", "notes")]
    follow_cols.sort(key=lambda c: ["no", "days_waiting", "status", "sent_at", "title", "company", "channel",
                                    "sent_to", "source", "notes"].index(c[2]))
    _list_sheet(wb.create_sheet("Perlu Follow-up"), d["follow"], "PerluFollowUp", "Perlu Follow-up",
                f"Lamaran berstatus Terkirim yang sudah ≥ {FOLLOW_UP_DAYS} hari tanpa kabar, terlama di atas. "
                "Kirim email singkat menanyakan progres, atau tandai Ditolak bila sudah lewat.", follow_cols,
                empty="Tidak ada lamaran yang perlu di-follow-up saat ini.")

    wb.properties.title = "Rekap Lamaran Kerja"
    wb.properties.creator = "Webotomatis Pencari Kerja"
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()
