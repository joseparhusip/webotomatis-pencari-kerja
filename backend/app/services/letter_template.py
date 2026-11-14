"""Surat lamaran tanpa AI: disusun dari bukti konkret di CV dan kebutuhan yang ditulis perusahaan.

Struktur (format surat lamaran Indonesia yang umum dipakai HR):
    Yth. Bapak/Ibu Tim Rekrutmen / {perusahaan} / di {kota}
    Dengan hormat,
    1. Pembuka   — sumber lowongan, posisi, perkenalan singkat
    2. Kecocokan — kebutuhan yang ditulis perusahaan → 2–3 bukti CV paling relevan
    3. Nilai     — kekuatan kerja + jujur soal teknologi yang belum dikuasai (maks. 2)
    4. Penutup   — lampiran CV, harapan ikut seleksi, terima kasih
    Hormat saya, / nama / email · portofolio

Kalimat & bukti diambil dari halaman Profil (user_profile & letter_evidence). DEFAULT_* adalah contoh
bawaan dari CV pemilik aplikasi: disalin ke Profil lewat "Salin contoh bawaan", dan dipakai bila Profil
belum punya bukti.
"""

import re
from dataclasses import dataclass

from app.models import Job, UserProfile
from app.models.enums import WorkMode
from app.services.targeting import matched_skills


@dataclass(frozen=True)
class Evidence:
    id: str
    en: str
    categories: frozenset[str]
    skills: frozenset[str]


# Setiap kalimat berdiri sendiri (tidak bergantung pada kalimat sebelumnya) karena urutannya dipilih per lowongan.
DEFAULT_EVIDENCE: list[Evidence] = [
    Evidence(
        "Di PT Sistem Pelayanan Kesehatan dan Data, saya mengembangkan backend platform MedCredix menggunakan "
        "Go (Gin), GORM, dan PostgreSQL, mencakup 44 endpoint REST API yang terdokumentasi dengan Swagger, "
        "autentikasi JWT dengan kontrol akses berbasis peran (RBAC), serta deployment menggunakan Docker Compose.",
        "At PT Sistem Pelayanan Kesehatan dan Data, I develop the backend of the MedCredix platform using Go (Gin), "
        "GORM and PostgreSQL, covering 44 REST API endpoints documented with Swagger, JWT authentication with "
        "role-based access control (RBAC), and deployment with Docker Compose.",
        frozenset({"backend", "fullstack"}),
        frozenset({"Go", "PostgreSQL", "Docker", "REST API", "JWT/RBAC", "Swagger"}),
    ),
    Evidence(
        "Untuk sisi frontend, saya mengembangkan dashboard Sistem Informasi Manajemen Rumah Sakit (SIMRS) di "
        "PT Sistem Pelayanan Kesehatan dan Data menggunakan Next.js, React, TypeScript, dan Tailwind CSS, yang "
        "terintegrasi dengan REST API berdasarkan kontrak Swagger serta validasi data menggunakan Zod.",
        "On the frontend side, I develop a hospital information system (SIMRS) dashboard at PT Sistem Pelayanan "
        "Kesehatan dan Data using Next.js, React, TypeScript and Tailwind CSS, integrated with a REST API based on "
        "a Swagger contract and validated with Zod.",
        frozenset({"frontend", "fullstack"}),
        frozenset({"Next.js", "React", "TypeScript", "Tailwind", "JavaScript", "Swagger"}),
    ),
    Evidence(
        "Sebagai Mobile Developer di JEC Eye Hospitals & Clinics, saya membangun aplikasi Android menggunakan "
        "Flutter beserta backend Node.js/Express, termasuk fitur OCR KTP, push notification FCM, dan Google "
        "Sign-In; uji usability terhadap 120 responden menghasilkan skor SUS 79 (kategori baik).",
        "As a Mobile Developer at JEC Eye Hospitals & Clinics, I built an Android app with Flutter and its "
        "Node.js/Express backend, including ID-card OCR, FCM push notifications and Google Sign-In; a usability "
        "test with 120 respondents scored 79 on the System Usability Scale.",
        frozenset({"mobile", "fullstack"}),
        frozenset({"Flutter", "Dart", "Firebase", "Node.js", "JavaScript", "REST API"}),
    ),
    Evidence(
        "Sebagai Back End Developer di Kantor Pusat PT Pos Indonesia, saya membangun backend aplikasi IHSAN POS "
        "dengan PostgreSQL, mulai dari perancangan ERD hingga pengujian seluruh endpoint menggunakan Postman; "
        "hasil proyek tersebut dipublikasikan di Jurnal JUISI.",
        "As a Back End Developer at PT Pos Indonesia headquarters, I built the IHSAN POS backend on PostgreSQL, "
        "from ERD design to testing every endpoint in Postman; the project was published in the JUISI journal.",
        frozenset({"backend"}),
        frozenset({"PostgreSQL", "REST API"}),
    ),
    Evidence(
        "Pada proyek SecuraKost dan Toko Kelontong Online, saya membangun antarmuka web menggunakan Vue.js "
        "(Composition API dan Pinia) serta Tailwind CSS, termasuk pengelolaan state keranjang dan filter produk "
        "secara reaktif.",
        "In the SecuraKost and Toko Kelontong Online projects, I built web interfaces with Vue.js (Composition API "
        "and Pinia) and Tailwind CSS, including reactive cart state and product filters.",
        frozenset({"frontend"}),
        frozenset({"Vue.js", "Tailwind", "JavaScript"}),
    ),
    Evidence(
        "Pada proyek Digimize, saya mengembangkan aplikasi Android secara end-to-end menggunakan Flutter yang "
        "terhubung dengan REST API berbasis PHP dan MySQL.",
        "In the Digimize project, I developed an end-to-end Android app with Flutter connected to a PHP and "
        "MySQL REST API.",
        frozenset({"mobile"}),
        frozenset({"Flutter", "Dart", "PHP", "MySQL", "REST API"}),
    ),
    Evidence(
        "Pada proyek Website Carter ULBI, saya membangun aplikasi web full stack menggunakan PHP dan MySQL, "
        "termasuk integrasi payment gateway Midtrans dan RajaOngkir API, hingga deployment ke server produksi.",
        "In the Carter ULBI website project, I built a full stack web application with PHP and MySQL, including "
        "Midtrans payment gateway and RajaOngkir API integrations, through to production deployment.",
        frozenset({"fullstack", "backend", "frontend"}),
        frozenset({"PHP", "MySQL", "REST API", "JavaScript", "HTML", "CSS", "Bootstrap"}),
    ),
]

DEFAULT_INTRO_ID = ("Saya merupakan lulusan S1 Bisnis Digital dengan IPK 3,75/4,00 yang saat ini bekerja sebagai "
                    "pengembang full stack untuk produk teknologi kesehatan.")
DEFAULT_INTRO_EN = ("I am a Digital Business graduate (GPA 3.75/4.00) currently working as a full stack developer "
                    "on healthcare technology products.")
DEFAULT_STRENGTH_ID = ("Selain kemampuan teknis, saya terbiasa menganalisis kebutuhan dan memodelkan sistem "
                       "menggunakan UML, ERD, dan kontrak API sebelum implementasi, sehingga dapat berkoordinasi "
                       "dengan baik bersama tim produk maupun tim pengembang.")
DEFAULT_STRENGTH_EN = ("Beyond technical skills, I am used to analysing requirements and modelling systems with UML, "
                       "ERD and API contracts before implementation, which helps me work closely with both product "
                       "and engineering teams.")

SOURCE_LABELS = {"glints": "Glints", "jobstreet": "JobStreet", "kalibrr": "Kalibrr", "lokerid": "Loker.id",
                 "kitalulus": "KitaLulus", "dealls": "Dealls", "googlejobs": "Google Jobs",
                 "instagram": "Instagram", "astra": "Astra Career", "traveloka": "Traveloka Careers"}

# Item "skill" lowongan yang bukan teknologi (tidak disebut sebagai kekurangan).
_SOFT = re.compile(r"komunikasi|communication|kerja ?sama|teamwork|english|bahasa|leadership|problem|analytical|"
                   r"analitis|manajemen waktu|time management|detail|interpersonal|presentasi|presentation|"
                   r"architecture|arsitektur|design pattern|principle|best practice|methodolog", re.I)
_TITLE_NOISE = re.compile(r"\s*[\(\[]?\b(urgent(ly)?( needed| hiring)?|asap|hiring|immediate(ly)?|segera)\b[\)\]]?\s*", re.I)


# ---------- pemilihan isi ----------

def clean_title(title: str) -> str:
    """Judul iklan tanpa kata tambahan iklan ('URGENT', 'ASAP', 'Hiring')."""
    t = _TITLE_NOISE.sub(" ", title)
    t = re.sub(r"\s{2,}", " ", t).strip(" -–|") or title.strip()
    if t.isupper():  # "PROGRAMMER STAF" → "Programmer Staf"; singkatan pendek (IT, UI, PHP) tetap kapital
        t = " ".join(w if len(w) <= 3 else w.capitalize() for w in t.split())
    return t


def skill_key(name: str) -> str:
    """'React.js' / 'ReactJS' / 'react' -> 'react'; 'Tailwind CSS' -> 'tailwind'; 'Golang' -> 'go'."""
    k = name.lower().strip()
    k = re.sub(r"\.?js$", "", k) if k not in ("js", "next.js", "nextjs") else k
    k = k.replace("next.js", "next").replace("nextjs", "next")
    k = re.sub(r"[\s._-]+", "", k)
    k = re.sub(r"(css)$", "", k) if k not in ("css", "css3") else k
    aliases = {"golang": "go", "postgres": "postgresql", "restfulapi": "restapi", "restful": "restapi",
               "api": "restapi", "html5": "html", "css3": "css", "expressjs": "express", "vuejs": "vue",
               "reactjs": "react", "nodejs": "node", "javascriptes6": "javascript", "es6": "javascript"}
    return aliases.get(k, k)


# Skill yang secara wajar ikut dimiliki bila skill utamanya dimiliki (bukan klaim baru).
_IMPLIES = {
    "flutter": {"dart", "mobiledevelopment", "androiddevelopment", "mobileappdevelopment", "android"},
    "react": {"javascript", "html", "css", "frontenddevelopment", "webdevelopment"},
    "vue": {"javascript", "html", "css", "frontenddevelopment", "webdevelopment"},
    "next": {"react", "javascript", "html", "css"},
    "node": {"javascript", "backenddevelopment"},
    "express": {"node", "javascript"},
    "typescript": {"javascript"},
    "go": {"backenddevelopment"},
    "gin": {"go"},
    "php": {"backenddevelopment", "webdevelopment"},
    "postgresql": {"sql"},
    "mysql": {"sql"},
}


def owned_skills(profile: UserProfile | None) -> set[str]:
    owned = {skill_key(s) for s in (profile.skills if profile else []) or []}
    for k in list(owned):
        owned |= _IMPLIES.get(k, set())
    return owned


def job_targets(job: Job, profile: UserProfile | None = None) -> tuple[list[str], list[str], set[str]]:
    """(skill iklan yang BENAR-BENAR dimiliki — ejaan iklan, skill teknis iklan yang belum dimiliki,
    nama skill kanonik yang dicari iklan). Klaim hanya berdasarkan skill di Profil, dicocokkan ketat."""
    owned = owned_skills(profile)
    have, gaps = [], []
    for s in job.required_skills or []:
        if skill_key(s) in owned:
            have.append(s)
        elif len(s) <= 30 and not _SOFT.search(s):
            gaps.append(s)
    text = "\n".join(filter(None, [job.title, job.description, job.requirements, ", ".join(job.required_skills or [])]))
    canonical = set(matched_skills(text))
    if not job.required_skills:  # iklan tanpa daftar skill (mis. JobStreet): ambil dari teks iklan
        have = [s for s in matched_skills(text)
                if s not in ("Git", "REST API") and skill_key(s) in owned]
    return have, gaps, canonical


_GENERIC = {"REST API", "JavaScript", "Git"}
_CATEGORY_CORE = {
    "mobile": {"flutter", "dart", "kotlin", "swift", "reactnative", "android", "ios"},
    "backend": {"go", "node", "express", "php", "postgresql", "mysql", "laravel", "codeigniter", "python"},
    "frontend": {"react", "next", "vue", "typescript", "tailwind", "javascript"},
    "fullstack": {"react", "next", "vue", "node", "go", "php", "postgresql", "mysql", "typescript"},
}
_GENERIC_TERM = re.compile(r"development|\bapi\b|^html|^css|\bsql\b|javascript|^git$", re.I)


def pick_evidence(job: Job, pool: list[Evidence] | None = None, limit: int = 3) -> list[Evidence]:
    """Bukti paling relevan: skill inti yang diminta iklan (bobot 3), skill umum (1), kategori role sama (4)."""
    if pool is None:  # Profil belum punya bukti sama sekali
        pool = DEFAULT_EVIDENCE
    category = job.role_category.value if job.role_category else "other"
    _, _, wanted = job_targets(job)

    def score(item: tuple[int, Evidence]) -> tuple[int, int]:
        idx, e = item
        hits = e.skills & wanted
        return (3 * len(hits - _GENERIC) + len(hits & _GENERIC) + (4 if category in e.categories else 0), -idx)

    ranked = sorted(enumerate(pool), key=score, reverse=True)
    return [e for _, e in ranked[:limit]]


def _join(items: list[str], lang: str) -> str:
    """'A, B, dan C' (PUEBI) / 'A, B, and C'."""
    items = [i for i in items if i]
    if len(items) <= 2:
        return (" dan " if lang == "id" else " and ").join(items)
    return ", ".join(items[:-1]) + (", dan " if lang == "id" else ", and ") + items[-1]


def _bare_url(url: str | None) -> str | None:
    """'https://www.joseparhusip.com/' -> 'joseparhusip.com' (lebih rapi di tanda tangan)."""
    if not url:
        return None
    return re.sub(r"^https?://(www\.)?", "", url.strip()).rstrip("/") or None


def _source(job: Job) -> str | None:
    for listing in getattr(job, "listings", None) or []:
        name = getattr(getattr(listing, "source", None), "name", None)
        if name:
            return SOURCE_LABELS.get(name, name)
    return None


def _place(job: Job) -> str:
    if job.work_mode == WorkMode.REMOTE or not job.location_city:
        return "Tempat"
    return re.sub(r"^(Kota|Kabupaten|Kab\.)\s+", "", job.location_city)


# ---------- penyusunan ----------

def letter_header(job: Job, lang: str) -> str:
    """Kepala surat, disusun sistem (bukan AI) supaya posisi & tujuan selalu tepat:

        Perihal: Lamaran Pekerjaan sebagai {posisi}

        Yth. Bapak/Ibu Tim Rekrutmen
        {perusahaan}
        di {kota}

        Dengan hormat,
    """
    title, company = clean_title(job.title), job.company_name.strip()
    if lang == "en":
        return f"Re: Application for the {title} position\n\nDear Hiring Team at {company},"
    return (f"Perihal: Lamaran Pekerjaan sebagai {title}\n\n"
            f"Yth. Bapak/Ibu Tim Rekrutmen\n{company}\ndi {_place(job)}\n\nDengan hormat,")


def signature(profile: UserProfile, lang: str) -> str:
    """Tanda tangan surat:

        Hormat saya,
        (dua baris kosong — ruang tanda tangan)
        Nama
        email
        portofolio
        LinkedIn

    Nomor HP sengaja tidak dicantumkan.
    """
    closing = "Hormat saya," if lang == "id" else "Sincerely,"
    contact = [profile.email, _bare_url(profile.portfolio_url), _bare_url(profile.linkedin_url)]
    return "\n".join([closing, "", "", profile.full_name] + [c for c in contact if c])


def build_template_letter(
    job: Job, profile: UserProfile, lang: str, pool: list[Evidence] | None = None
) -> str:
    letter = _compose(job, profile, lang, pick_evidence(job, pool, 3))
    if len(re.findall(r"\w+", letter)) < 230:  # target minimal pemeriksaan surat
        letter = _compose(job, profile, lang, pick_evidence(job, pool, 4))
    return letter


def _compose(job: Job, profile: UserProfile, lang: str, evidence: list[Evidence]) -> str:
    title, company = clean_title(job.title), job.company_name.strip()
    have, gaps, _ = job_targets(job, profile)
    # Sebut dulu skill yang langsung dibuktikan oleh pengalaman di paragraf yang sama; istilah umum belakangan.
    proof = [skill_key(x) for e in evidence for x in e.skills]
    core = _CATEGORY_CORE.get(job.role_category.value if job.role_category else "", set())
    have = sorted(have, key=lambda x: (
        -(proof.count(skill_key(x)) + (2 if skill_key(x) in core else 0)),
        bool(_GENERIC_TERM.search(x)),
        have.index(x),
    ))
    source = _source(job)
    need = _join(have[:4], lang)
    gap = _join(gaps[:2], lang)

    if lang == "en":
        header = letter_header(job, lang)
        opening = (f"I am writing to apply for the {title} position at {company}"
                   + (f", which I found on {source}." if source else ".")
                   + (f" {profile.intro_en}" if profile.intro_en else ""))
        fit = (f"The role calls for {need}. These requirements closely match my hands-on experience. " if need
               else "My experience below is directly relevant to the needs of this role. ")
        fit += " ".join(e.en or e.id for e in evidence)
        value = profile.strength_en or ""
        if gap and need:
            value += (f" {gap} {'are' if len(gaps[:2]) > 1 else 'is'} not yet my main focus, but I learn new "
                      "technologies quickly and am ready to get up to speed with your team's stack.")
        closing = ("I have attached my CV for your consideration. I would welcome the opportunity to proceed to the "
                   "next stage of your selection process and to discuss how I can contribute to your team. "
                   "Thank you for your time and consideration.")
        parts = [header, opening, fit, value.strip(), closing]
    else:
        header = letter_header(job, lang)
        opening = ((f"Berdasarkan informasi lowongan pekerjaan yang saya peroleh melalui {source}, saya bermaksud "
                    if source else "Melalui surat ini, saya bermaksud ")
                   + f"mengajukan lamaran untuk posisi {title} di {company}."
                   + (f" {profile.intro_id}" if profile.intro_id else ""))
        fit = (f"Posisi ini membutuhkan penguasaan {need}. Kebutuhan tersebut sejalan dengan pengalaman "
               "yang telah saya jalani. " if need
               else "Pengalaman saya berikut relevan dengan kebutuhan posisi ini. ")
        fit += " ".join(e.id for e in evidence)
        value = profile.strength_id or ""
        if gap and need:
            value += (f" Untuk {gap} yang juga disebutkan dalam kualifikasi, saya belum menjadikannya fokus utama, "
                      "namun saya terbiasa mempelajari teknologi baru dengan cepat dan siap menyesuaikan diri "
                      "dengan standar tim.")
        closing = ("Sebagai bahan pertimbangan, bersama surat ini saya lampirkan CV yang memuat rincian pendidikan "
                   "dan pengalaman saya. Besar harapan saya untuk dapat mengikuti tahap seleksi berikutnya "
                   "sehingga dapat menjelaskan kemampuan saya secara lebih lengkap. Atas perhatian dan waktu "
                   "yang Bapak/Ibu berikan, saya ucapkan terima kasih.")
        parts = [header, opening, fit, value.strip(), closing]
    return "\n\n".join(p for p in parts if p) + "\n\n" + signature(profile, lang)
