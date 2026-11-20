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
from datetime import UTC, datetime

from app.core.config import get_settings
from app.services.targeting import matched_skills

log = logging.getLogger(__name__)

MIN_TEXT_CHARS = 200  # di bawah ini dianggap PDF hasil scan (tanpa lapisan teks)
AI_TIMEOUT_SECONDS = 180

FIELDS = ("full_name", "email", "phone", "address", "linkedin_url", "portfolio_url", "summary")
LETTER_KEYS = ("intro_id", "intro_en", "strength_id", "strength_en")
CATEGORIES = ("fullstack", "frontend", "backend", "mobile")

_PROMPT = """Ini adalah CV (curriculum vitae) seorang pelamar kerja. Baca SELURUH halaman dengan teliti
(bila berupa gambar/hasil scan, lakukan OCR karakter demi karakter) lalu isi JSON sesuai skema.

DATA DIRI
- full_name, email, phone: disalin persis seperti tertulis.
- address: domisili singkat (kota, provinsi) yang tertulis, bukan alamat lengkap.
- linkedin_url, portfolio_url: link yang tertulis/tertanam (portfolio_url = GitHub atau situs portofolio, bukan LinkedIn).
- summary: ringkasan profesional 2-4 kalimat Bahasa Indonesia baku. Ambil dari bagian ringkasan/profil di CV
  (terjemahkan bila berbahasa Inggris); bila tidak ada, susun HANYA dari fakta yang tertulis.
- skills: skill teknis/alat yang benar-benar tertulis (maks. 30), ejaan resmi (mis. "PostgreSQL", "Node.js").
- experience_years: total tahun pengalaman kerja profesional dari rentang tanggal kerja di CV (bilangan bulat).
  Magang, organisasi, dan proyek kuliah tidak dihitung. 0 bila belum ada pengalaman kerja penuh waktu.

RIWAYAT (salin dari CV, urut terbaru dulu)
- experiences: tiap pengalaman kerja/magang: company, role, employment_type (mis. "Penuh waktu", "Magang",
  "Freelance"; "" bila tidak tertulis), location, start, end (seperti tertulis, mis. "Jan 2024", "Sekarang"),
  highlights (poin pencapaian/tugas, disalin dari CV — boleh dirapikan tanpa mengubah fakta dan angka).
- projects: name, role, tech (daftar teknologi yang tertulis), description (1-2 kalimat dari CV), url (bila ada).
- education: institution, degree (mis. "S1"), major, start, end, gpa (seperti tertulis).
- certifications: nama sertifikat/pelatihan/penghargaan + penerbit + tahun, seperti tertulis.

BAHAN SURAT LAMARAN (hanya dari fakta CV)
- evidence: 4-8 bukti konkret untuk surat lamaran. Tiap bukti satu kalimat utuh yang berdiri sendiri, format
  "Di/Sebagai <peran> di <perusahaan/proyek>, saya <melakukan apa> menggunakan <teknologi>, <hasil/angka>."
  * source: nama perusahaan atau proyek PERSIS seperti tertulis di CV.
  * text_id: Bahasa Indonesia baku; text_en: English formal (terjemahan setia).
  * categories: pilih dari fullstack, frontend, backend, mobile yang sesuai bukti itu.
  * skills: teknologi yang disebut di bukti itu.
  * Angka/metrik hanya boleh yang tertulis di CV. Jangan menambah angka.
- intro_id / intro_en: satu kalimat perkenalan diri yang berdiri sendiri (latar pendidikan + peran saat ini),
  tanpa menyebut posisi yang dilamar. Pola: "Saya merupakan lulusan S1 … yang saat ini bekerja sebagai … di …." /
  "I am a … graduate currently working as … at …."
- strength_id / strength_en: 1-2 kalimat kekuatan kerja yang terbukti dari CV (bukan klise).

{full_text_rule}

Aturan ketat:
- JANGAN mengarang, menebak, atau melengkapi data yang tidak tertulis. Tidak ada -> "" atau [].
- Jangan pernah menulis null, "-", "N/A", "tidak ada", atau placeholder seperti [Nama].
- Salin nama orang, nama perusahaan, email, nomor, dan link apa adanya; jangan "memperbaiki" ejaannya.
- Periksa ulang setiap karakter hasil OCR (0/O, 1/l/I, 5/S, rn/m).
- Kalimat yang kamu susun (summary, evidence, intro, strength) wajib berbahasa baku tanpa salah ketik,
  tanpa tanda seru, tanpa klise ("sangat antusias", "pekerja keras", "passionate", "dynamic")."""

_FULL_TEXT_NEEDED = "- full_text: transkripsi lengkap SELURUH teks CV apa adanya (pertahankan baris & urutan, tanpa ringkasan)."
_FULL_TEXT_SKIP = '- full_text: isi string kosong "".'

_S = {"type": "string"}
_LIST_S = {"type": "array", "items": _S}


def _obj(props: dict) -> dict:
    return {"type": "object", "properties": props, "required": list(props), "propertyOrdering": list(props)}


_SCHEMA = _obj({
    **{k: _S for k in FIELDS},
    "skills": _LIST_S,
    "experience_years": {"type": "integer"},
    "experiences": {"type": "array", "items": _obj({
        "company": _S, "role": _S, "employment_type": _S, "location": _S, "start": _S, "end": _S,
        "highlights": _LIST_S})},
    "projects": {"type": "array", "items": _obj({"name": _S, "role": _S, "tech": _LIST_S, "description": _S, "url": _S})},
    "education": {"type": "array", "items": _obj({
        "institution": _S, "degree": _S, "major": _S, "start": _S, "end": _S, "gpa": _S})},
    "certifications": _LIST_S,
    "evidence": {"type": "array", "items": _obj({
        "source": _S, "text_id": _S, "text_en": _S,
        "categories": {"type": "array", "items": {"type": "string", "enum": list(CATEGORIES)}},
        "skills": _LIST_S})},
    **{k: _S for k in LETTER_KEYS},
    "full_text": _S,
})


class CvScanError(Exception):
    pass


@dataclass
class CvScan:
    fields: dict = field(default_factory=dict)      # data diri + skills + experience_years — tanpa None
    text: str = ""                                  # teks CV (lapisan teks PDF atau hasil OCR AI)
    method: str = "pattern"                         # "gemini" | "anthropic" | "pattern"
    note: str | None = None                         # alasan bila AI tidak dipakai / gagal
    experiences: list[dict] = field(default_factory=list)
    projects: list[dict] = field(default_factory=list)
    education: list[dict] = field(default_factory=list)
    certifications: list[str] = field(default_factory=list)
    evidence: list[dict] = field(default_factory=list)  # {text_id, text_en, categories, skills, source}
    letter: dict = field(default_factory=dict)          # intro_id/en, strength_id/en

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


async def scan_cv(pdf: bytes, text_layer: str) -> CvScan:
    s = get_settings()
    text_layer = text_layer or ""
    needs_ocr = len(text_layer.strip()) < MIN_TEXT_CHARS
    note = None
    links = pdf_links(pdf)
    links_block = ("\n\nTautan (hyperlink) yang tertanam di PDF. Pakai untuk linkedin_url/portfolio_url/url proyek "
                   "bila teksnya hanya tertulis \"LinkedIn\"/\"GitHub\"/\"Demo\":\n" + "\n".join(links)) if links else ""
    if s.ai_ready:
        prompt = _PROMPT.format(full_text_rule=_FULL_TEXT_NEEDED if needs_ocr else _FULL_TEXT_SKIP) + links_block
        try:
            if s.ai_provider == "gemini":
                raw = await asyncio.wait_for(_gemini(pdf, prompt), AI_TIMEOUT_SECONDS)
            else:
                raw = await asyncio.wait_for(_claude(pdf, prompt), AI_TIMEOUT_SECONDS)
            data = _parse_json(raw)
            ocr_text = str(data.get("full_text") or "").strip()
            text = ocr_text if needs_ocr and len(ocr_text) > len(text_layer.strip()) else text_layer
            return build_scan(data, text, links, s.ai_provider or "pattern")
        except Exception as exc:  # jaringan/kuota/key/JSON rusak — tetap lanjut dengan pola sederhana
            log.warning("Scan CV dengan %s gagal: %s", s.ai_label, exc)
            note = f"{s.ai_label} gagal membaca CV ({type(exc).__name__}); dipakai pembacaan sederhana."
    else:
        note = ("AI belum aktif, CV dibaca dengan pola sederhana (pengalaman, proyek, dan bukti CV tidak terisi). "
                "Isi API key di Pengaturan untuk hasil lengkap.")
    if not text_layer.strip():
        note = f"{note} PDF tidak punya teks (hasil scan) dan tidak bisa dibaca tanpa AI."
    return CvScan(heuristic_fields(text_layer + "\n" + "\n".join(links)), text_layer, "pattern", note)


def build_scan(data: dict, text: str, links: list[str], method: str) -> CvScan:
    """Bersihkan + verifikasi jawaban AI terhadap isi CV (teks + hyperlink)."""
    source = text + "\n" + "\n".join(links)
    fields = verify_fields(_clean(data), source)
    # Kolom yang terlewat AI tapi terbaca pola sederhana → lengkapi (sama-sama dari isi CV).
    for key, value in heuristic_fields(source).items():
        if key in ("full_name", "email", "phone", "linkedin_url", "portfolio_url") and not fields.get(key):
            fields[key] = value
    ctx = _Ctx(source)
    return CvScan(
        fields=fields, text=text, method=method,
        experiences=_experiences(data.get("experiences"), ctx),
        projects=_projects(data.get("projects"), ctx),
        education=_education(data.get("education"), ctx),
        certifications=[c for c in (_str(x) for x in _list(data.get("certifications"))) if c and ctx.numbers_ok(c)][:20],
        evidence=_evidence(data.get("evidence"), ctx),
        letter={k: (_sentence(v) if ctx.numbers_ok(v) else "") for k, v in ((k, _str(data.get(k))) for k in LETTER_KEYS)},
    )


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

async def _gemini(pdf: bytes, prompt: str) -> str:
    from google import genai
    from google.genai import errors, types

    s = get_settings()
    client = genai.Client(api_key=s.gemini_api_key,
                          http_options=types.HttpOptions(timeout=AI_TIMEOUT_SECONDS * 1000,
                                                         retry_options=types.HttpRetryOptions(attempts=1)))
    models = [s.gemini_model] + [m for m in s.gemini_fallback_models if m != s.gemini_model]
    last: Exception | None = None
    for model in models:
        try:
            resp = await client.aio.models.generate_content(
                model=model,
                contents=[types.Part.from_bytes(data=pdf, mime_type="application/pdf"), prompt],
                config=types.GenerateContentConfig(
                    temperature=0,
                    max_output_tokens=65536,
                    response_mime_type="application/json",
                    response_schema=_SCHEMA,
                ),
            )
            if resp.text:
                return resp.text
            last = CvScanError("Gemini tidak mengembalikan jawaban.")
        except errors.ServerError as exc:
            last = exc
        except errors.ClientError as exc:
            if getattr(exc, "code", None) not in (404, 429):
                raise
            last = exc
    raise last or CvScanError("Gemini tidak tersedia.")


async def _claude(pdf: bytes, prompt: str) -> str:
    import anthropic

    s = get_settings()
    client = anthropic.AsyncAnthropic(api_key=s.anthropic_api_key)
    resp = await client.messages.create(
        model=s.letter_model,
        max_tokens=32000,
        messages=[{"role": "user", "content": [
            {"type": "document", "source": {"type": "base64", "media_type": "application/pdf",
                                            "data": base64.b64encode(pdf).decode()}},
            {"type": "text", "text": prompt + "\n\nKeluarkan hanya satu objek JSON dengan kunci berikut, tanpa "
                                              "penjelasan dan tanpa ```:\n" + json.dumps(_SCHEMA)},
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
        return all(_num_key(n) in self.numbers for n in _NUM.findall(sentence))


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


def _evidence(items, ctx: _Ctx) -> list[dict]:
    cv_skills = set(matched_skills(ctx.low))
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
        skills = [s for s in matched_skills(f"{named} {text_id}") if s in cv_skills]
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
        "skills": matched_skills(text),
    }
    if years:
        out["experience_years"] = int(years.group(1))
    return out
