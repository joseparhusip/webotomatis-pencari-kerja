"""Profil target pencarian (lihat ARCHITECTURE.md §3) dan skor kecocokan sementara.

Skor di sini berbasis aturan kata kunci. Di Fase 2 diganti/dilengkapi skor dari AI.
"""

import re

from app.models.enums import ExperienceLevel, RoleCategory

# Keyword yang dikirim ke setiap sumber saat scraping.
# Urutan penting: sumber berkuota (Google Jobs) hanya memakai beberapa keyword pertama.
SEARCH_KEYWORDS: list[str] = [
    "full stack developer",
    "programmer",
    "software engineer",
    "frontend developer",
    "backend developer",
    "web developer",
    "flutter developer",
    "mobile developer",
    "react developer",
    "golang developer",
    "it support",
    "qa engineer",
    "system analyst",
    "data analyst",
    "staff it",
    "devops engineer",
]

# Kategori pengembang (paling sesuai CV) dan kategori IT lain yang tetap relevan.
TARGET_CATEGORIES = {
    RoleCategory.FULLSTACK,
    RoleCategory.FRONTEND,
    RoleCategory.BACKEND,
    RoleCategory.MOBILE,
}
IT_CATEGORIES = TARGET_CATEGORIES | {
    RoleCategory.QA, RoleCategory.IT_SUPPORT, RoleCategory.DATA, RoleCategory.IT_OTHER,
}
# Bobot kategori dalam skor: developer paling cocok; Data/BI & analis sistem dekat dengan CV.
_CATEGORY_POINTS = {
    RoleCategory.FULLSTACK: 40, RoleCategory.FRONTEND: 40, RoleCategory.BACKEND: 40, RoleCategory.MOBILE: 40,
    RoleCategory.DATA: 30, RoleCategory.IT_OTHER: 25, RoleCategory.QA: 25, RoleCategory.IT_SUPPORT: 20,
}

# Skill dari CV, dikelompokkan supaya alias terhitung satu skill.
CV_SKILLS: dict[str, tuple[str, ...]] = {
    "Go": (r"\bgolang\b", r"\bgo\b(?!\s*-?\s*to)", r"\bgin\b"),
    "Next.js": (r"next\.?js",),
    "React": (r"\breact(\.?js)?\b",),
    "Vue.js": (r"\bvue(\.?js)?\b",),
    "TypeScript": (r"\btypescript\b", r"\bts\b"),
    "JavaScript": (r"\bjavascript\b", r"\bjs\b"),
    "Tailwind": (r"\btailwind",),
    "Node.js": (r"node\.?js", r"\bexpress(\.?js)?\b"),
    "PHP": (r"\bphp\b", r"\blaravel\b", r"\bcodeigniter\b"),
    "PostgreSQL": (r"postgre(s|sql)",),
    "MySQL": (r"\bmysql\b",),
    "REST API": (r"\brest(ful)?\b", r"\bapi\b"),
    "Docker": (r"\bdocker\b",),
    "Git": (r"\bgit\b", r"\bgithub\b", r"\bgitlab\b"),
    "Flutter": (r"\bflutter\b",),
    "Dart": (r"\bdart\b",),
    "Firebase": (r"\bfirebase\b", r"\bfcm\b"),
    "Python": (r"\bpython\b",),
    "JWT/RBAC": (r"\bjwt\b", r"\brbac\b", r"\boauth"),
    "Figma": (r"\bfigma\b",),
    "Swagger": (r"\bswagger\b", r"\bopenapi\b"),
}
_CV_SKILL_PATTERNS = {
    name: [re.compile(p, re.I) for p in patterns] for name, patterns in CV_SKILLS.items()
}

_DEV = r"(developer|engineer|programmer|programer|dev\b|app|apps|application)"
_ROLE_RULES: list[tuple[RoleCategory, re.Pattern]] = [
    # "mobile" saja bisa berarti "Barista Mobile" — wajib ada konteks pengembang.
    (RoleCategory.MOBILE, re.compile(rf"flutter|react native|\bkotlin\b|\bswift\b|\b(android|ios|mobile)\b.{{0,20}}{_DEV}", re.I)),
    (RoleCategory.FULLSTACK, re.compile(r"full[\s-]?stack", re.I)),
    (RoleCategory.FRONTEND, re.compile(r"front[\s-]?end|\breact(\.?js)?\b|\bvue(\.?js)?\b|next\.?js|angular|"
                                       r"\bui developer|web developer|web programmer", re.I)),
    (RoleCategory.BACKEND, re.compile(rf"back[\s-]?end|golang|\bgo\s+{_DEV}|node\.?js|\bphp\b|laravel|codeigniter|"
                                      rf"(?<!\w)(java|\.net|asp\.net|python|c#|ruby|rust)\s+{_DEV}|api (developer|engineer)", re.I)),
]

_LEVEL_RULES: list[tuple[ExperienceLevel, re.Pattern]] = [
    (ExperienceLevel.SENIOR, re.compile(r"\b(senior|sr\.?|lead|principal|head|manager|staff engineer|architect)\b", re.I)),
    (ExperienceLevel.ENTRY, re.compile(r"\b(intern(ship)?|magang|fresh ?grad(uate)?|trainee|entry)\b", re.I)),
    (ExperienceLevel.JUNIOR, re.compile(r"\b(junior|jr\.?)\b", re.I)),
    (ExperienceLevel.MID, re.compile(r"\b(mid|middle|intermediate)\b", re.I)),
]

_EXP_WORD = re.compile(r"pengalaman|experience|berpengalaman|hands-on", re.I)
_OFF_STACK = re.compile(r"\b(java|c#|\.net|asp\.net|quarkus|spring boot|ruby|rails|scala|elixir|cobol|abap)\b", re.I)
_YEARS = re.compile(r"(\d{1,2})\s*\+?\s*(?:-\s*\d{1,2}\s*)?(?:tahun|years?|yrs?|thn)", re.I)

# Kategori IT non-developer, dicek dari judul (sebelum aturan non-teknis).
_IT_ROLE_RULES: list[tuple[RoleCategory, re.Pattern]] = [
    (RoleCategory.QA, re.compile(
        r"\b(qa|qc software|quality assurance|software tester|tester|test engineer|testing engineer|sdet|"
        r"software development engineer in test|automation test)", re.I)),
    (RoleCategory.DATA, re.compile(
        r"\b(data (analyst|engineer|scientist|warehouse|analytics|specialist)|business intelligence|bi (analyst|developer)|"
        r"data science|analis data|machine learning|ml engineer|ai engineer|artificial intelligence|etl|database (developer|administrator|admin)|dba)\b", re.I)),
    (RoleCategory.IT_SUPPORT, re.compile(
        r"\b(it support|technical support|tech support|help ?desk|service desk|desktop support|it helpdesk|"
        r"network (engineer|administrator|admin)|system administrator|sys ?admin|noc|it infrastructure|"
        r"infrastructure engineer|teknisi (it|komputer|jaringan)|it technician|staf+ it|staff it|it staff|it officer|"
        r"admin it|it admin|it operation|it ops|support engineer|it engineer|it asset|"
        r"teknisi\b.{0,20}\b(printer|komputer|laptop|cctv|it|jaringan)|computer technician|"
        r"application support|support aplikasi|aplikasi support|edp|electronic data processing)\b", re.I)),
    (RoleCategory.IT_OTHER, re.compile(
        r"\b(system analyst|analis sistem|business analyst|it business analyst|devops|devsecops|sre|site reliability|"
        r"cloud (engineer|architect)|security|cyber|penetration|soc analyst|ui/?ux|ux designer|ui designer|"
        r"product designer|it project|it manager|head of it|it (lead|consultant|auditor|governance|risk)|"
        r"erp|sap|odoo|salesforce|crm (developer|consultant)|presales|pre[- ]sales|solution (engineer|architect)|"
        r"technical writer|scrum master|it product|product engineer|rpa|automation (engineer|specialist)|"
        r"embedded|plc|iot|game developer|blockchain|system implementor|implementor|"
        r"implementation (specialist|engineer|consultant)|technical product manager|product design(er)?)\b", re.I)),
]

# Kata yang menandakan bukan role software (mis. "Sales Representative - Software House").
_NON_TECH = re.compile(r"\b(sales|marketing|admin(?!istrator)|account executive|telemarketing|teacher|guru|"
                       r"pengajar|driver|kurir|security guard|satpam)\b", re.I)

# Judul umum pengembang — kategori pengembang ditentukan dari deskripsi.
_GENERIC_DEV = re.compile(
    r"\b(software (engineer|developer)|programmer|programer|application developer|it developer|"
    r"web programmer|developer|software engineering|pengembang)\b", re.I)
# Penanda umum pekerjaan IT dalam judul (mis. "Staf TI", "IT Specialist").
_GENERIC_IT = re.compile(r"\b(it|ti|teknologi informasi|information technology|sistem informasi|"
                         r"information system|komputer|computer|software|aplikasi)\b", re.I)
# Kategori asli dari situs sumber yang berarti pekerjaan IT.
_SOURCE_IT = re.compile(r"teknologi informasi|information technology|it and software|it & software|"
                        r"software|developer|programmer|engineering & it|it/|komputer", re.I)
_SOURCE_HINTS: list[tuple[RoleCategory, re.Pattern]] = [
    (RoleCategory.IT_SUPPORT, re.compile(r"meja bantuan|dukungan it|help ?desk|jaringan|network|telekomunikasi|"
                                         r"telecommunication|administrasi .*sistem|system administration", re.I)),
    (RoleCategory.QA, re.compile(r"pengujian|penjaminan mutu|testing|quality assurance", re.I)),
    (RoleCategory.DATA, re.compile(r"database|basis data|data|business intelligence", re.I)),
    (RoleCategory.IT_OTHER, re.compile(r"analis bisnis|analis sistem|business/systems analyst|keamanan|security|"
                                       r"arsitek|architect|konsultan|consultant|manajemen|management|proyek|project|"
                                       r"produk|product|penjualan|sales|desain|design", re.I)),
]

_TEXT_SIGNALS: dict[RoleCategory, re.Pattern] = {
    RoleCategory.MOBILE: re.compile(r"\b(flutter|dart|android|kotlin|react native|swift)\b", re.I),
    RoleCategory.FRONTEND: re.compile(r"\b(react|vue|next\.?js|angular|tailwind|front[\s-]?end|html|css)\b", re.I),
    RoleCategory.BACKEND: re.compile(r"\b(golang|node\.?js|express|php|laravel|codeigniter|postgre\w*|mysql|"
                                     r"back[\s-]?end|rest(ful)? api|java|\.net|c#|python|spring)\b", re.I),
}


def is_it_source_category(source_category: str | None) -> bool:
    return bool(source_category and _SOURCE_IT.search(source_category))


def classify_role(title: str, text: str | None = None, source_category: str | None = None) -> RoleCategory:
    """Kategori dari judul, lalu deskripsi, lalu kategori asli situs sumber (mis. subkategori JobStreet)."""
    for category, pattern in _ROLE_RULES:
        if pattern.search(title):
            # "Sales Mobile", "Marketing Web" dsb. bukan pengembang.
            return RoleCategory.OTHER if _NON_TECH.search(title) and not _GENERIC_DEV.search(title) else category
    for category, pattern in _IT_ROLE_RULES:
        if pattern.search(title):
            return category
    # Sales/marketing/pengajar tetap non-IT walau menyebut "software" — KECUALI situs sumber sendiri
    # menaruhnya di kategori IT (mis. Sales Engineer di kategori "Teknologi Informasi" JobStreet).
    if _NON_TECH.search(title):
        return RoleCategory.IT_OTHER if is_it_source_category(source_category) else RoleCategory.OTHER
    if not _GENERIC_DEV.search(title):
        if is_it_source_category(source_category):
            for category, pattern in _SOURCE_HINTS:
                if pattern.search(source_category or ""):
                    return category
            return RoleCategory.IT_OTHER
        if _GENERIC_IT.search(title):
            return RoleCategory.IT_OTHER
        return RoleCategory.OTHER

    hits = {cat: bool(p.search(text or "")) for cat, p in _TEXT_SIGNALS.items()}
    if hits[RoleCategory.MOBILE] and not (hits[RoleCategory.FRONTEND] and hits[RoleCategory.BACKEND]):
        return RoleCategory.MOBILE
    if hits[RoleCategory.FRONTEND] and hits[RoleCategory.BACKEND]:
        return RoleCategory.FULLSTACK
    if hits[RoleCategory.FRONTEND]:
        return RoleCategory.FRONTEND
    if hits[RoleCategory.BACKEND]:
        return RoleCategory.BACKEND
    # "Software Engineer"/"Programmer" tanpa detail stack: anggap generalis.
    return RoleCategory.FULLSTACK


def detect_level(title: str, text: str | None = None) -> ExperienceLevel | None:
    for level, pattern in _LEVEL_RULES:
        if pattern.search(title):
            return level
    # Hanya baris yang membahas pengalaman (hindari "perusahaan berdiri 10 tahun").
    exp_lines = [ln for ln in (text or "").splitlines() if _EXP_WORD.search(ln)]
    years = [int(y) for ln in exp_lines for y in _YEARS.findall(ln) if int(y) <= 20]
    if years:
        need = max(years)  # syarat terberat yang disebut
        if need >= 5:
            return ExperienceLevel.SENIOR
        if need >= 3:
            return ExperienceLevel.MID
        if need >= 1:
            return ExperienceLevel.JUNIOR
    return None


def level_from_years(min_years: int | None) -> ExperienceLevel | None:
    """Syarat pengalaman minimal yang disebut eksplisit oleh situs sumber."""
    if min_years is None:
        return None
    if min_years >= 5:
        return ExperienceLevel.SENIOR
    if min_years >= 3:
        return ExperienceLevel.MID
    if min_years >= 1:
        return ExperienceLevel.JUNIOR
    return ExperienceLevel.ENTRY


def matched_skills(text: str) -> list[str]:
    return [
        name for name, patterns in _CV_SKILL_PATTERNS.items() if any(p.search(text) for p in patterns)
    ]


def score_job(
    category: RoleCategory, level: ExperienceLevel | None, text: str
) -> tuple[int, str, list[str]]:
    """Kembalikan (skor 0–100, alasan, skill yang cocok)."""
    skills = matched_skills(text)
    score = 0
    reasons: list[str] = []

    points = _CATEGORY_POINTS.get(category, 0)
    score += points
    if category not in IT_CATEGORIES:
        reasons.append("bukan pekerjaan IT")
    elif category not in TARGET_CATEGORIES:
        reasons.append("IT, bukan peran pengembang")

    if level in (ExperienceLevel.ENTRY, ExperienceLevel.JUNIOR):
        score += 25
    elif level is None:
        score += 15
    elif level == ExperienceLevel.MID:
        score += 5
        reasons.append("butuh pengalaman 3+ tahun")
    else:
        reasons.append("level senior")

    score += min(35, len(skills) * 7)

    off_stack = sorted({m.lower() for m in _OFF_STACK.findall(text)})
    core = {"Go", "Node.js", "PHP", "React", "Vue.js", "Next.js", "Flutter"} & set(skills)
    if off_stack and not core:
        score -= 20
        reasons.append("stack utama di luar CV: " + ", ".join(off_stack))

    # Target 0–2 tahun: role mid/senior tidak boleh tampil sebagai "sangat cocok".
    if level == ExperienceLevel.SENIOR:
        score = min(score, 30)
    elif level == ExperienceLevel.MID:
        score = min(score, 65)

    reason = ("Cocok: " + ", ".join(skills)) if skills else "Tidak ada skill CV yang disebut"
    if reasons:
        reason += " · " + "; ".join(reasons)
    return max(0, min(score, 100)), reason, skills
