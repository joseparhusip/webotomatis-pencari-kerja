"""Skor CV: baca file (teks PDF/Word atau OCR) → pemetaan oleh kode → nilai per kriteria oleh aturan tetap →
dibandingkan dengan lowongan di database → AI menyusun kesimpulan & prioritas perbaikan dari hasil itu.

Angka skor SELALU dihitung oleh kode (CV yang sama = skor yang sama). AI tidak boleh mengubah skor; jawabannya
dicek: hanya boleh membahas kriteria yang ada, hanya angka yang tertulis di CV/laporan, dan contoh "sebelum"
harus benar-benar kalimat dari CV.
"""

import asyncio
import io
import json
import logging
import re
from collections import Counter
from dataclasses import dataclass

from app.core.config import get_settings
from app.services import cv_scan
from app.services.cv_scan import CvScan, _Ctx, _str, experience_type

log = logging.getLogger(__name__)

MAX_BYTES = 5 * 1024 * 1024          # PDF & Word; CV 1–3 halaman umumnya < 1 MB
MIN_MARKET_JOBS = 20                 # di bawah ini perbandingan pasar tidak dinilai (datanya terlalu sedikit)
MARKET_SAMPLE = 1500                 # lowongan terbaru per bidang yang dibandingkan
TOP_DEMAND = 15

GROUPS = {
    "contact": "Kontak & identitas",
    "summary": "Ringkasan profil",
    "experience": "Pengalaman & proyek",
    "education": "Pendidikan",
    "skills": "Skill",
    "market": "Kecocokan dengan lowongan",
    "ats": "Keterbacaan sistem (ATS)",
}
GRADES = ((85, "Sangat baik"), (70, "Baik"), (55, "Cukup"), (0, "Perlu perbaikan"))
# Batas skor bila CV tidak nyambung dengan bidang yang dituju: (porsi lowongan yang menyebut skill CV, skor maksimal).
FIELD_CAPS = ((0.10, 54), (0.30, 69))
DECISIONS = {
    "siap": "Siap dipakai melamar",
    "perbaiki_sedikit": "Layak dipakai, perbaiki sedikit",
    "perbaiki_dulu": "Perbaiki dulu sebelum melamar",
}


class CvScoreError(Exception):
    pass


# ---------- membaca file ----------

def file_kind(data: bytes, filename: str) -> str:
    from app.services.docx_text import is_docx

    if data.startswith(b"%PDF"):
        return "pdf"
    if is_docx(data):
        return "docx"
    if data.startswith(b"\xd0\xcf\x11\xe0"):
        raise CvScoreError("Format Word lama (.doc) belum didukung. Buka di Word lalu Simpan Sebagai .docx atau PDF.")
    raise CvScoreError("File harus PDF atau Word (.docx).")


def check_upload(data: bytes, filename: str) -> str:
    if not data:
        raise CvScoreError("File kosong.")
    if len(data) > MAX_BYTES:
        raise CvScoreError(f"File terlalu besar ({len(data) / 1024 / 1024:.1f} MB). Maksimal 5 MB — "
                           "kompres PDF-nya atau kecilkan gambar di dalamnya.")
    return file_kind(data, filename)


def _pdf_text_layer(data: bytes) -> str:
    from pypdf import PdfReader

    try:
        reader = PdfReader(io.BytesIO(data))
        return "\n".join((p.extract_text() or "") for p in reader.pages[:10]).strip()
    except Exception:
        return ""


async def read_document(data: bytes, kind: str, on_stage=None) -> CvScan:
    """Teks + pemetaan oleh kode (tanpa AI). PDF memakai jalur yang sama dengan Profil."""
    async def stage(key: str) -> None:
        if on_stage:
            await on_stage(key)

    if kind == "pdf":
        return await cv_scan.read_cv(data, await asyncio.to_thread(_pdf_text_layer, data), on_stage=on_stage)

    from app.services.docx_text import DocxError, docx_ocr, docx_text

    await stage("text")
    try:
        text, links = await asyncio.to_thread(docx_text, data)
    except DocxError as exc:
        raise CvScoreError(str(exc)) from exc
    method = "text"
    if len(text) < cv_scan.MIN_TEXT_CHARS:
        await stage("ocr")
        ocr = await asyncio.to_thread(docx_ocr, data)
        if len(ocr) > len(text):
            text, method = ocr, "ocr"
    await stage("map")
    scan = cv_scan.pattern_scan(text, links)
    scan.method, scan.links = method, links
    if not text:
        scan.note = "Dokumen Word tidak berisi teks yang bisa dibaca."
    return scan


# ---------- penilaian oleh aturan ----------

@dataclass
class Criterion:
    key: str
    group: str
    label: str
    max: int
    points: float = 0
    status: str = "miss"        # ok | partial | miss | na (tidak bisa dinilai)
    detail: str = ""            # fakta dari CV / data lowongan
    fix: str = ""               # saran perbaikan (dipakai bila AI tidak ada)

    def grade(self, ratio: float, detail: str, fix: str = "") -> "Criterion":
        ratio = max(0.0, min(1.0, ratio))
        self.points = round(self.max * ratio, 1)
        self.status = "ok" if ratio >= 0.999 else "partial" if ratio > 0 else "miss"
        self.detail = detail
        self.fix = "" if self.status == "ok" else fix
        return self

    def skip(self, detail: str) -> "Criterion":
        self.status, self.points, self.detail = "na", 0, detail
        return self


_YEAR = re.compile(r"\b(19|20)\d{2}\b")
_METRIC = re.compile(r"\d")
_VERB_EN = re.compile(
    r"^(led|built|developed|designed|created|managed|implemented|improved|increased|reduced|launched|delivered|"
    r"analy[sz]ed|coordinated|handled|maintained|optimi[sz]ed|automated|integrated|collaborated|conducted|prepared|"
    r"organi[sz]ed|supported|assisted|executed|produced|wrote|achieved|served|processed|verified|trained|planned|"
    r"monitored|established|drove|ran|made|taught|sold|negotiated|[a-z]{3,}ed)\b", re.I)
_VERB_ID = re.compile(r"^(me(?:m|n|ng|ny|r)?[a-z]{3,}|ber[a-z]{3,}|di[a-z]{4,}|ter[a-z]{3,})\b", re.I)


def _bullets(scan: CvScan) -> list[str]:
    out = [h for e in scan.experiences for h in (e.get("highlights") or []) if h]
    out += [p.get("description") for p in scan.projects if p.get("description")]
    return [re.sub(r"^•\s*", "", b).strip() for b in out if len(b.strip()) >= 15]


def _quantified(b: str) -> bool:
    return bool(_METRIC.search(_YEAR.sub("", b)))


def _action(b: str) -> bool:
    first = re.sub(r"^[^A-Za-z]+", "", b)
    return bool(_VERB_EN.match(first) or _VERB_ID.match(first))


def _words(text: str) -> int:
    return len(re.findall(r"[A-Za-zÀ-ÿ0-9]+", text or ""))


def _names(items: list[str], limit: int = 3) -> str:
    items = [i for i in items if i]
    more = len(items) - limit
    return ", ".join(f"\"{i}\"" for i in items[:limit]) + (f" dan {more} lainnya" if more > 0 else "")


def _hard_skills(skills: list[str]) -> list[str]:
    from app.services.targeting import _SOFT_SKILL

    return [s for s in skills if s and not _SOFT_SKILL.match(s.strip())]


def score_contact(scan: CvScan) -> list[Criterion]:
    f = scan.fields
    name = Criterion("contact_name", "contact", "Nama lengkap", 2)
    name.grade(1 if f.get("full_name") else 0, f"Terbaca: {f['full_name']}" if f.get("full_name") else
               "Nama tidak terbaca di bagian atas CV.", "Tulis nama lengkap di baris paling atas CV sebagai teks (bukan gambar).")
    email = Criterion("contact_email", "contact", "Email", 3)
    email.grade(1 if f.get("email") else 0, "Email tercantum." if f.get("email") else "Tidak ada alamat email.",
                "Cantumkan email profesional (mis. nama.belakang@…) di bagian kontak.")
    phone = Criterion("contact_phone", "contact", "Nomor HP / WhatsApp", 3)
    phone.grade(1 if f.get("phone") else 0, "Nomor HP tercantum." if f.get("phone") else "Tidak ada nomor HP.",
                "Cantumkan nomor HP/WhatsApp aktif; HR di Indonesia sering menghubungi lewat WhatsApp.")
    link = Criterion("contact_link", "contact", "LinkedIn / portofolio", 2)
    has = [k for k, v in (("LinkedIn", f.get("linkedin_url")), ("portofolio", f.get("portfolio_url"))) if v]
    link.grade(1 if has else 0, f"Ada: {', '.join(has)}." if has else "Tidak ada tautan LinkedIn atau portofolio.",
               "Tambahkan tautan LinkedIn (dan portofolio/GitHub bila ada) supaya HR bisa melihat bukti kerja.")
    return [name, email, phone, link]


def score_summary(scan: CvScan, skills: list[str]) -> list[Criterion]:
    from app.services.targeting import matched_skills

    text = scan.fields.get("summary") or ""
    n = _words(text)
    present = Criterion("summary_present", "summary", "Ada ringkasan profil", 5)
    present.grade(1 if text else 0, f"Ringkasan {n} kata." if text else "Tidak ada bagian ringkasan/profil.",
                  "Tambahkan 2–4 kalimat ringkasan di bawah nama: posisi yang dituju, pengalaman utama, dan skill kunci.")
    length = Criterion("summary_length", "summary", "Panjang ringkasan pas (25–120 kata)", 3)
    if not text:
        length.grade(0, "Tidak ada ringkasan.", "Buat ringkasan 25–120 kata.")
    elif 25 <= n <= 120:
        length.grade(1, f"{n} kata.")
    else:
        length.grade(0.5, f"{n} kata — {'terlalu pendek' if n < 25 else 'terlalu panjang'}.",
                     "Ringkasan ideal 25–120 kata (2–4 kalimat) supaya terbaca cepat oleh HR.")
    focus = Criterion("summary_focus", "summary", "Ringkasan menyebut skill dari CV", 2)
    found = matched_skills(text, skills=skills) if text and skills else []
    focus.grade(1 if found else 0, f"Menyebut: {', '.join(found[:5])}." if found else
                "Ringkasan tidak menyebut skill yang tercantum di CV." if text else "Tidak ada ringkasan.",
                "Sebut 2–3 skill utama dari daftar skill-mu di ringkasan.")
    return [present, length, focus]


def score_experience(scan: CvScan) -> list[Criterion]:
    work = [e for e in scan.experiences if experience_type(e)]
    dated_projects = [p for p in scan.projects if p.get("start")]
    entries = work + scan.projects
    out = []

    present = Criterion("exp_present", "experience", "Ada pengalaman kerja / magang / proyek", 8)
    count = len(work) + len(dated_projects)
    present.grade(min(1, count / 2),
                  f"{len(work)} pengalaman kerja/magang dan {len(scan.projects)} proyek terbaca." if entries else
                  "Tidak ada bagian pengalaman atau proyek yang terbaca.",
                  "Tambahkan pengalaman kerja, magang, freelance, atau proyek (kampus/pribadi) beserta tanggalnya.")
    out.append(present)

    dates = Criterion("exp_dates", "experience", "Setiap pengalaman punya tanggal mulai & selesai", 5)
    if entries:
        missing = [e.get("company") or e.get("name") or e.get("role") for e in entries if not (e.get("start") and e.get("end"))]
        dates.grade(1 - len(missing) / len(entries), f"{len(entries) - len(missing)} dari {len(entries)} entri lengkap." +
                    (f" Tanpa tanggal: {_names(missing)}." if missing else ""),
                    "Tulis rentang waktu di setiap entri (mis. Jan 2024 – Jun 2024) supaya lama pengalaman bisa dihitung.")
    else:
        dates.grade(0, "Tidak ada entri pengalaman.", "Tambahkan pengalaman beserta rentang tanggalnya.")
    out.append(dates)

    roles = Criterion("exp_roles", "experience", "Posisi & tempat kerja jelas", 4)
    if work:
        missing = [e.get("company") or e.get("role") for e in work if not (e.get("role") and e.get("company"))]
        roles.grade(1 - len(missing) / len(work), f"{len(work) - len(missing)} dari {len(work)} pengalaman kerja "
                    "menulis posisi dan nama tempat kerja." + (f" Kurang lengkap: {_names(missing)}." if missing else ""),
                    "Tulis posisi dan nama perusahaan di setiap pengalaman (mis. \"Admin Gudang, PT …\").")
    elif scan.projects:
        roles.grade(0.5, "Belum ada pengalaman kerja/magang; hanya proyek.",
                    "Bila pernah magang/freelance/kerja paruh waktu, cantumkan sebagai pengalaman tersendiri.")
    else:
        roles.grade(0, "Tidak ada pengalaman kerja.", "Tambahkan pengalaman kerja/magang beserta posisinya.")
    out.append(roles)

    bullets = _bullets(scan)
    detail = Criterion("exp_bullets", "experience", "Tugas/pencapaian ditulis per poin", 4)
    if work:
        thin = [e.get("company") or e.get("role") for e in work if len(e.get("highlights") or []) < 2]
        detail.grade(1 - len(thin) / len(work), f"{len(work) - len(thin)} dari {len(work)} pengalaman punya ≥2 poin." +
                     (f" Poinnya kurang: {_names(thin)}." if thin else ""),
                     "Tulis 2–5 poin per pengalaman: apa yang dikerjakan, alat yang dipakai, dan hasilnya.")
    else:
        detail.grade(min(1, len(bullets) / 3) * 0.5, f"{len(bullets)} poin uraian terbaca.",
                     "Uraikan tiap pengalaman/proyek dalam 2–5 poin.")
    out.append(detail)

    metrics = Criterion("exp_metrics", "experience", "Pencapaian memakai angka", 6)
    if bullets:
        q = [b for b in bullets if _quantified(b)]
        metrics.grade(len(q) / len(bullets) / 0.3, f"{len(q)} dari {len(bullets)} poin memakai angka "
                      "(jumlah, %, target, durasi). Target: ≥30% poin.",
                      "Tambahkan angka nyata pada poin pencapaian (mis. jumlah pelanggan, % kenaikan, jumlah laporan per "
                      "bulan). Pakai angka yang benar — jangan dikarang.")
    else:
        metrics.grade(0, "Tidak ada poin uraian untuk dinilai.", "Tulis poin pencapaian dengan angka nyata.")
    out.append(metrics)

    verbs = Criterion("exp_action", "experience", "Poin diawali kata kerja", 3)
    if bullets:
        a = [b for b in bullets if _action(b)]
        verbs.grade(len(a) / len(bullets) / 0.6, f"{len(a)} dari {len(bullets)} poin diawali kata kerja "
                    "(mis. Membuat, Mengelola, Built, Managed). Target: ≥60%.",
                    "Awali tiap poin dengan kata kerja aktif: Membuat, Mengelola, Menyusun, Meningkatkan, …")
    else:
        verbs.grade(0, "Tidak ada poin uraian untuk dinilai.", "Awali poin dengan kata kerja aktif.")
    out.append(verbs)
    return out


def score_education(scan: CvScan) -> list[Criterion]:
    edu = scan.education
    present = Criterion("edu_present", "education", "Ada riwayat pendidikan", 6)
    present.grade(1 if edu else 0, f"{len(edu)} entri: {_names([e.get('institution') for e in edu])}." if edu else
                  "Bagian pendidikan tidak terbaca.", "Tambahkan bagian Pendidikan: institusi, jurusan, dan tahun.")
    detail = Criterion("edu_detail", "education", "Jurusan/jenjang & tahun tercantum", 4)
    if edu:
        full = [e for e in edu if (e.get("degree") or e.get("major")) and (e.get("end") or e.get("start"))]
        detail.grade(len(full) / len(edu), f"{len(full)} dari {len(edu)} entri punya jurusan/jenjang dan tahun.",
                     "Lengkapi jenjang (S1/D3/SMK), jurusan, dan tahun lulus di setiap entri pendidikan.")
    else:
        detail.grade(0, "Tidak ada entri pendidikan.", "Tambahkan jenjang, jurusan, dan tahun lulus.")
    return [present, detail]


def score_skills(scan: CvScan, skills: list[str]) -> list[Criterion]:
    from app.services.targeting import _skill_patterns, matched_skills

    hard = _hard_skills(skills)
    count = Criterion("skills_count", "skills", "Jumlah skill cukup (≥8)", 6)
    count.grade(len(skills) / 8, f"{len(skills)} skill terbaca." if skills else "Tidak ada daftar skill.",
                "Buat bagian Skill berisi 8–15 skill yang relevan dengan posisi yang dituju.")
    tech = Criterion("skills_hard", "skills", "Skill teknis / alat kerja (≥5)", 4)
    tech.grade(len(hard) / 5, f"{len(hard)} skill teknis: {', '.join(hard[:6])}{'…' if len(hard) > 6 else ''}." if hard else
               "Hanya soft skill (komunikasi, kerja sama, …) atau tidak ada skill.",
               "Tambahkan skill teknis/alat kerja yang benar-benar dikuasai (software, bahasa pemrograman, mesin, sertifikasi).")
    proven = Criterion("skills_proven", "skills", "Skill terbukti dipakai di pengalaman/proyek", 5)
    names = list(_skill_patterns(hard).keys())
    if names:
        body = " ".join(_bullets(scan) + [e.get("role", "") for e in scan.experiences]
                        + [" ".join(p.get("tech") or []) + " " + (p.get("name") or "") for p in scan.projects])
        used = matched_skills(body, skills=hard)
        unused = [n for n in names if n not in used]
        proven.grade(len(used) / len(names) / 0.5, f"{len(used)} dari {len(names)} skill teknis disebut di uraian "
                     f"pengalaman/proyek. Target: ≥50%." + (f" Belum terbukti: {', '.join(unused[:6])}." if unused else ""),
                     "Sebut skill yang dipakai di poin pengalaman/proyek (mis. \"Membuat laporan stok harian dengan Excel\").")
    else:
        proven.grade(0, "Tidak ada skill teknis untuk dicek.", "Tambahkan skill teknis dan tunjukkan pemakaiannya di pengalaman.")
    return [count, tech, proven]


def score_ats(scan: CvScan, kind: str) -> list[Criterion]:
    from app.services.cv_text import split_sections

    text_c = Criterion("ats_text", "ats", "Teks bisa dibaca mesin (bukan gambar)", 4)
    if scan.method == "ocr":
        text_c.grade(0, "CV berupa gambar/hasil scan — teksnya hanya bisa dibaca dengan OCR. Banyak sistem rekrutmen "
                     "(ATS) tidak melakukan OCR.", "Ekspor CV langsung dari Word/Google Docs/Canva ke PDF (bukan foto/scan).")
    elif scan.text:
        text_c.grade(1, f"Teks langsung dari {'PDF' if kind == 'pdf' else 'Word'}.")
    else:
        text_c.grade(0, "Teks tidak terbaca sama sekali.", "Unggah CV yang berisi teks.")
    n = _words(scan.text)
    length = Criterion("ats_length", "ats", "Panjang CV wajar (250–1.100 kata)", 3)
    if 250 <= n <= 1100:
        length.grade(1, f"{n} kata (±{max(1, round(n / 450))} halaman).")
    else:
        length.grade(0.5 if n >= 120 else 0, f"{n} kata — {'terlalu singkat' if n < 250 else 'terlalu panjang'}.",
                     "Isi CV dengan pengalaman & proyek yang relevan." if n < 250 else
                     "Ringkas CV jadi 1–2 halaman: buang pengalaman yang tidak relevan dan kalimat panjang.")
    found = split_sections(scan.text) if scan.text else {}
    want = {"experience": "Pengalaman", "education": "Pendidikan", "skills": "Skill"}
    have = [label for key, label in want.items() if found.get(key)]
    missing = [label for key, label in want.items() if not found.get(key)]
    heads = Criterion("ats_sections", "ats", "Judul bagian standar", 3)
    heads.grade(len(have) / len(want), f"Ditemukan: {', '.join(have) or '—'}." + (f" Tidak ditemukan: {', '.join(missing)}." if missing else ""),
                "Pakai judul bagian yang umum: \"Pengalaman Kerja\" / \"Experience\", \"Pendidikan\", \"Skill\".")
    return [text_c, length, heads]


def score_market(market: dict | None) -> list[Criterion]:
    rel = Criterion("market_relevance", "market", "Skill CV dicari lowongan bidangmu", 7)
    cov = Criterion("market_coverage", "market", "Menguasai skill yang paling banyak dicari", 8)
    if not market or market.get("jobs", 0) < MIN_MARKET_JOBS:
        why = (f"Baru {market.get('jobs', 0)} lowongan di bidang ini di database (minimal {MIN_MARKET_JOBS})."
               if market and market.get("categories") else "Bidang CV belum bisa ditentukan dari isi CV.")
        return [rel.skip(why + " Jalankan scraping dulu agar bisa dibandingkan."), cov.skip(why)]
    n, hit = market["jobs"], market["jobs_with_cv_skill"]
    rel.grade(hit / n / 0.6, f"{hit} dari {n} lowongan {market['label']} menyebut minimal satu skill di CV-mu. Target: ≥60%.",
              "Tambahkan skill yang dicari lowongan (lihat daftar \"Belum ada di CV\") — hanya yang benar-benar dikuasai.")
    top = market["top"]
    total = sum(t["count"] for t in top) or 1
    owned = sum(t["count"] for t in top if t["owned"])
    missing = [t["name"] for t in top if not t["owned"]]
    cov.grade(owned / total / 0.5, f"Dari {len(top)} skill paling dicari, {sum(t['owned'] for t in top)} ada di CV." +
              (f" Belum ada: {', '.join(missing[:5])}." if missing else ""),
              "Pelajari/cantumkan skill yang paling banyak diminta di bidangmu bila memang sudah dikuasai; bila belum, "
              "jadikan target belajar.")
    return [rel, cov]


def build_report(scan: CvScan, kind: str, market: dict | None) -> dict:
    skills = list(scan.fields.get("skills") or [])
    criteria = (score_contact(scan) + score_summary(scan, skills) + score_experience(scan) + score_education(scan)
                + score_skills(scan, skills) + score_market(market) + score_ats(scan, kind))
    rated = [c for c in criteria if c.status != "na"]
    got, full = sum(c.points for c in rated), sum(c.max for c in rated)
    score = round(got / full * 100) if full else 0
    raw_score, cap, cap_reason = score, None, ""
    if market and market.get("jobs", 0) >= MIN_MARKET_JOBS:
        rel = market["jobs_with_cv_skill"] / market["jobs"]
        # CV yang rapi tetapi tidak nyambung dengan bidang yang dituju tidak boleh tampak "siap".
        cap = next((limit for share, limit in FIELD_CAPS if rel < share), None)
        if cap is not None and score > cap:
            score = cap
            cap_reason = (f"hanya {round(rel * 100)}% dari {market['jobs']} lowongan {market['label']} menyebut skill "
                          "yang ada di CV-mu, jadi CV ini belum menunjukkan kecocokan dengan bidang yang dituju")
        else:
            cap = None
    groups = []
    for key, label in GROUPS.items():
        items = [c for c in criteria if c.group == key]
        rated_g = [c for c in items if c.status != "na"]
        g_max = sum(c.max for c in rated_g)
        g_pts = round(sum(c.points for c in rated_g), 1)
        groups.append({"key": key, "label": label, "points": g_pts, "max": g_max,
                       "pct": round(g_pts / g_max * 100) if g_max else None, "na": not rated_g})
    years = cv_scan.total_experience(scan.experiences, scan.projects)
    return {
        "score": score,
        "raw_score": raw_score,
        "cap": cap,
        "cap_reason": cap_reason,
        "grade": next(label for limit, label in GRADES if score >= limit),
        "assessed_max": full,
        "groups": groups,
        "criteria": [c.__dict__ for c in criteria],
        "market": market,
        "facts": {
            "name": scan.fields.get("full_name") or "",
            "words": _words(scan.text),
            "skills": skills,
            "experience_months": years["months"],
            "experience_text": cv_scan.months_text(years["months"]) if years["months"] else "",
            "experiences": len(scan.experiences), "projects": len(scan.projects), "education": len(scan.education),
            "method": scan.method, "kind": kind,
        },
    }


# ---------- perbandingan dengan lowongan di database ----------

def clean_fields(values) -> list[str]:
    """Bidang pilihan pengguna → hanya nilai RoleCategory yang sah (tanpa 'other'), urutan tetap, tanpa dobel."""
    from app.models.enums import RoleCategory

    out = []
    for v in values or []:
        v = str(v).strip()
        if v in RoleCategory._value2member_map_ and v != RoleCategory.OTHER.value and v not in out:
            out.append(v)
    return out


async def market_snapshot(session, scan: CvScan, chosen: list[str] | None = None) -> dict:
    """Skill yang dicari lowongan di bidang yang dituju (dari database hasil scraping, bukan tebakan).
    chosen = bidang yang dipilih pengguna sebelum menilai; kosong → ditebak dari isi CV."""
    from datetime import UTC, datetime

    from sqlalchemy import or_, select

    from app.models import Job
    from app.models.enums import JobStatus, RoleCategory
    from app.services.cv_targets import derive_targets
    from app.web.templating import templates

    detected = [c for c in derive_targets(scan).categories if c in RoleCategory._value2member_map_][:4]
    chosen = clean_fields(chosen)
    cats = chosen or detected
    labels = templates.env.globals.get("ROLE_LABELS", {})
    out = {"categories": cats, "label": " / ".join(labels.get(c, c) for c in cats), "jobs": 0,
           "jobs_with_cv_skill": 0, "top": [], "cv_skill_demand": [],
           "mode": "manual" if chosen else "auto", "detected": detected,
           "detected_label": " / ".join(labels.get(c, c) for c in detected)}
    if not cats:
        return out
    now = datetime.now(UTC)
    rows = (await session.execute(
        select(Job.title, Job.description, Job.requirements, Job.required_skills)
        .where(Job.role_category.in_([RoleCategory(c) for c in cats]), Job.status != JobStatus.HIDDEN,
               or_(Job.expires_at.is_(None), Job.expires_at >= now))
        .order_by(Job.posted_at.desc().nulls_last()).limit(MARKET_SAMPLE)
    )).all()
    skills = list(scan.fields.get("skills") or [])
    out.update(await asyncio.to_thread(_market_counts, [tuple(r) for r in rows], skills))
    return out


def _market_counts(rows: list[tuple], cv_skills: list[str]) -> dict:
    from app.services.letter_template import skill_key
    from app.services.targeting import _canonical, _clean_skill, _skill_patterns, known_skills
    from app.web.jobs_export import _SOFT_REQ, _owned_checker

    def key_of(name: str) -> str:
        return skill_key(re.sub(r"^(microsoft|ms)\s+", "", name.strip(), flags=re.I))

    def listed(required) -> list[str]:
        return [(_canonical(s) or _clean_skill(s)) for s in (required or [])
                if s and len(s) <= 30 and not _SOFT_REQ.search(s)]

    # Kosakata skill dipelajari dari data: skill yang dicantumkan situs sumber (kolom "skill") di ≥2 lowongan,
    # lalu dicari juga di deskripsi lowongan lain — bukan hanya kamus bawaan.
    vocab_count, vocab_name = Counter(), {}
    for *_, required in rows:
        for name in set(listed(required)):
            k = key_of(name)
            vocab_count[k] += 1
            vocab_name.setdefault(k, name)
    vocab = {k: re.compile(rf"(?<![\w.]){re.escape(vocab_name[k])}(?![\w])", re.I)
             for k, n in vocab_count.most_common(120) if n >= 2 and len(vocab_name[k]) >= 2}

    patterns = _skill_patterns(_hard_skills(cv_skills))
    per_skill = Counter()
    with_skill = 0
    demand = Counter()
    display: dict[str, Counter] = {}
    for title, desc, req, required in rows:
        text = " ".join(x for x in (title, desc, req) if x)
        hits = {name for name, pats in patterns.items() if any(p.search(text) for p in pats)}
        per_skill.update(hits)
        with_skill += bool(hits)
        seen: set[str] = set()
        found = listed(required) + known_skills(text) + [vocab_name[k] for k, p in vocab.items() if p.search(text)]
        for name in found:
            key = key_of(name)
            if key and key not in seen:
                seen.add(key)
                demand[key] += 1
                display.setdefault(key, Counter())[name] += 1
    is_owned = _owned_checker(cv_skills)
    top = []
    for key, count in demand.most_common(TOP_DEMAND * 2):
        if count < 2:
            break
        name = display[key].most_common(1)[0][0]
        top.append({"name": name, "count": count, "pct": round(count / len(rows) * 100) if rows else 0,
                    "owned": bool(is_owned(name))})
        if len(top) >= TOP_DEMAND:
            break
    return {
        "jobs": len(rows),
        "jobs_with_cv_skill": with_skill,
        "top": top,
        "cv_skill_demand": [{"name": n, "count": per_skill.get(n, 0),
                             "pct": round(per_skill.get(n, 0) / len(rows) * 100) if rows else 0}
                            for n in sorted(patterns, key=lambda n: -per_skill.get(n, 0))],
    }


# ---------- kesimpulan ----------

def allowed_decisions(score: int) -> list[str]:
    if score >= 80:
        return ["siap", "perbaiki_sedikit"]
    if score >= 55:
        return ["perbaiki_sedikit", "perbaiki_dulu"]
    return ["perbaiki_dulu"]


def _lost(c: dict) -> float:
    return c["max"] - c["points"] if c["status"] != "na" else 0


def basic_review(report: dict) -> dict:
    """Kesimpulan tanpa AI — disusun langsung dari hasil penilaian (juga dipakai bila jawaban AI tidak lolos cek)."""
    crit = report["criteria"]
    groups = [g for g in report["groups"] if not g["na"]]
    best = max(groups, key=lambda g: g["pct"] or 0) if groups else None
    worst = min(groups, key=lambda g: g["pct"] if g["pct"] is not None else 100) if groups else None
    decision = allowed_decisions(report["score"])[0 if report["score"] >= 80 else -1]
    verdict = f"Skor CV {report['score']}/100 ({report['grade']})."
    if best and worst and best is not worst:
        verdict += f" Bagian terkuat: {best['label'].lower()} ({best['pct']}%); paling perlu diperbaiki: " \
                   f"{worst['label'].lower()} ({worst['pct']}%)."
    strengths = [{"criterion": c["key"], "text": f"{c['label']}: {c['detail']}"}
                 for c in sorted((c for c in crit if c["status"] == "ok"), key=lambda c: -c["max"])[:4]]
    priorities = [{"criterion": c["key"], "text": c["fix"], "before": "", "after": ""}
                  for c in sorted((c for c in crit if c["status"] in ("partial", "miss")), key=_lost, reverse=True)[:5]
                  if c["fix"]]
    return {"verdict": verdict, "decision": decision, "strengths": strengths, "priorities": priorities, "ai": ""}


_PROMPT = """Kamu reviewer CV untuk pencari kerja di Indonesia. Sistem SUDAH membaca CV (teks PDF/Word atau OCR),
memetakannya, dan menilai tiap kriteria dengan aturan tetap. Tugasmu: menyimpulkan dan memprioritaskan perbaikan.

ATURAN KERAS:
- Skor sudah final ({score}/100, {grade}). Jangan menghitung ulang, jangan menyebut skor lain.
- Bahas hanya kriteria di LAPORAN, pakai "key"-nya persis. Kekuatan hanya dari kriteria berstatus "ok";
  prioritas perbaikan hanya dari kriteria berstatus "partial" atau "miss". Urutkan prioritas dari yang paling berdampak.
- Jangan mengarang fakta. Setiap angka yang kamu tulis harus ada di TEKS CV atau LAPORAN. Untuk contoh kalimat
  perbaikan yang butuh angka yang tidak ada di CV, tulis penanda seperti [jumlah] atau [%] — jangan isi angka sendiri.
- "before"/"after" HANYA untuk kriteria {rewrite}; selain itu kosongkan keduanya.
  "before" = salinan PERSIS satu poin/kalimat dari TEKS CV. "after" = kalimat yang sama, dirapikan, dengan penanda
  [jumlah]/[%]/[hasil] di tempat yang perlu diisi pemilik CV. Jangan menambah aktivitas, hasil, skill, tautan,
  atau email yang tidak tertulis di CV.
- decision pilih salah satu dari: {decisions}.
- Bahasa Indonesia yang wajar dan langsung ke inti. verdict 2–4 kalimat. Maksimal 4 kekuatan dan 5 prioritas."""

_S = {"type": "string"}
_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": _S,
        "decision": _S,
        "strengths": {"type": "array", "items": {"type": "object", "properties": {"criterion": _S, "text": _S},
                                                  "required": ["criterion", "text"]}},
        "priorities": {"type": "array", "items": {"type": "object", "properties": {
            "criterion": _S, "text": _S, "before": _S, "after": _S}, "required": ["criterion", "text"]}},
    },
    "required": ["verdict", "decision", "strengths", "priorities"],
}


def _report_for_ai(report: dict) -> dict:
    m = report.get("market") or {}
    return {
        "score": report["score"], "grade": report["grade"],
        "skor_sebelum_dibatasi": report.get("raw_score", report["score"]),
        "alasan_skor_dibatasi": report.get("cap_reason") or "",
        "criteria": [{k: c[k] for k in ("key", "label", "status", "points", "max", "detail")} for c in report["criteria"]],
        "market": {"bidang": m.get("label"), "bidang_dipilih_pengguna": m.get("mode") == "manual",
                   "bidang_dari_isi_cv": m.get("detected_label"), "lowongan_dibandingkan": m.get("jobs"),
                   "skill_paling_dicari": [{k: t[k] for k in ("name", "count", "owned")} for t in m.get("top") or []]},
    }


# Contoh "sebelum → sesudah" hanya untuk kriteria soal CARA MENULIS. Kriteria seperti kontak, jumlah skill, atau
# skill yang dicari pasar hanya bisa dipenuhi dengan menambah data baru — contohnya pasti karangan.
REWRITE_CRITERIA = {"exp_metrics", "exp_action", "exp_bullets", "summary_length", "summary_focus", "skills_proven"}
MAX_NEW_WORDS = 3
_PLACEHOLDER = re.compile(r"\[[^\]]{1,30}\]")
_SKILL_PH = re.compile(r"skill|keahlian|kemampuan|tools?|alat|software|aplikasi|sertifika|bahasa|teknologi", re.I)
_CONTACT_BITS = re.compile(r"https?://\S+|www\.\S+|[\w.+-]+@[\w-]+\.[\w.]+|[a-z0-9-]+\.(?:com|id|io|me|net|org|app)\b\S*", re.I)
_STOP = {"yang", "dan", "untuk", "dengan", "dari", "pada", "dalam", "secara", "serta", "atau", "sebesar", "hingga",
         "sehingga", "agar", "per", "setiap", "the", "and", "with", "for", "into", "from", "that", "this", "by", "of"}


def _rewrite_ok(after: str, text: str, ctx: _Ctx, cv_ctx: _Ctx, skill_names: list[str], before: str = "") -> bool:
    """'Sesudah' boleh merapikan & memberi penanda [jumlah], tetapi tidak menambah fakta baru."""
    from app.services.targeting import known_skills

    if not after or not ctx.numbers_ok(after):
        return False
    if any(re.search(r"\d", ph) or _SKILL_PH.search(ph) for ph in _PLACEHOLDER.findall(after)):
        return False                                     # penanda hanya untuk angka/hasil, bukan angka tebakan / skill baru
    if before and any(n not in after for n in re.findall(r"\d+", before)):
        return False                                     # tanggal/angka asli tidak boleh hilang
    low_cv = text.lower()
    if any(bit.lower().rstrip(".,") not in low_cv for bit in _CONTACT_BITS.findall(after)):
        return False                                     # tautan/email/situs karangan
    clean = _PLACEHOLDER.sub(" ", after)
    for name in set(known_skills(clean)) | {n for n in skill_names if re.search(rf"(?<!\w){re.escape(n)}(?!\w)", clean, re.I)}:
        if not cv_scan._skill_in_text(name, low_cv, cv_ctx.squashed, cv_ctx.words):
            return False                                 # skill yang tidak ada di CV
    cv_words = {w.lower() for w in re.findall(r"[A-Za-zÀ-ÿ]{3,}", text)}
    new = {w.lower() for w in re.findall(r"[A-Za-zÀ-ÿ]{4,}", clean)} - cv_words - _STOP
    return len(new) <= MAX_NEW_WORDS                     # tidak menambah aktivitas/hasil baru


def facts_text(report: dict) -> str:
    """Angka yang boleh disebut AI selain isi CV: skor & fakta tiap kriteria — BUKAN bobot poin (2, 3, 5, 8 …)."""
    m = report.get("market") or {}
    parts = [str(report.get("score", "")), str(report.get("raw_score", "")), "100"]  # "85/100"
    parts += [c.get("detail") or "" for c in report.get("criteria") or []]
    parts += [f"{t['name']} {t['count']}" for t in m.get("top") or []] + [str(m.get("jobs", ""))]
    return "\n".join(parts)


def check_ai_review(data: dict, report: dict, text: str) -> dict:
    """Saring jawaban AI terhadap laporan & teks CV. Bagian yang gagal diganti versi tanpa AI."""
    base = basic_review(report)
    ctx = _Ctx(text + "\n" + facts_text(report))
    cv_ctx = _Ctx(text)
    status = {c["key"]: c["status"] for c in report["criteria"]}
    skill_names = [t["name"] for t in (report.get("market") or {}).get("top") or []]
    ok = lambda s: bool(s) and ctx.numbers_ok(s)  # noqa: E731

    verdict = _str(data.get("verdict"))
    if verdict and not ok(verdict):
        verdict = ""
    decision = _str(data.get("decision"))
    allowed = allowed_decisions(report["score"])
    if decision not in allowed or not verdict:
        decision = base["decision"]  # kalimat & label keputusan harus dari sumber yang sama

    strengths, seen = [], set()
    for item in data.get("strengths") or []:
        key, msg = _str((item or {}).get("criterion")), _str((item or {}).get("text"))
        if status.get(key) == "ok" and key not in seen and ok(msg):
            seen.add(key)
            strengths.append({"criterion": key, "text": msg})
    priorities, seen = [], set()
    for item in data.get("priorities") or []:
        item = item or {}
        key, msg = _str(item.get("criterion")), _str(item.get("text"))
        if status.get(key) not in ("partial", "miss") or key in seen or not ok(msg):
            continue
        before, after = _str(item.get("before")), _str(item.get("after"))
        if (key not in REWRITE_CRITERIA or not before or len(before) < 10 or not cv_ctx.mentions(before)
                or not _rewrite_ok(after, text, ctx, cv_ctx, skill_names, before)):
            before = after = ""               # contoh bukan dari CV / menambah fakta baru → dibuang
        seen.add(key)
        priorities.append({"criterion": key, "text": msg, "before": before, "after": after})
    # Kriteria gagal yang tidak dibahas AI tetap muncul (versi tanpa AI), supaya tidak ada yang terlewat.
    for p in base["priorities"]:
        if len(priorities) >= 5:
            break
        if p["criterion"] not in seen:
            priorities.append(p)
    return {
        "verdict": verdict or base["verdict"],
        "decision": decision,
        "strengths": strengths[:4] or base["strengths"],
        "priorities": priorities[:5],
    }


async def ai_review(report: dict, text: str) -> dict:
    s = get_settings()
    if not s.ai_ready:
        raise CvScoreError("AI belum aktif")
    if not text:
        raise CvScoreError("teks CV kosong")
    prompt = (_PROMPT.format(score=report["score"], grade=report["grade"], rewrite=", ".join(sorted(REWRITE_CRITERIA)),
                             decisions=", ".join(f'"{d}" ({DECISIONS[d]})' for d in allowed_decisions(report["score"])))
              + "\n\nLAPORAN:\n" + json.dumps(_report_for_ai(report), ensure_ascii=False)
              + "\n\nTEKS CV:\n<cv>\n" + text[:30000] + "\n</cv>")
    call = cv_scan._gemini(prompt, _SCHEMA) if s.ai_provider == "gemini" else cv_scan._claude(prompt, _SCHEMA)
    data = cv_scan._parse_json(await asyncio.wait_for(call, cv_scan.AI_TIMEOUT_SECONDS))
    out = check_ai_review(data, report, text)
    out["ai"] = s.ai_provider or ""
    return out


# ---------- penjelasan skor (bahasa sehari-hari, dihitung kode dari laporan — angkanya pasti cocok) ----------

GRADE_MEANING = {
    "Sangat baik": "CV-mu sudah kuat dan siap bersaing. Tinggal dipoles sedikit supaya makin menonjol.",
    "Baik": "CV-mu sudah layak dikirim, tapi masih ada beberapa bagian yang membuatnya kalah menonjol dari pelamar lain.",
    "Cukup": "CV-mu masih punya kekurangan yang cukup terlihat oleh HR. Perbaiki dulu bagian yang paling banyak "
             "kehilangan poin sebelum dipakai melamar.",
    "Perlu perbaikan": "Banyak informasi penting belum ada atau belum terbaca. CV seperti ini berisiko tersaring "
                       "sebelum sempat dibaca HR.",
}

# Arti kehilangan poin untuk tiap kriteria, dari sudut pandang HR / sistem rekrutmen.
WHY = {
    "contact_name": "Nama tidak terbaca di bagian atas, jadi HR dan sistem rekrutmen tidak tahu CV ini milik siapa.",
    "contact_email": "Tidak ada email, padahal email adalah jalur utama HR untuk membalas lamaran atau mengundang interview.",
    "contact_phone": "Tidak ada nomor HP/WhatsApp, jadi HR tidak bisa menghubungimu dengan cepat.",
    "contact_link": "Tidak ada LinkedIn atau portofolio, jadi HR tidak punya tempat untuk melihat bukti kerjamu.",
    "summary_present": "Tidak ada ringkasan di bagian atas, padahal HR biasanya hanya membaca beberapa detik pertama.",
    "summary_length": "Panjang ringkasan belum pas. Ringkasan 2–4 kalimat paling cepat ditangkap HR.",
    "summary_focus": "Ringkasan belum menyebut skill yang kamu punya, jadi kesan pertamanya kurang spesifik.",
    "exp_present": "Pengalaman atau proyek yang terbaca masih sedikit, jadi bukti kemampuanmu terbatas.",
    "exp_dates": "Ada pengalaman tanpa tanggal, jadi HR tidak tahu berapa lama kamu di sana.",
    "exp_roles": "Ada pengalaman yang posisi atau nama tempat kerjanya tidak jelas.",
    "exp_bullets": "Ada pengalaman yang uraiannya terlalu sedikit, jadi HR tidak tahu apa saja yang kamu kerjakan.",
    "exp_metrics": "Sebagian besar poin belum memakai angka, jadi hasil kerjamu sulit diukur dan dibandingkan.",
    "exp_action": "Banyak poin tidak diawali kata kerja, jadi kalimatnya terasa pasif dan kurang menunjukkan peranmu.",
    "edu_present": "Riwayat pendidikan tidak terbaca, padahal banyak lowongan mensyaratkan jenjang tertentu.",
    "edu_detail": "Jurusan, jenjang, atau tahun lulus belum lengkap, jadi syarat pendidikan sulit dicek.",
    "skills_count": "Daftar skill masih sedikit, jadi peluang cocok dengan kata kunci lowongan berkurang.",
    "skills_hard": "Skill teknis / alat kerja masih sedikit. Soft skill saja sulit dibuktikan dan jarang dicari sistem.",
    "skills_proven": "Banyak skill hanya ditulis di daftar, tapi tidak terlihat dipakai di pengalaman atau proyek.",
    "market_relevance": "Banyak lowongan di bidang yang dituju tidak menyebut skill yang ada di CV-mu.",
    "market_coverage": "Beberapa skill yang paling sering diminta lowongan di bidang ini belum ada di CV-mu.",
    "ats_text": "CV berupa gambar/hasil scan, jadi banyak sistem rekrutmen (ATS) tidak bisa membaca isinya.",
    "ats_length": "Panjang CV belum ideal — terlalu singkat terlihat kosong, terlalu panjang membuat HR melewatkan isinya.",
    "ats_sections": "Ada judul bagian yang tidak standar, jadi sistem rekrutmen bisa salah membaca isinya.",
}


def _num(x: float) -> str:
    """76.4 → '76,4'; 8.0 → '8'."""
    return f"{x:.1f}".rstrip("0").rstrip(".").replace(".", ",")


def _thousand(n: int) -> str:
    return f"{n:,}".replace(",", ".")


def explain(report: dict) -> dict:
    crit = report["criteria"]
    rated = [c for c in crit if c["status"] != "na"]
    got = sum(c["points"] for c in rated)
    full = sum(c["max"] for c in rated) or 1
    score = report["score"]
    losses = sorted(({"key": c["key"], "label": c["label"], "lost": round(c["max"] - c["points"], 1),
                      "why": WHY.get(c["key"], c["label"]), "detail": c["detail"], "group": c["group"]}
                     for c in rated if c["max"] - c["points"] > 0.05), key=lambda x: -x["lost"])
    lost_total = round(full - got, 1)
    raw = report.get("raw_score", score)
    math = f"Kamu mendapat {_num(got)} dari {full} poin yang bisa dinilai: {_num(got)} ÷ {full} × 100 = {raw}."
    if report.get("cap") is not None and raw > score:
        math += f" Lalu dibatasi menjadi {score} karena {report['cap_reason']}."
    if full < 100:
        na = [c["label"] for c in crit if c["status"] == "na"]
        math += (f" {100 - full} poin lainnya ({', '.join(na)}) tidak dinilai karena datanya belum cukup — "
                 "tidak dianggap nol.")

    top3 = losses[:3]
    potential = min(100, round((got + sum(x["lost"] for x in top3)) / full * 100)) if top3 else score
    if report.get("cap") is not None:
        potential = max(score, min(potential, report["cap"]))  # batas bidang hanya hilang bila CV menunjukkan bidangnya
    by_group: dict[str, float] = {}
    for x in losses:
        by_group[x["group"]] = by_group.get(x["group"], 0) + x["lost"]
    biggest = max(by_group.items(), key=lambda kv: kv[1]) if by_group else None

    strong = [g["label"] for g in report["groups"] if not g["na"] and g["pct"] == 100]
    capped = report.get("cap") is not None and raw > score
    if capped:  # CV-nya sendiri bisa rapi — yang kurang adalah kecocokan dengan bidang yang dituju
        story = [f"Skormu {score} dari 100 ({report['grade']}) untuk bidang {(report.get('market') or {}).get('label', '')}.",
                 f"Dari kelengkapan dan cara penulisan saja CV-mu bernilai {raw}, tetapi dibatasi menjadi {score} karena "
                 f"{report['cap_reason']}. CV yang rapi belum cukup bila isinya belum menjawab kebutuhan lowongan di bidang itu."]
    else:
        story = [f"Skormu {score} dari 100 ({report['grade']}). {GRADE_MEANING.get(report['grade'], '')}"]
    if biggest:
        story.append(f"Total poin yang hilang {_num(lost_total)}, paling banyak di bagian "
                     f"{GROUPS[biggest[0]].lower()} ({_num(biggest[1])} poin).")
    if strong:
        story.append(f"Bagian yang sudah sempurna: {', '.join(s.lower() for s in strong)}.")

    m = report.get("market") or {}
    market_text = ""
    if m.get("jobs", 0) >= MIN_MARKET_JOBS:
        pct = round(m["jobs_with_cv_skill"] / m["jobs"] * 100)
        have = [t for t in m.get("top") or [] if t["owned"]][:4]
        gaps = [t for t in m.get("top") or [] if not t["owned"]][:4]
        market_text = (f"CV-mu dibandingkan dengan {_thousand(m['jobs'])} lowongan aktif bidang {m['label']} di database. "
                       f"{_thousand(m['jobs_with_cv_skill'])} di antaranya ({pct}%) menyebut minimal satu skill yang kamu punya")
        market_text += (" — CV-mu nyambung dengan kebutuhan pasar di bidang ini." if pct >= 60 else
                        " — masih banyak lowongan di bidang ini yang meminta skill lain." if pct >= 30 else
                        " — isi CV-mu belum banyak cocok dengan lowongan di bidang ini.")
        if have:
            market_text += (" Skill paling dicari yang sudah kamu punya: "
                            + ", ".join(f"{t['name']} ({_thousand(t['count'])} lowongan)" for t in have) + ".")
        if gaps:
            market_text += (" Yang sering diminta tapi belum ada di CV: "
                            + ", ".join(f"{t['name']} ({_thousand(t['count'])} lowongan)" for t in gaps) + ".")
    elif m.get("categories"):
        market_text = (f"Lowongan bidang {m['label']} di database baru {m.get('jobs', 0)} (minimal {MIN_MARKET_JOBS}), "
                       "jadi kecocokan dengan pasar belum dinilai. Jalankan scraping lalu nilai ulang.")

    field_note, field_tone = "", ""
    if m.get("mode") == "manual":
        chosen, detected = set(m.get("categories") or []), set(m.get("detected") or [])
        if detected and not chosen & detected:
            field_tone = "warn"
            field_note = (f"Bidang yang kamu pilih ({m['label']}) berbeda dengan yang terlihat dari isi CV "
                          f"({m['detected_label']}). Wajar bila kecocokan dengan lowongan rendah: CV-mu belum menunjukkan "
                          "pengalaman atau skill di bidang yang dituju. Tambahkan pengalaman, proyek, atau skill yang "
                          "relevan — atau buat CV khusus untuk bidang tersebut.")
        elif detected and chosen != detected:
            field_note = (f"Dinilai untuk bidang pilihanmu: {m['label']}. Dari isi CV, bidang yang paling terlihat: "
                          f"{m['detected_label']}.")
        else:
            field_note = f"Dinilai untuk bidang pilihanmu: {m['label']} — sesuai dengan isi CV."
    elif m.get("categories"):
        field_note = (f"Bidang ditebak dari isi CV: {m['label']}. Bila targetmu berbeda, pilih bidang sendiri "
                      "lewat tombol Ganti bidang supaya perbandingannya tepat.")

    return {"story": story, "math": math, "losses": losses, "potential": potential, "potential_n": len(top3),
            "market": market_text, "field_note": field_note, "field_tone": field_tone}
