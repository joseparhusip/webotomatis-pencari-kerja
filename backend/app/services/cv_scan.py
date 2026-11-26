"""Scan CV (PDF) → isi Profil otomatis: data diri, pengalaman, proyek, pendidikan, bukti CV, kalimat surat.

Urutan:
1. AI (Gemini, atau Claude bila Gemini kosong) membaca PDF langsung — termasuk PDF hasil scan/foto
   (OCR oleh model). Bila PDF tidak punya lapisan teks, AI juga mengembalikan transkripsi teks CV
   supaya surat lamaran AI tetap bisa memakai isi CV.
2. Hasil AI diverifikasi ulang terhadap isi CV (teks + hyperlink): nama, email, HP, link, skill,
   perusahaan, proyek, dan angka yang tidak benar-benar tertulis di CV dibuang (anti data karangan &
   typo OCR). Tidak ada nilai null — kosong = "" atau [].
3. Tanpa AI / AI gagal: pola sederhana (email, HP, LinkedIn, GitHub, skill) dari teks PDF.
"""

import asyncio
import base64
import io
import json
import logging
import re
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime

from app.core.config import get_settings
from app.models.enums import RoleCategory
from app.services.targeting import known_skills, matched_skills

log = logging.getLogger(__name__)

MIN_TEXT_CHARS = 200  # di bawah ini dianggap PDF hasil scan (tanpa lapisan teks)
AI_TIMEOUT_SECONDS = 260         # total untuk semua model (utama + cadangan)
PER_MODEL_TIMEOUT_SECONDS = 120  # satu model; lewat dari ini → coba model cadangan

FIELDS = ("full_name", "email", "phone", "address", "linkedin_url", "portfolio_url", "summary")
LETTER_KEYS = ("intro_id", "intro_en", "strength_id", "strength_en")
# Bidang lowongan (sama dengan RoleCategory, tanpa "other").
CATEGORIES = tuple(c.value for c in RoleCategory if c != RoleCategory.OTHER)
_CATEGORY_HELP = (
    "fullstack, frontend, backend, mobile (pengembang software); qa (software tester); it_support; "
    "data (data/BI analyst, data engineer, AI/ML); it_other (system analyst, devops, UI/UX, ERP, IT lain); "
    "marketing (pemasaran, digital marketing, social media, brand); creative (desain grafis, video editor, "
    "konten kreator, copywriter); sales; admin (administrasi, back office, data entry); customer_service; "
    "finance (akuntansi, keuangan, perbankan); hr; operations (operasional, logistik, gudang, purchasing); "
    "engineering (teknik non-IT); education; healthcare; hospitality (hotel, F&B, retail)"
)

_PROMPT = """Kamu membantu menyusun bahan lamaran kerja. Sistem SUDAH mengambil teks CV (dari PDF / OCR) dan
memetakan datanya (lihat DATA CV di bawah). Tugasmu HANYA menyusun kesimpulan dari fakta yang tertulis di TEKS CV,
lalu isi JSON sesuai skema. Jangan mengubah atau menambah data diri, pengalaman, maupun skill.

- summary_id: ringkasan profesional 2-4 kalimat Bahasa Indonesia baku, hanya dari fakta CV (terjemahkan bila CV
  berbahasa Inggris).
- evidence: 4-8 bukti konkret untuk surat lamaran. Tiap bukti satu kalimat utuh yang berdiri sendiri, format
  "Di/Sebagai <peran> di <perusahaan/proyek>, saya <melakukan apa> menggunakan <teknologi/alat>, <hasil/angka>."
  * source: nama perusahaan atau proyek PERSIS seperti tertulis di CV.
  * text_id: Bahasa Indonesia baku; text_en: English formal (terjemahan setia).
  * categories: bidang lowongan yang cocok untuk bukti itu (kode di bawah, boleh lebih dari satu).
  * skills: teknologi/alat yang disebut di bukti itu.
  * Angka/metrik hanya boleh yang tertulis di CV. Jangan menambah angka.
- intro_id / intro_en: satu kalimat perkenalan diri (latar pendidikan + peran saat ini/terakhir), tanpa menyebut
  posisi yang dilamar. Pola: "Saya merupakan lulusan S1 … yang saat ini bekerja sebagai … di …." /
  "I am a … graduate currently working as … at …."
- strength_id / strength_en: 1-2 kalimat kekuatan kerja yang terbukti dari CV (bukan klise).
- target_roles: 4-10 nama posisi yang realistis dilamar orang ini di portal lowongan Indonesia, berdasarkan
  pengalaman kerja, magang, proyek, pendidikan, dan skill yang BENAR-BENAR tertulis. Urutkan dari yang paling kuat
  buktinya. Ikuti isi CV apa adanya: CV yang mayoritas marketing/media sosial → posisi marketing/konten, bukan
  developer; CV developer → posisi developer. Jangan menaikkan level (lulusan baru → junior/staff, bukan manager).
  * title: nama posisi umum di iklan lowongan (mis. "Digital Marketing Staff", "Backend Developer").
  * category: satu kode bidang: {categories}.
  * basis: dasar dari CV, singkat dan spesifik (mis. "Marketing Officer Intern di PT X; skill Digital Marketing").
- experiences / education: HANYA bila DATA CV menunjukkan daftar itu kosong ([]), salin dari TEKS CV
  (experiences: company, role, employment_type, location, start, end, highlights; education: institution, degree,
  major, start, end, gpa). Bila DATA CV sudah berisi, kembalikan [].

Aturan ketat:
- JANGAN mengarang, menebak, atau melengkapi data yang tidak tertulis. Tidak ada -> "" atau [].
- Jangan pernah menulis null, "-", "N/A", "tidak ada", atau placeholder seperti [Nama].
- Kalimat yang kamu susun wajib berbahasa baku tanpa salah ketik, tanpa tanda seru, tanpa klise
  ("sangat antusias", "pekerja keras", "passionate", "dynamic")."""

_S = {"type": "string"}
_LIST_S = {"type": "array", "items": _S}


def _obj(props: dict) -> dict:
    return {"type": "object", "properties": props, "required": list(props), "propertyOrdering": list(props)}


_SCHEMA = _obj({
    "summary_id": _S,
    "evidence": {"type": "array", "items": _obj({
        "source": _S, "text_id": _S, "text_en": _S,
        "categories": {"type": "array", "items": {"type": "string", "enum": list(CATEGORIES)}},
        "skills": _LIST_S})},
    **{k: _S for k in LETTER_KEYS},
    "target_roles": {"type": "array", "items": _obj({
        "title": _S, "category": {"type": "string", "enum": list(CATEGORIES)}, "basis": _S})},
    "experiences": {"type": "array", "items": _obj({
        "company": _S, "role": _S, "employment_type": _S, "location": _S, "start": _S, "end": _S,
        "highlights": _LIST_S})},
    "education": {"type": "array", "items": _obj({
        "institution": _S, "degree": _S, "major": _S, "start": _S, "end": _S, "gpa": _S})},
})


class CvScanError(Exception):
    pass


@dataclass
class CvScan:
    fields: dict = field(default_factory=dict)      # data diri + skills + experience_years — tanpa None
    text: str = ""                                  # teks CV (lapisan teks PDF atau hasil OCR Tesseract)
    method: str = "text"                            # sumber teks: "text" (PDF) | "ocr" (Tesseract)
    ai: str = ""                                    # "gemini" | "anthropic" bila kesimpulan disusun AI
    note: str | None = None                         # alasan bila AI tidak dipakai / gagal
    experiences: list[dict] = field(default_factory=list)
    projects: list[dict] = field(default_factory=list)
    education: list[dict] = field(default_factory=list)
    certifications: list[str] = field(default_factory=list)
    evidence: list[dict] = field(default_factory=list)  # {text_id, text_en, categories, skills, source}
    letter: dict = field(default_factory=dict)          # intro_id/en, strength_id/en
    targets: list[dict] = field(default_factory=list)   # {title, category, basis} — posisi yang cocok dilamar
    links: list[str] = field(default_factory=list)     # hyperlink di PDF (LinkedIn/GitHub/portofolio)

    def to_data(self) -> dict:
        """Disimpan di cv_document.scan_data (tanpa teks CV, sudah ada di parsed_text)."""
        data = asdict(self)
        data.pop("text")
        data["note"] = data["note"] or ""
        data["scanned_at"] = datetime.now(UTC).isoformat(timespec="seconds")
        return data

    @classmethod
    def from_data(cls, data: dict, text: str = "") -> "CvScan":
        known = {k: v for k, v in (data or {}).items() if k in cls.__dataclass_fields__ and k != "text"}
        return cls(text=text, **known)


async def read_cv(pdf: bytes, text_layer: str, on_stage=None) -> CvScan:
    """Tahap cepat (tanpa AI): 1) teks PDF, atau OCR lokal (Tesseract) bila PDF berupa gambar;
    2) pemetaan data CV oleh kode. Kesimpulan AI menyusul lewat conclude()."""
    async def stage(key: str) -> None:
        if on_stage:
            await on_stage(key)

    await stage("text")
    links = pdf_links(pdf)
    text, method = (text_layer or "").strip(), "text"
    if len(text) < MIN_TEXT_CHARS:
        await stage("ocr")
        from app.services.ocr import ocr_pdf

        ocr_text = await asyncio.to_thread(ocr_pdf, pdf)
        if len(ocr_text) > len(text):
            text, method = ocr_text, "ocr"
    await stage("map")
    scan = pattern_scan(text, links)
    scan.method = method
    scan.links = links
    if not text:
        scan.note = "PDF tidak punya teks dan OCR tidak bisa membacanya (gambar terlalu buram?). Unggah PDF yang lebih jelas."
    else:
        draft_conclusions(scan)  # kolom surat & bukti langsung terisi dari fakta CV; versi AI menyusul
    return scan


_ID_WORDS = re.compile(r"\b(dan|yang|untuk|dengan|dalam|serta|membuat|mengembangkan|melakukan|mengelola|"
                       r"menangani|membangun|melayani|menyusun|bertanggung)\b", re.I)


def _join_id(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " dan " + items[-1]


def _join_en(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def _end(text: str) -> str:
    text = text.strip()
    return text if text.endswith((".", "!", "?")) else text + "."


def draft_conclusions(scan: CvScan) -> None:
    """Kalimat surat & bukti versi dasar TANPA AI — hanya dari data yang sudah dipetakan dari CV (tidak ada klaim
    baru). Dipakai supaya kolom tidak kosong saat AI tidak aktif/sibuk; diganti versi AI begitu AI berhasil."""
    from app.services.cv_targets import clean_role
    from app.services.targeting import _SOFT_SKILL, classify_role

    today = datetime.now(UTC).date()
    exps = sorted((e for e in scan.experiences or [] if e.get("company")),
                  key=lambda e: _month_index(e.get("end") or e.get("start"), is_end=True, today=today) or 0,
                  reverse=True)
    latest = exps[0] if exps else None
    current = bool(latest and _NOW.search(latest.get("end") or ""))
    edu = (scan.education or [None])[0]

    # Perkenalan: pendidikan + pekerjaan terakhir/saat ini.
    intro_id = intro_en = ""
    if edu and (edu.get("major") or edu.get("institution")):
        deg, major, inst = edu.get("degree") or "", edu.get("major") or "", edu.get("institution") or ""
        intro_id = " ".join(p for p in ("Saya merupakan lulusan", deg, major) if p) + (f" dari {inst}" if inst else "")
        intro_en = "I am a " + (f"{major} " if major else "") + "graduate" + (f" of {inst}" if inst else "")
        if latest and latest.get("role"):
            r, c = latest["role"], latest["company"]
            intro_id += f" yang saat ini bekerja sebagai {r} di {c}" if current else f" dengan pengalaman sebagai {r} di {c}"
            intro_en += f" currently working as {r} at {c}" if current else f" with experience as {r} at {c}"
    elif latest and latest.get("role"):
        r, c = latest["role"], latest["company"]
        intro_id = f"Saya {'saat ini bekerja' if current else 'berpengalaman'} sebagai {r} di {c}"
        intro_en = f"I {'currently work' if current else 'have worked'} as {r} at {c}"

    # Kekuatan: skill teknis yang tertulis di CV (soft skill tidak diklaim).
    skills = [s for s in (scan.fields or {}).get("skills", []) if not _SOFT_SKILL.match(s.strip())][:5]
    strength_id = f"Saya terbiasa menggunakan {_join_id(skills)} dalam pekerjaan." if skills else ""
    strength_en = f"I am experienced in working with {_join_en(skills)}." if skills else ""
    scan.letter = {"intro_id": _end(intro_id) if intro_id else "", "intro_en": _end(intro_en) if intro_en else "",
                   "strength_id": strength_id, "strength_en": strength_en}

    # Bukti: satu per pengalaman, dari poin tugas & alat yang tertulis di bawah pengalaman itu.
    vocab = list((scan.fields or {}).get("skills", [])) + known_skills(scan.text or "")
    evidence = []
    for e in exps[:6]:
        role, company = e.get("role") or "", e["company"]
        points = e.get("highlights") or []
        used = matched_skills(" ".join(points), skills=vocab)[:5]
        lead_id = f"Sebagai {role} di {company}" if role else f"Di {company}"
        lead_en = f"As {role} at {company}" if role else f"At {company}"
        first = points[0].strip() if points else ""
        if first and _ID_WORDS.search(first):          # poin CV berbahasa Indonesia → kalimat utuh
            text_id = _end(f"{lead_id}, saya {first[0].lower()}{first[1:]}")
        elif used:
            text_id = _end(f"{lead_id}, saya bekerja menggunakan {_join_id(used)}")
        elif first:
            text_id = _end(f"{lead_id}, saya menangani: {first}")
        else:
            continue                                    # tanpa isi tugas → tidak ada yang bisa dibuktikan
        text_en = _end(f"{lead_en}: {first}") if first else _end(f"{lead_en}, I worked with {_join_en(used)}")
        cat = classify_role(clean_role(role)) if role else RoleCategory.OTHER
        evidence.append({"source": company, "text_id": text_id, "text_en": text_en,
                         "categories": [] if cat == RoleCategory.OTHER else [cat.value], "skills": used})
    scan.evidence = evidence


async def conclude(scan: CvScan) -> CvScan:
    """Tahap AI: ringkasan, bukti surat, kalimat surat, usulan posisi — dari teks + hasil pemetaan.
    Melempar exception bila AI tidak aktif / gagal (pemanggil yang memutuskan coba lagi)."""
    s = get_settings()
    if not s.ai_ready:
        raise CvScanError("AI belum aktif")
    if not scan.text:
        raise CvScanError("teks CV kosong")
    mapped = {"full_name": scan.fields.get("full_name"), "skills": scan.fields.get("skills"),
              "experiences": scan.experiences, "projects": scan.projects, "education": scan.education}
    prompt = (_PROMPT.format(categories=_CATEGORY_HELP)
              + "\n\nDATA CV (hasil pemetaan sistem):\n" + json.dumps(mapped, ensure_ascii=False)
              + ("\n\nTautan di PDF:\n" + "\n".join(scan.links) if scan.links else "")
              + "\n\nTEKS CV:\n<cv>\n" + scan.text[:30000] + "\n</cv>")
    call = _gemini(prompt) if s.ai_provider == "gemini" else _claude(prompt)
    data = _parse_json(await asyncio.wait_for(call, AI_TIMEOUT_SECONDS))
    return add_conclusions(scan, data, scan.links, s.ai_provider or "")


async def scan_cv(pdf: bytes, text_layer: str) -> CvScan:
    """Baca + simpulkan sekaligus (dipakai bila worker/Redis tidak ada)."""
    scan = await read_cv(pdf, text_layer)
    s = get_settings()
    if not scan.text:
        return scan
    if not s.ai_ready:
        scan.note = ("AI belum aktif: data CV dipetakan dari teks; ringkasan Bahasa Indonesia, bukti surat, kalimat surat, "
                     "dan usulan posisi tidak dibuat. Isi API key di Pengaturan.")
        return scan
    try:
        return await conclude(scan)
    except Exception as exc:  # jaringan/kuota/key/JSON rusak — data CV tetap ada dari pemetaan
        log.warning("Kesimpulan CV dengan %s gagal: %s", s.ai_label, exc)
        scan.note = (f"{s.ai_label} gagal menyusun kesimpulan ({type(exc).__name__}); data CV tetap dipetakan dari "
                     "teks. Klik Pindai ulang beberapa saat lagi untuk bukti & kalimat surat.")
        return scan


def add_conclusions(scan: CvScan, data: dict, links: list[str], provider: str) -> CvScan:
    """Kesimpulan AI, diverifikasi terhadap teks CV (angka, nama perusahaan/proyek, dasar target)."""
    ctx = _Ctx(scan.text + "\n" + "\n".join(links))
    summary = _str(data.get("summary_id"))
    if summary and ctx.numbers_ok(summary):
        scan.fields["summary"] = summary
    if not scan.experiences:  # pemetaan tidak menemukan pengalaman (format CV tidak umum) → salinan AI, dicek
        scan.experiences = _experiences(data.get("experiences"), ctx)
        years = total_experience(scan.experiences, scan.projects)
        if years["counted"]:
            scan.fields["experience_years"] = years["years"]
    if not scan.education:
        scan.education = _education(data.get("education"), ctx)
    if not scan.projects and data.get("projects"):
        scan.projects = _projects(data.get("projects"), ctx)
    # Versi AI menggantikan versi dasar; bagian yang tidak lolos pengecekan tetap memakai versi dasar.
    scan.evidence = _evidence(data.get("evidence"), ctx, scan.fields.get("skills") or []) or scan.evidence
    ai_letter = {k: (_sentence(v) if ctx.numbers_ok(v) else "") for k, v in ((k, _str(data.get(k))) for k in LETTER_KEYS)}
    scan.letter = {k: ai_letter.get(k) or (scan.letter or {}).get(k, "") for k in LETTER_KEYS}
    scan.targets = _targets(data.get("target_roles"), ctx)
    scan.ai = provider
    return scan


_BASIS_STOP = {"dan", "and", "di", "at", "the", "dengan", "with", "skill", "skills", "pengalaman", "experience",
               "sebagai", "as", "untuk", "for", "dari", "from", "proyek", "project", "magang", "intern", "pt", "pt."}


def _targets(items, ctx: "_Ctx") -> list[dict]:
    """Posisi target dari AI; hanya yang kategorinya valid dan dasarnya benar-benar tertulis di CV."""
    out, seen = [], set()
    for it in _list(items):
        if not isinstance(it, dict):
            continue
        title, cat, basis = _str(it.get("title"))[:60], _str(it.get("category")).lower(), _str(it.get("basis"))[:200]
        if not title or cat not in CATEGORIES or not basis or title.lower() in seen:
            continue
        words = [w for w in re.findall(r"[a-z0-9.+#]{3,}", basis.lower()) if w not in _BASIS_STOP]
        if ctx.has_text and (not words or not any(w in ctx.low for w in words)):
            continue  # dasar tidak ditemukan di CV → dianggap karangan
        seen.add(title.lower())
        out.append({"title": title, "category": cat, "basis": basis})
    return out[:10]


# ---------- lama pengalaman kerja ----------

_MONTHS = {
    "jan": 1, "januari": 1, "january": 1, "feb": 2, "februari": 2, "february": 2, "peb": 2, "pebruari": 2,
    "mar": 3, "maret": 3, "march": 3, "apr": 4, "april": 4, "mei": 5, "may": 5,
    "jun": 6, "juni": 6, "june": 6, "jul": 7, "juli": 7, "july": 7,
    "agu": 8, "agt": 8, "ags": 8, "agus": 8, "agustus": 8, "aug": 8, "august": 8,
    "sep": 9, "sept": 9, "september": 9, "okt": 10, "oktober": 10, "oct": 10, "october": 10,
    "nov": 11, "nop": 11, "november": 11, "nopember": 11, "des": 12, "desember": 12, "dec": 12, "december": 12,
}
_NOW = re.compile(r"sekarang|saat ini|kini|present|current|now|ongoing|hingga kini|sampai sekarang", re.I)
# Jenis kerja → label. Urutan penting: "magang" dicek sebelum "penuh waktu" (mis. "Magang penuh waktu").
_TYPE_RULES = (
    ("Magang", re.compile(r"magang|intern|internship|trainee|pkl|praktik kerja|kerja praktik|apprentice", re.I)),
    ("Kontrak", re.compile(r"kontrak|contract|pkwt", re.I)),
    ("Freelance", re.compile(r"freelance|lepas|self.?employed|wirausaha|wiraswasta|owner|founder", re.I)),
    ("Paruh waktu", re.compile(r"paruh waktu|part.?time", re.I)),
    ("Penuh waktu", re.compile(r"penuh waktu|full.?time|permanen|permanent|tetap|pkwtt", re.I)),
)
_NOT_WORK = re.compile(r"organisasi|organization|volunteer|relawan|kepanitiaan|komunitas|ukm|bem|himpunan", re.I)


def _month_index(text: str, *, is_end: bool, today: date) -> int | None:
    """'Oct 2023' / 'Oktober 2023' / '10/2023' / '2023-10' / '2023' / 'Sekarang' → tahun*12 + (bulan-1)."""
    t = (text or "").strip().lower()
    if not t:
        return None
    if _NOW.search(t):
        return today.year * 12 + today.month - 1
    year = re.search(r"(19|20)\d{2}", t)
    if not year:
        return None
    y = int(year.group(0))
    month = None
    if m := re.search(r"\b(\d{1,2})\s*[/.-]\s*(?:19|20)\d{2}\b", t):      # 10/2023, 10-2023
        month = int(m.group(1))
    elif m := re.search(r"\b(?:19|20)\d{2}\s*[/.-]\s*(\d{1,2})\b", t):    # 2023-10, 2023/10
        month = int(m.group(1))
    if month is not None and not 1 <= month <= 12:
        month = None
    if month is None:
        for word in re.findall(r"[a-z]+", t):
            if word in _MONTHS:
                month = _MONTHS[word]
                break
    if month is None:  # hanya tahun: awal = Januari, akhir = Desember (atau bulan ini bila tahun berjalan)
        month = (today.month if y == today.year else 12) if is_end else 1
    return y * 12 + month - 1


def experience_type(exp: dict) -> str | None:
    """Label jenis kerja; None = bukan pengalaman kerja (organisasi/relawan)."""
    text = f"{exp.get('employment_type') or ''} {exp.get('role') or ''}"
    if exp.get("employment_type") == "Proyek":
        return "Proyek"
    if _NOT_WORK.search(exp.get("employment_type") or ""):
        return None
    for label, pattern in _TYPE_RULES:
        if pattern.search(text):
            return label
    return "Lainnya"


def total_experience(experiences: list[dict], projects: list[dict] | None = None, today: date | None = None) -> dict:
    """Lama pengalaman dari CV — aturan yang SAMA untuk semua CV: kerja (penuh waktu, kontrak, paruh waktu),
    magang, freelance, dan proyek yang bertanggal. Organisasi tidak dihitung. Tumpang-tindih tidak dobel."""
    items = list(experiences or [])
    for proj in projects or []:
        if proj.get("start"):
            items.append({"company": proj.get("name") or "Proyek", "role": proj.get("role") or "",
                          "employment_type": "Proyek", "start": proj.get("start"), "end": proj.get("end") or ""})
    return experience_summary(items, today)


def experience_summary(experiences: list[dict], today: date | None = None) -> dict:
    """Total pengalaman kerja dari tanggal di CV: penuh waktu, kontrak, magang, freelance, paruh waktu, dan lainnya.

    Periode yang tumpang-tindih digabung (tidak dihitung dobel). Bulan awal & akhir sama-sama dihitung
    (Okt 2023 – Mar 2024 = 6 bulan). Tahun = dibulatkan ke bawah.
    """
    today = today or datetime.now(UTC).date()
    periods: list[tuple[int, int]] = []
    by_type: dict[str, int] = {}
    skipped: list[str] = []
    for exp in experiences or []:
        kind = experience_type(exp)
        name = exp.get("company") or exp.get("role") or "?"
        if kind is None:
            skipped.append(f"{name} (organisasi)")
            continue
        start = _month_index(exp.get("start"), is_end=False, today=today)
        end = _month_index(exp.get("end"), is_end=True, today=today) if exp.get("end") else None
        if end is None and start is not None and not exp.get("end"):
            end = start  # tanpa tanggal akhir → dihitung 1 bulan saja
        if start is None or end is None or end < start:
            skipped.append(f"{name} (tanggal tidak terbaca)")
            continue
        periods.append((start, end))
        by_type[kind] = by_type.get(kind, 0) + (end - start + 1)
    total = 0
    cur_s = cur_e = None
    for s, e in sorted(periods):
        if cur_e is not None and s <= cur_e + 1:
            cur_e = max(cur_e, e)
            continue
        if cur_e is not None:
            total += cur_e - cur_s + 1
        cur_s, cur_e = s, e
    if cur_e is not None:
        total += cur_e - cur_s + 1
    return {"months": total, "years": total // 12, "by_type": by_type, "counted": len(periods), "skipped": skipped}


def months_text(months: int) -> str:
    y, m = divmod(months, 12)
    return " ".join(p for p in (f"{y} thn" if y else "", f"{m} bln" if m else "") if p) or "0 bln"


def _sentence(text: str) -> str:
    """Kalimat utuh: huruf pertama kapital, diakhiri titik."""
    text = text.strip()
    if not text:
        return ""
    text = text[0].upper() + text[1:]
    return text if text[-1] in ".?" else text + "."


def pdf_links(pdf: bytes) -> list[str]:
    """URL dari hyperlink di PDF (mis. teks "LinkedIn" yang bisa diklik). Link Google Drive dilewati."""
    from pypdf import PdfReader

    out: list[str] = []
    try:
        for page in PdfReader(io.BytesIO(pdf)).pages:
            for annot in page.get("/Annots") or []:
                action = annot.get_object().get("/A") or {}
                uri = str(action.get("/URI") or "").strip()
                if uri.startswith(("http://", "https://")) and "drive.google.com" not in uri and uri not in out:
                    out.append(uri)
    except Exception:  # PDF rusak/terenkripsi — cukup tanpa link
        log.debug("Gagal membaca hyperlink PDF", exc_info=True)
    return out[:30]


# ---------- AI ----------

async def _gemini(prompt: str, schema: dict | None = None) -> str:
    from google import genai
    from google.genai import errors, types

    from app.services.letters import _thinking

    s = get_settings()
    client = genai.Client(api_key=s.gemini_api_key,
                          http_options=types.HttpOptions(timeout=PER_MODEL_TIMEOUT_SECONDS * 1000,
                                                         retry_options=types.HttpRetryOptions(attempts=1)))
    models = [s.gemini_model] + [m for m in s.gemini_fallback_models if m != s.gemini_model]
    last: Exception | None = None
    # Server Gemini penuh (503) biasanya pulih dalam hitungan detik: coba lagi sekali per model sebelum pindah.
    attempts = [(m, n) for m in models for n in (1, 2)]
    for model, attempt in attempts:
        if attempt == 2:
            if not isinstance(last, errors.ServerError):
                continue  # timeout/kuota: langsung model berikutnya
            await asyncio.sleep(4)
        try:
            # Batas waktu per model: model utama yang lambat tidak menghabiskan jatah model cadangan.
            resp = await asyncio.wait_for(client.aio.models.generate_content(
                model=model,
                contents=[prompt],  # hanya teks CV + hasil pemetaan, bukan file PDF
                config=types.GenerateContentConfig(
                    temperature=0,
                    max_output_tokens=65536,
                    response_mime_type="application/json",
                    response_schema=schema or _SCHEMA,
                    # Membaca CV = ekstraksi, bukan penalaran panjang: ikuti "Seberapa lama AI berpikir" (bawaan: cepat).
                    thinking_config=_thinking(model, types),
                ),
            ), PER_MODEL_TIMEOUT_SECONDS)
            if resp.text:
                return resp.text
            last = CvScanError("Gemini tidak mengembalikan jawaban.")
        except (errors.ServerError, TimeoutError) as exc:
            log.warning("Kesimpulan CV dengan %s (percobaan %d) gagal/terlalu lama: %s", model, attempt, type(exc).__name__)
            last = exc
        except errors.ClientError as exc:
            if getattr(exc, "code", None) not in (404, 429):
                raise
            last = exc
    raise last or CvScanError("Gemini tidak tersedia.")


async def _claude(prompt: str, schema: dict | None = None) -> str:
    import anthropic

    s = get_settings()
    client = anthropic.AsyncAnthropic(api_key=s.anthropic_api_key)
    resp = await client.messages.create(
        model=s.letter_model,
        max_tokens=32000,
        messages=[{"role": "user", "content": [
            {"type": "text", "text": prompt + "\n\nKeluarkan hanya satu objek JSON dengan kunci berikut, tanpa "
                                              "penjelasan dan tanpa ```:\n" + json.dumps(schema or _SCHEMA)},
        ]}],
    )
    return "".join(b.text for b in resp.content if b.type == "text")


def _parse_json(raw: str) -> dict:
    m = re.search(r"\{.*\}", raw or "", re.S)
    if not m:
        raise CvScanError("jawaban AI bukan JSON")
    data = json.loads(m.group(0))
    if not isinstance(data, dict):
        raise CvScanError("jawaban AI bukan objek JSON")
    return data


# ---------- pembersihan ----------

_EMPTY = re.compile(
    r"^(null|none|nil|n/?a|-+|\.+|tidak ada|belum ada|tidak diketahui|not (provided|available|specified)|unknown|\[.*\])$",
    re.I,
)


def _str(value) -> str:
    """None/placeholder → "" (tidak pernah null)."""
    if value is None or isinstance(value, (dict, list)):
        return ""
    v = re.sub(r"[ \t]+", " ", str(value)).strip()
    return "" if _EMPTY.match(v) else v


def _list(value) -> list:
    if isinstance(value, list):
        return value
    if isinstance(value, str) and value.strip():
        return re.split(r"[,\n]", value)
    return []


def _clean(data: dict) -> dict:
    out = {k: _str(data.get(k)) for k in FIELDS}
    for k in ("full_name", "email", "phone", "address", "linkedin_url", "portfolio_url"):
        out[k] = re.sub(r"\s+", " ", out[k])
    out["email"] = out["email"].replace(" ", "").lower()
    if out["email"] and not _EMAIL.fullmatch(out["email"]):
        out["email"] = ""
    out["skills"] = _dedup(_str(x)[:60] for x in _list(data.get("skills")) if _str(x))[:30]
    try:
        out["experience_years"] = max(0, min(40, int(float(data.get("experience_years") or 0))))
    except (TypeError, ValueError):
        out["experience_years"] = 0
    return out


def _dedup(items) -> list[str]:
    seen, out = set(), []
    for it in items:
        if it.lower() not in seen:
            seen.add(it.lower())
            out.append(it)
    return out


# ---------- verifikasi: buang nilai yang tidak benar-benar ada di isi CV ----------

def _squash(value: str) -> str:
    return re.sub(r"[^a-z0-9@]", "", value.lower())


def _digits(value: str) -> str:
    d = re.sub(r"\D", "", value)
    return "0" + d[2:] if d.startswith("62") else d


def _url_key(value: str) -> str:
    return _squash(re.sub(r"^(https?://)?(www\.)?", "", value.strip().lower()).rstrip("/"))


_NUM = re.compile(r"\d+(?:[.,]\d+)?")


def _num_key(n: str) -> str:
    return n.replace(",", ".").rstrip("0").rstrip(".") if "." in n.replace(",", ".") else n.lstrip("0") or "0"


class _Ctx:
    """Isi CV yang sudah dinormalisasi untuk pengecekan cepat."""

    def __init__(self, source: str):
        self.squashed = _squash(source)
        self.low = source.lower()
        self.words = {w.lower() for w in re.findall(r"[A-Za-z0-9#+]+", source)}
        self.numbers = {_num_key(n) for n in _NUM.findall(source)}
        self.has_text = len(source.strip()) >= 50

    def mentions(self, name: str) -> bool:
        """Nama perusahaan/proyek/institusi tertulis di CV (toleran spasi, tanda baca, huruf besar)."""
        if not self.has_text:
            return True
        key = _squash(name)
        if not key:
            return False
        if key in self.squashed:
            return True
        # Nama panjang boleh disingkat AI ("PT Pos Indonesia (Persero)" vs "PT Pos Indonesia"): ≥80% kata ada.
        words = [w.lower() for w in re.findall(r"[A-Za-z0-9]{3,}", name)]
        return bool(words) and sum(w in self.words for w in words) / len(words) >= 0.8

    def numbers_ok(self, sentence: str) -> bool:
        """Semua angka di kalimat tertulis di CV (mencegah metrik karangan)."""
        if not self.has_text:
            return True
        # Jenjang pendidikan (S1, S2, D3, D4) & kode seperti "B2B" bukan angka/metrik.
        sentence = _DEGREE_CODE.sub(" ", sentence)
        return all(_num_key(n) in self.numbers for n in _NUM.findall(sentence))


_DEGREE_CODE = re.compile(r"\b(?:[SD][1-4]|B2[BC]|C2C)\b", re.I)


_SKILL_ALIASES = {
    "postgresql": ["postgres"], "golang": ["go"], "go": ["golang"], "javascript": ["js"],
    "typescript": ["ts"], "kubernetes": ["k8s"], "react": ["reactjs"], "reactjs": ["react"],
    "nodejs": ["node"], "vuejs": ["vue"], "nextjs": ["next"],
}


def _skill_in_text(skill: str, low: str, squashed: str, words: set[str]) -> bool:
    key = _squash(skill)
    if not key:
        return False
    if skill.lower() in low or (len(key) >= 3 and key in squashed):  # "Node.js" ↔ "NodeJS"
        return True
    if len(key) <= 2:
        return key in words
    return any(a in words for a in _SKILL_ALIASES.get(key, []))


def verify_fields(fields: dict, text: str) -> dict:
    """Cocokkan data diri hasil AI dengan isi CV supaya tidak ada data karangan / typo OCR.

    Bila teks CV terlalu pendek untuk dicocokkan, nilai AI dipakai apa adanya.
    """
    if len(text.strip()) < 50:
        return fields
    out = dict(fields)
    squashed, digits, low = _squash(text), _digits(text), text.lower()
    words = {w.lower() for w in re.findall(r"[A-Za-z0-9#+]+", text)}

    if out.get("email") and _squash(out["email"]) not in squashed:
        found = _EMAIL.search(text)
        out["email"] = found.group(0).lower() if found else ""
    if out.get("phone") and (len(_digits(out["phone"])) < 9 or _digits(out["phone"]) not in digits):
        found = _PHONE.search(text)
        out["phone"] = re.sub(r"\s+", " ", found.group(0)).strip() if found else ""
    for key in ("linkedin_url", "portfolio_url"):
        if out.get(key) and _url_key(out[key]) not in squashed:
            out[key] = ""
    if out.get("full_name"):
        name_words = [w.lower() for w in re.findall(r"[A-Za-z]+", out["full_name"])]
        if not name_words or not all(w in words for w in name_words):
            out["full_name"] = ""
    if out.get("address"):
        addr_words = [w.lower() for w in re.findall(r"[A-Za-z]{4,}", out["address"])]
        if addr_words and not any(w in words for w in addr_words):
            out["address"] = ""
    out["skills"] = [sk for sk in out.get("skills", []) if _skill_in_text(sk, low, squashed, words)]
    return out


def _experiences(items, ctx: _Ctx) -> list[dict]:
    out = []
    for it in _list(items):
        if not isinstance(it, dict):
            continue
        e = {k: _str(it.get(k)) for k in ("company", "role", "employment_type", "location", "start", "end")}
        if not e["company"] or not ctx.mentions(e["company"]):
            continue
        e["highlights"] = [h for h in (_str(x) for x in _list(it.get("highlights"))) if h and ctx.numbers_ok(h)][:10]
        out.append(e)
    return out[:15]


def _projects(items, ctx: _Ctx) -> list[dict]:
    out = []
    for it in _list(items):
        if not isinstance(it, dict):
            continue
        p = {k: _str(it.get(k)) for k in ("name", "role", "description", "url")}
        if not p["name"] or not ctx.mentions(p["name"]):
            continue
        if p["url"] and _url_key(p["url"]) not in ctx.squashed:
            p["url"] = ""
        if not ctx.numbers_ok(p["description"]):
            p["description"] = ""
        p["tech"] = [t for t in _dedup(_str(x) for x in _list(it.get("tech")) if _str(x))
                     if _skill_in_text(t, ctx.low, ctx.squashed, ctx.words)][:15]
        out.append(p)
    return out[:15]


def _education(items, ctx: _Ctx) -> list[dict]:
    out = []
    for it in _list(items):
        if not isinstance(it, dict):
            continue
        e = {k: _str(it.get(k)) for k in ("institution", "degree", "major", "start", "end", "gpa")}
        if not e["institution"] or not ctx.mentions(e["institution"]):
            continue
        if e["gpa"] and not ctx.numbers_ok(e["gpa"]):
            e["gpa"] = ""
        out.append(e)
    return out[:6]


def _evidence(items, ctx: _Ctx, skills: list[str]) -> list[dict]:
    vocab = list(skills) + known_skills(ctx.low)  # skill yang tertulis di CV ini (bukan CV lain)
    cv_skills = set(matched_skills(ctx.low, skills=vocab))
    out = []
    for it in _list(items):
        if not isinstance(it, dict):
            continue
        text_id, text_en, src = _str(it.get("text_id")), _str(it.get("text_en")), _str(it.get("source"))
        if len(text_id) < 40 or not src or not ctx.mentions(src):
            continue
        if not ctx.numbers_ok(text_id):  # ada angka yang tidak tertulis di CV → buang
            continue
        if text_en and not ctx.numbers_ok(text_en):
            text_en = ""
        cats = [c for c in _dedup(_str(x).lower() for x in _list(it.get("categories"))) if c in CATEGORIES]
        # Skill kanonik (sama dengan pencocokan lowongan), hanya yang memang ada di CV.
        named = " ".join(_str(x) for x in _list(it.get("skills")))
        skills = [s for s in matched_skills(f"{named} {text_id}", skills=vocab) if s in cv_skills]
        out.append({"source": src, "text_id": text_id, "text_en": text_en, "categories": cats, "skills": skills})
    return out[:8]


# ---------- tanpa AI ----------

_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PHONE = re.compile(r"(?:\+62|62|0)\s?8[\d\s().-]{7,15}\d")
_LINKEDIN = re.compile(r"(?:https?://)?(?:[a-z]{2,3}\.)?linkedin\.com/in/[A-Za-z0-9_%-]+/?", re.I)
_GITHUB = re.compile(r"(?:https?://)?(?:www\.)?github\.com/[A-Za-z0-9_-]+/?", re.I)
_PORTFOLIO = re.compile(r"https?://[A-Za-z0-9-]+\.(?:vercel\.app|netlify\.app|github\.io|carrd\.co)/?\S*", re.I)
_NAME_LINE = re.compile(r"^[A-Za-z][A-Za-z.' -]{2,60}$")
_HEADINGS = re.compile(
    r"^(curriculum vitae|cv|resume|profil|profile|about me|tentang saya|summary|contact|kontak|data diri)$", re.I)
_YEARS = re.compile(r"(\d{1,2})\+?\s*(?:tahun|years?|yrs?)\s+(?:of\s+)?(?:pengalaman|experience)", re.I)


def pattern_scan(text: str, links: list[str], note: str | None = None) -> CvScan:
    """Pemetaan oleh kode (tanpa AI): data diri, ringkasan, pengalaman, proyek, pendidikan, skill — disalin dari teks."""
    from app.services.cv_text import (
        parse_address, parse_education, parse_experiences, parse_name, parse_projects, parse_skill_list,
        parse_summary, split_sections,
    )

    fields = heuristic_fields(text + "\n" + "\n".join(links))
    fields["full_name"] = parse_name(text) or fields.get("full_name", "")
    fields["address"] = parse_address(text)
    sections = split_sections(text)
    experiences = parse_experiences(sections.get("experience", []))
    fields["summary"] = parse_summary(sections)
    from app.services.targeting import _canonical

    listed = parse_skill_list(text, sections)
    # Teknologi yang terdeteksi di teks hanya ditambahkan bila belum tercantum di daftar skill CV
    # ("Go/Gin" sudah mencakup "Go", "React.js" sudah mencakup "React").
    covered = {(_canonical(p) or p).lower() for s in listed for p in re.split(r"\s*/\s*", s)} | {s.lower() for s in listed}
    fields["skills"] = _dedup(listed + [k for k in fields.get("skills", []) if k.lower() not in covered])[:30]
    projects = parse_projects(sections.get("projects", []))
    years = total_experience(experiences, projects)
    if years["counted"]:
        fields["experience_years"] = years["years"]
    return CvScan(fields, text, "text", note, experiences=experiences, projects=projects,
                  education=parse_education(sections.get("education", [])))


def heuristic_fields(text: str) -> dict:
    """Pola sederhana. experience_years hanya ada bila tertulis eksplisit (tidak menimpa dengan 0)."""
    text = text or ""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    name = next((ln for ln in lines[:8] if _NAME_LINE.match(ln) and not _HEADINGS.match(ln)
                 and 2 <= len(ln.split()) <= 5), "")
    email = _EMAIL.search(text)
    phone = _PHONE.search(text)
    linkedin = _LINKEDIN.search(text)
    github = _GITHUB.search(text)
    site = _PORTFOLIO.search(text)
    years = _YEARS.search(text)
    out = {
        "full_name": name.title() if name.isupper() else name,
        "email": email.group(0).lower() if email else "",
        "phone": re.sub(r"\s+", " ", phone.group(0)).strip() if phone else "",
        "address": "",
        "linkedin_url": linkedin.group(0) if linkedin else "",
        "portfolio_url": (github or site).group(0) if (github or site) else "",
        "summary": "",
        "skills": known_skills(text),
    }
    if years:
        out["experience_years"] = int(years.group(1))
    return out
