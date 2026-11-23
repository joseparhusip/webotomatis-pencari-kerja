from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi.templating import Jinja2Templates

WIB = ZoneInfo("Asia/Jakarta")
BULAN = ["Jan", "Feb", "Mar", "Apr", "Mei", "Jun", "Jul", "Agu", "Sep", "Okt", "Nov", "Des"]

ROLE_LABELS = {"fullstack": "Full Stack", "frontend": "Frontend", "backend": "Backend",
               "mobile": "Mobile", "qa": "QA / Tester", "it_support": "IT Support", "data": "Data / BI",
               "it_other": "IT Lainnya", "other": "Non-IT"}
MODE_LABELS = {"onsite": "WFO", "remote": "Remote", "hybrid": "Hybrid"}
LEVEL_LABELS = {"entry": "Entry", "junior": "Junior", "mid": "Mid", "senior": "Senior",
                "unknown": "Tidak disebut"}
EMPLOYMENT_LABELS = {"fulltime": "Penuh waktu", "contract": "Kontrak", "internship": "Magang",
                     "freelance": "Freelance", "parttime": "Paruh waktu"}
STATUS_LABELS = {"new": "Baru", "seen": "Dilihat", "saved": "Disimpan", "hidden": "Disembunyikan",
                 "applied": "Dilamar"}


def _juta(v: int) -> str:
    s = f"{v / 1_000_000:.1f}".rstrip("0").rstrip(".")
    return s.replace(".", ",")


def rupiah_short(lo: int | None, hi: int | None) -> str:
    if not lo and not hi:
        return "—"
    lo, hi = lo or hi, hi or lo
    if hi < 1_000_000:
        return f"Rp {lo:,}".replace(",", ".")
    if lo == hi:
        return f"Rp {_juta(lo)} jt"
    return f"Rp {_juta(lo)}–{_juta(hi)} jt"


def rupiah_long(lo: int | None, hi: int | None) -> str:
    if not lo and not hi:
        return "Tidak disebutkan"
    fmt = lambda v: f"Rp {v:,}".replace(",", ".")  # noqa: E731
    lo, hi = lo or hi, hi or lo
    return f"{fmt(lo)} / bulan" if lo == hi else f"{fmt(lo)} – {fmt(hi)} / bulan"


def date_id(dt: datetime | None, with_time: bool = False) -> str:
    if not dt:
        return "—"
    d = dt.astimezone(WIB)
    s = f"{d.day} {BULAN[d.month - 1]} {d.year}"
    return f"{s}, {d:%H:%M}" if with_time else s


def reltime(dt: datetime | None) -> str:
    if not dt:
        return "—"
    secs = (datetime.now(UTC) - dt).total_seconds()
    if secs < 3600:
        return f"{max(1, int(secs // 60))} mnt"
    if secs < 86400:
        return f"{int(secs // 3600)} jam"
    if secs < 86400 * 30:
        return f"{int(secs // 86400)} hr"
    d = dt.astimezone(WIB)
    return f"{d.day} {BULAN[d.month - 1]}"


def score_class(score: int | None) -> str:
    if score is None:
        return "score-lo"
    return "score-hi" if score >= 75 else "score-mid" if score >= 50 else "score-lo"


_COMPANY_PREFIX = {"PT", "PT.", "CV", "CV.", "TBK", "TBK.", "PERSERO", "(PERSERO)"}


def initials(name: str | None) -> str:
    """'PT. Siaga Abdi Utama' -> 'SA'; 'Jose Elio Parhusip' -> 'JE'."""
    words = [w for w in (name or "").replace(",", " ").split() if w.upper() not in _COMPANY_PREFIX]
    letters = [w[0] for w in words if w[:1].isalnum()]
    return "".join(letters[:2]).upper() or "?"


def exp_text(lo: int | None, hi: int | None) -> str:
    """Syarat pengalaman seperti ditulis situs lowongan."""
    if lo is None and hi is None:
        return ""
    if hi is not None and lo is not None and hi != lo:
        return f"{lo}–{hi} tahun pengalaman"
    if (lo or 0) == 0:
        return "Tanpa pengalaman / fresh graduate"
    return f"Min. {lo} tahun pengalaman"


def skill_matches(skill: str, _cv_skills=None) -> bool:
    from app.services.targeting import matched_skills
    return bool(matched_skills(skill))


def number_id(n: int) -> str:
    return f"{n:,}".replace(",", ".")


def short_city(city: str | None) -> str:
    if not city:
        return "—"
    return (city.replace("Selatan", "Sel.").replace("Utara", "Ut.").replace("Barat", "Bar.")
            .replace("Timur", "Tim.").replace("Kota ", "").replace("Kabupaten ", "Kab. "))


templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))
templates.env.filters.update(
    rupiah_short=rupiah_short, rupiah_long=rupiah_long, date_id=date_id, reltime=reltime,
    score_class=score_class, number_id=number_id, short_city=short_city, initials=initials,
    exp_text=exp_text, skill_matches=skill_matches,
)


def _duration(seconds: int) -> str:
    from app.services.scrape_progress import duration_text
    return duration_text(seconds)


templates.env.filters["duration"] = _duration
from app.services.letter_template import SOURCE_LABELS  # noqa: E402

templates.env.globals.update(
    SOURCE_LABELS=SOURCE_LABELS,
    ROLE_LABELS=ROLE_LABELS, MODE_LABELS=MODE_LABELS, LEVEL_LABELS=LEVEL_LABELS,
    EMPLOYMENT_LABELS=EMPLOYMENT_LABELS, STATUS_LABELS=STATUS_LABELS,
)
