"""Pembuatan & pemeriksaan surat lamaran."""

import logging
import re
from dataclasses import dataclass

import anthropic

from app.core.config import get_settings
from app.models import Application, Job, UserProfile
from app.models.enums import ApplyMode
from app.services.letter_template import (
    Evidence,
    _place,
    _source,
    build_template_letter,
    clean_title,
    job_targets,
    letter_header,
    signature,
)
from app.services.parsing import normalize_company

log = logging.getLogger(__name__)

_ID_WORDS = re.compile(r"\b(dan|yang|untuk|dengan|di|ke|dari|kami|anda|memiliki|pengalaman|mampu|kerja)\b", re.I)
_EN_WORDS = re.compile(r"\b(and|the|for|with|to|of|we|you|have|experience|ability|work)\b", re.I)
_PLACEHOLDER = re.compile(r"\[[^\]]{1,60}\]|\{[^}]{1,60}\}|<[A-Z][^>]{0,40}>|nama perusahaan|company name", re.I)
_SUBJECT_HINT = re.compile(r"[^\n]*(subject|subjek|judul email|format email)[^\n]*", re.I)
_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")

# UI_GUIDELINES.md §11 — dipakai di prompt AI dan pemeriksaan otomatis.
CLICHES = [
    "sangat antusias", "sangat bersemangat", "penuh semangat", "dedikasi tinggi",
    "pekerja keras", "bekerja dalam tim maupun individu", "perusahaan terkemuka",
    "perusahaan ternama", "kontribusi terbaik", "nilai tambah yang signifikan",
    "era digital", "passionate", "dynamic", "leverage", "synergy", "fast-paced",
    "i am excited", "i am thrilled",
]

WORDS_MIN, WORDS_MAX = 230, 360


class LetterError(Exception):
    pass


_SYNTHETIC_LINE = re.compile(r"^(Pengalaman|Kategori|Skill):.*$", re.M)  # baris ringkasan buatan connector


def detect_language(job: Job) -> str:
    """Bahasa iklan. Sinyal terlalu sedikit → Indonesia (default pasar lokal)."""
    text = _SYNTHETIC_LINE.sub("", " \n".join(filter(None, [job.description, job.requirements])))
    en, id_ = len(_EN_WORDS.findall(text)), len(_ID_WORDS.findall(text))
    if en + id_ < 8:
        return "id"
    return "en" if en > id_ * 1.2 else "id"


def default_subject(job: Job, profile: UserProfile | None, lang: str) -> str:
    name = profile.full_name if profile else ""
    title = clean_title(job.title)
    if lang == "en":
        return f"Application for {title} - {name}".strip(" -")
    return f"Lamaran Pekerjaan - {title} - {name}".strip(" -")


# ---------- pembuatan ----------

SYSTEM_PROMPT = """Kamu menulis isi surat lamaran kerja (badan email) atas nama kandidat untuk satu lowongan.

Perihal, salam pembuka (Yth. … / Dengan hormat / Dear …), dan tanda tangan SUDAH ditulis sistem. Tulis hanya empat paragraf isi:
1. Pembuka: kalimat pertama WAJIB menyebut posisi yang dilamar persis sama dengan baris "Posisi" pada data lowongan (huruf dan ejaan sama), nama perusahaan persis seperti tertulis, dan sumber lowongan (bila diberikan); lalu satu kalimat perkenalan diri dari CV.
2. Kecocokan: sebut 2–4 kebutuhan konkret yang ditulis perusahaan di iklan (teknologi/tanggung jawab), lalu buktikan dengan 2–3 pengalaman paling relevan dari CV (nama perusahaan/proyek, teknologi, angka).
3. Nilai tambah: satu kekuatan kerja yang relevan dari CV. Jika iklan meminta teknologi yang tidak ada di CV, akui maksimal dua dengan jujur dan singkat (belum menjadi fokus utama, siap mempelajari) tanpa merendahkan diri.
4. Penutup: CV terlampir, harapan dapat mengikuti tahap seleksi berikutnya, ucapan terima kasih.

Aturan:
- Bahasa: id = Bahasa Indonesia baku, formal, dan sopan (gunakan "Bapak/Ibu"); en = formal business English.
- Jangan mengarang pengalaman, angka, atau fakta yang tidak ada di CV. Jangan mengarang informasi tentang perusahaan; sebut hal spesifik tentang perusahaan hanya jika ada di teks lowongan.
- Jika lowongan meminta pengalaman lebih lama dari yang dimiliki kandidat, jangan berbohong; tonjolkan bukti yang paling dekat.
- Panjang isi 250–320 kata (jangan kurang dari 250). Paragraf kecocokan boleh paling panjang. Tanpa bullet point, tanpa tanda seru, tanpa emoji, tanpa placeholder seperti [Nama].
- Nada: profesional, langsung, percaya diri, tidak memohon. Kalimat runtut dan tidak bertele-tele.
- Hindari frasa klise: "sangat antusias", "penuh semangat", "pekerja keras", "mampu bekerja dalam tim maupun individu", "perusahaan terkemuka", "kontribusi terbaik", "di era digital", "passionate", "dynamic", "leverage", "synergy", "fast-paced environment".
- JANGAN menulis salam pembuka, salam penutup ("Hormat saya"), nama, maupun kontak.

Keluarkan hanya empat paragraf tersebut, dipisah satu baris kosong, tanpa subject dan tanpa penjelasan."""


def _job_block(job: Job, profile: UserProfile) -> str:
    have, gaps, _ = job_targets(job, profile)
    parts = [
        f"Sumber lowongan: {_source(job) or '-'}",
        f"Posisi: {clean_title(job.title)}",
        f"Perusahaan: {job.company_name}",
        f"Lokasi: {job.location_city or '-'}",
        f"Skill yang diminta iklan: {', '.join(job.required_skills or []) or '-'}",
        f"  (dimiliki kandidat: {', '.join(have) or '-'}; belum ada di CV: {', '.join(gaps) or '-'})",
        f"Pengalaman diminta: {job.experience_min if job.experience_min is not None else '-'}"
        f"–{job.experience_max if job.experience_max is not None else '-'} tahun · Pendidikan: {job.education or '-'}",
        f"Deskripsi:\n{job.description or '-'}",
        f"Kualifikasi:\n{job.requirements or '-'}",
    ]
    return "\n".join(parts)[:12000]


@dataclass
class LetterResult:
    text: str
    source: str            # "ai" | "template"
    note: str | None = None  # alasan bila AI tidak dipakai / gagal (ditampilkan di halaman review)


async def generate_letter(
    job: Job, profile: UserProfile, cv_text: str | None, lang: str, pool: list[Evidence] | None = None
) -> LetterResult:
    settings = get_settings()
    template = lambda: build_template_letter(job, profile, lang, pool)  # noqa: E731
    if not settings.ai_ready:
        return LetterResult(template(), "template")
    if not cv_text:
        return LetterResult(template(), "template", "AI tidak dipakai: teks CV kosong (unggah CV PDF yang teksnya terbaca).")

    prompt = _user_prompt(job, profile, cv_text, lang)
    try:
        if settings.ai_provider == "gemini":
            body = await _generate_with_gemini(prompt)
        else:
            body = await _generate_with_claude(prompt)
    except LetterError as exc:
        return LetterResult(template(), "template", f"{settings.ai_label} gagal: {exc} Surat disusun dari template.")
    except Exception as exc:  # error jaringan/kuota/key dari SDK — jangan sampai pengguna tanpa surat
        log.warning("%s gagal (%s), pakai template", settings.ai_label, exc)
        return LetterResult(template(), "template",
                            f"{settings.ai_label} gagal ({_short_error(exc)}). Surat disusun dari template.")
    body = _strip_greetings(body)
    return LetterResult(letter_header(job, lang) + "\n\n" + body + "\n\n" + signature(profile, lang), "ai")


def _short_error(exc: Exception) -> str:
    msg = str(exc).replace("\n", " ")
    low = msg.lower()
    if "api key" in low or "api_key" in low or "permission" in low or "401" in msg or "403" in msg:
        return "API key ditolak — cek GEMINI_API_KEY"
    if "429" in msg or "quota" in low or "resource_exhausted" in low or "rate" in low:
        return "kuota/rate limit habis — coba lagi nanti"
    if "not found" in low or "404" in msg:
        return "model tidak ditemukan — cek GEMINI_MODEL"
    return f"{type(exc).__name__}: {msg[:140]}"


_GREETING_LINE = re.compile(
    r"^\s*(perihal|hal\s*:|re\s*:|yth\.?|kepada|dengan hormat|dear|hormat saya|sincerely|best regards|salam)\b", re.I)


def _strip_greetings(body: str) -> str:
    """Model kadang tetap menulis salam/penutup; sistem yang menambahkannya, jadi dibuang di sini."""
    paras = [p.strip() for p in re.split(r"\n\s*\n", body.strip()) if p.strip()]
    while paras and (_GREETING_LINE.match(paras[0]) and len(paras[0]) < 140):
        paras.pop(0)
    while paras and (_GREETING_LINE.match(paras[-1]) or len(paras[-1]) < 60 and "@" in paras[-1]):
        paras.pop()
    return "\n\n".join(paras)


def _user_prompt(job: Job, profile: UserProfile, cv_text: str, lang: str) -> str:
    return (
        f"Bahasa surat: {lang}\n"
        f"Nama kandidat: {profile.full_name}\n\n"
        f"<cv>\n{cv_text[:20000]}\n</cv>\n\n"
        f"<lowongan>\n{_job_block(job, profile)}\n</lowongan>"
    )


GEMINI_TIMEOUT_SECONDS = 60


async def _generate_with_gemini(prompt: str) -> str:
    """Model utama, lalu model cadangan bila sibuk (503), kena limit (429), atau timeout."""
    import asyncio

    from google.genai import errors

    settings = get_settings()
    models = [settings.gemini_model] + [m for m in settings.gemini_fallback_models if m != settings.gemini_model]
    last: Exception | None = None
    for model in models:
        try:
            return await asyncio.wait_for(_gemini_once(model, prompt), GEMINI_TIMEOUT_SECONDS + 10)
        except (errors.ServerError, asyncio.TimeoutError, TimeoutError) as exc:
            log.warning("Gemini %s tidak tersedia (%s), coba model berikutnya", model, type(exc).__name__)
            last = exc
        except errors.ClientError as exc:
            if getattr(exc, "code", None) in (404, 429):  # model tidak ada / limit model ini
                log.warning("Gemini %s ditolak (%s), coba model berikutnya", model, getattr(exc, "code", ""))
                last = exc
                continue
            raise
    raise last or LetterError("Gemini tidak tersedia.")


async def _gemini_once(model: str, prompt: str) -> str:
    from google import genai
    from google.genai import types

    settings = get_settings()
    client = genai.Client(
        api_key=settings.gemini_api_key,
        http_options=types.HttpOptions(
            timeout=GEMINI_TIMEOUT_SECONDS * 1000,  # milidetik
            retry_options=types.HttpRetryOptions(attempts=1),  # cadangan ditangani di atas, bukan retry tanpa henti
        ),
    )
    response = await client.aio.models.generate_content(
        model=model,
        contents=prompt,
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            temperature=0.4,
            max_output_tokens=8192,
            thinking_config=_thinking(model, types),
        ),
    )
    candidate = (response.candidates or [None])[0]
    reason = str(getattr(candidate, "finish_reason", "") or "")
    if "SAFETY" in reason or "PROHIBITED" in reason or "BLOCKLIST" in reason:
        raise LetterError("Gemini menolak membuat surat untuk lowongan ini.")
    if "MAX_TOKENS" in reason:
        raise LetterError("jawaban Gemini terpotong.")
    text = (response.text or "").strip()
    if not text:
        raise LetterError("Gemini tidak mengembalikan teks surat.")
    return text


def _thinking(model: str, types):
    """Gemini 3.x memakai thinking_level; model lama (2.x) dibiarkan default."""
    level = (get_settings().gemini_thinking_level or "").upper()
    if level and re.match(r"gemini-([3-9]|\d{2})", model):
        return types.ThinkingConfig(thinking_level=level)
    return None


async def _generate_with_claude(prompt: str) -> str:
    settings = get_settings()
    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    user = prompt
    response = await client.beta.messages.create(
        model=settings.letter_model,
        max_tokens=4000,
        system=SYSTEM_PROMPT,
        output_config={"effort": "medium"},
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        messages=[{"role": "user", "content": user}],
    )
    if response.stop_reason == "refusal":
        raise LetterError("AI menolak membuat surat untuk lowongan ini. Tulis manual atau buat ulang dengan template.")
    text = "".join(b.text for b in response.content if b.type == "text").strip()
    if not text:
        raise LetterError("AI tidak mengembalikan teks surat.")
    return text


# ---------- pemeriksaan ----------

@dataclass
class Check:
    level: str  # "ok" | "warn" | "error"
    text: str


def _contains(letter: str, phrase: str) -> bool:
    """Cocokkan tanpa peduli huruf besar/kecil & spasi berlebih ("PROGRAMMER  STAF" = "Programmer Staf")."""
    norm = lambda t: re.sub(r"\s+", " ", t).strip().lower()  # noqa: E731
    return bool(phrase.strip()) and norm(phrase) in norm(letter)


def word_count(text: str) -> int:
    return len(re.findall(r"\w+", text or ""))


def run_checks(
    app: Application,
    job: Job,
    profile: UserProfile | None,
    cv_file_ok: bool,
    emails_today: int,
) -> list[Check]:
    s = get_settings()
    letter = app.cover_letter_text or ""
    checks: list[Check] = []

    if profile is None:
        checks.append(Check("error", "Profil belum diisi (nama, email, kontak)."))

    if not letter.strip():
        checks.append(Check("error", "Surat masih kosong."))
    else:
        company_norm = normalize_company(job.company_name)
        letter_norm = normalize_company(letter)
        n = letter_norm.count(company_norm) if company_norm else 0
        checks.append(Check("ok", f"Nama perusahaan muncul ({n}×)") if n
                      else Check("error", f"Nama perusahaan \"{job.company_name}\" tidak ada di surat."))

        title = clean_title(job.title)
        if _contains(letter, title):
            checks.append(Check("ok", f"Posisi \"{title}\" disebut"))
        else:
            checks.append(Check("error", f"Posisi \"{title}\" belum disebut di surat. Tulis posisi yang dilamar "
                                         "sesuai judul lowongan (mis. di baris Perihal)."))

        ph = _PLACEHOLDER.search(letter)
        checks.append(Check("error", f"Ada placeholder: \"{ph.group(0)}\"") if ph
                      else Check("ok", "Tidak ada placeholder"))

        wc = word_count(letter)
        checks.append(Check("ok", f"{wc} kata (target {WORDS_MIN}–{WORDS_MAX})")
                      if WORDS_MIN <= wc <= WORDS_MAX
                      else Check("warn", f"{wc} kata (target {WORDS_MIN}–{WORDS_MAX})"))

        found = [c for c in CLICHES if c in letter.lower()]
        if found:
            checks.append(Check("warn", "Frasa klise: " + ", ".join(f"\"{c}\"" for c in found)))
        if "!" in letter:
            checks.append(Check("warn", "Ada tanda seru."))

    if app.apply_mode == ApplyMode.EMAIL:
        if not app.sent_to:
            checks.append(Check("error", "Email penerima kosong."))
        elif not _EMAIL_RE.match(app.sent_to.strip()):
            checks.append(Check("error", f"Email penerima tidak valid: {app.sent_to}"))
        if not (app.email_subject or "").strip():
            checks.append(Check("error", "Subject kosong."))
        if not cv_file_ok:
            checks.append(Check("error", "File CV tidak ditemukan untuk dilampirkan."))
        if not s.smtp_ready:
            checks.append(Check("error", "SMTP belum diatur (isi SMTP_USER & SMTP_PASSWORD di .env)."))
        if emails_today >= s.max_emails_per_day:
            checks.append(Check("error", f"Kuota harian habis ({emails_today}/{s.max_emails_per_day})."))

    hint = _SUBJECT_HINT.search("\n".join(filter(None, [job.description, job.requirements])))
    if hint:
        checks.append(Check("warn", f"Iklan menyebut format: \"{hint.group(0).strip()[:140]}\""))
    return checks


def can_send(checks: list[Check]) -> bool:
    return not any(c.level == "error" for c in checks)
