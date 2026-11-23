"""Target pencarian & skor kecocokan — semuanya mengikuti CV yang sedang aktif di Profil.

* Klasifikasi lowongan: bidang IT (detail per peran) + bidang non-IT (marketing, admin, sales, …).
* Target (bidang, keyword, skill, lama pengalaman) diambil dari Profil, yang diisi dari scan CV.
  Tidak ada skill/bidang pemilik aplikasi yang ditanam di kode.
* Skor: bidang lowongan vs bidang CV, level vs lama pengalaman, dan skill CV yang disebut iklan.
"""

import re
from dataclasses import dataclass, field

from app.models.enums import ExperienceLevel, RoleCategory

# ---------- bidang ----------

IT_DEV_CATEGORIES = {RoleCategory.FULLSTACK, RoleCategory.FRONTEND, RoleCategory.BACKEND, RoleCategory.MOBILE}
IT_CATEGORIES = IT_DEV_CATEGORIES | {
    RoleCategory.QA, RoleCategory.IT_SUPPORT, RoleCategory.DATA, RoleCategory.IT_OTHER,
}
NON_IT_CATEGORIES = [
    RoleCategory.MARKETING, RoleCategory.CREATIVE, RoleCategory.SALES, RoleCategory.ADMIN,
    RoleCategory.CUSTOMER_SERVICE, RoleCategory.FINANCE, RoleCategory.HR, RoleCategory.OPERATIONS,
    RoleCategory.ENGINEERING, RoleCategory.EDUCATION, RoleCategory.HEALTHCARE, RoleCategory.HOSPITALITY,
]
# Bidang yang berdekatan (skor sebagian bila CV tidak menargetkan bidang itu secara langsung).
_RELATED: dict[RoleCategory, set[RoleCategory]] = {
    RoleCategory.MARKETING: {RoleCategory.CREATIVE, RoleCategory.SALES},
    RoleCategory.CREATIVE: {RoleCategory.MARKETING},
    RoleCategory.SALES: {RoleCategory.MARKETING, RoleCategory.CUSTOMER_SERVICE},
    RoleCategory.ADMIN: {RoleCategory.CUSTOMER_SERVICE, RoleCategory.FINANCE, RoleCategory.HR},
    RoleCategory.CUSTOMER_SERVICE: {RoleCategory.ADMIN, RoleCategory.SALES, RoleCategory.HOSPITALITY},
    RoleCategory.FINANCE: {RoleCategory.ADMIN},
    RoleCategory.HR: {RoleCategory.ADMIN},
    RoleCategory.OPERATIONS: {RoleCategory.ADMIN, RoleCategory.ENGINEERING},
    RoleCategory.ENGINEERING: {RoleCategory.OPERATIONS},
    RoleCategory.HOSPITALITY: {RoleCategory.CUSTOMER_SERVICE, RoleCategory.SALES},
    RoleCategory.DATA: {RoleCategory.IT_OTHER},
}

# Kata kunci pencarian umum per bidang (dipakai hanya untuk bidang yang memang didukung isi CV).
CATEGORY_KEYWORDS: dict[RoleCategory, list[str]] = {
    RoleCategory.FULLSTACK: ["full stack developer", "web developer", "programmer"],
    RoleCategory.FRONTEND: ["frontend developer"],
    RoleCategory.BACKEND: ["backend developer", "software engineer"],
    RoleCategory.MOBILE: ["mobile developer"],
    RoleCategory.QA: ["qa engineer", "software tester"],
    RoleCategory.IT_SUPPORT: ["it support", "staff it"],
    RoleCategory.DATA: ["data analyst", "business intelligence"],
    RoleCategory.IT_OTHER: ["system analyst"],
    RoleCategory.MARKETING: ["digital marketing", "social media specialist", "marketing"],
    RoleCategory.CREATIVE: ["content creator", "graphic designer", "video editor"],
    RoleCategory.SALES: ["sales executive", "account executive"],
    RoleCategory.ADMIN: ["staff admin", "administrasi", "back office"],
    RoleCategory.CUSTOMER_SERVICE: ["customer service"],
    RoleCategory.FINANCE: ["staff accounting", "finance staff"],
    RoleCategory.HR: ["hr staff", "recruitment"],
    RoleCategory.OPERATIONS: ["staff operasional", "logistik", "purchasing"],
    RoleCategory.ENGINEERING: ["engineer"],
    RoleCategory.EDUCATION: ["guru", "tutor"],
    RoleCategory.HEALTHCARE: ["perawat", "apoteker"],
    RoleCategory.HOSPITALITY: ["hotel", "barista", "store crew"],
}
# Keyword teknologi spesifik — dipakai hanya bila skill itu ada di CV.
SKILL_KEYWORDS = {
    "Go": "golang developer", "Flutter": "flutter developer", "React": "react developer",
    "Vue.js": "vue developer", "PHP": "php developer", "Node.js": "node.js developer",
    "Python": "python developer", "Java": "java developer", "Kotlin": "android developer",
    "Power BI": "power bi",
}

# Kategori asli tiap bidang di JobStreet (id dari API pencarian JobStreet, bukan tebakan).
JOBSTREET_CLASSIFICATIONS: dict[RoleCategory, list[str]] = {
    **{c: ["6281"] for c in IT_CATEGORIES},                     # Teknologi Informasi & Komunikasi
    RoleCategory.MARKETING: ["6008"],                           # Pemasaran & Komunikasi
    RoleCategory.CREATIVE: ["6304", "6263"],                    # Periklanan, Seni & Media; Desain & Arsitektur
    RoleCategory.SALES: ["6362"],                               # Penjualan
    RoleCategory.ADMIN: ["6251"],                               # Administrasi & Dukungan Perkantoran
    RoleCategory.CUSTOMER_SERVICE: ["1204"],                    # Call Center & Layanan Konsumen
    RoleCategory.FINANCE: ["1200", "1203", "1214"],             # Akuntansi; Perbankan; Asuransi
    RoleCategory.HR: ["6317"],                                  # Sumber Daya Manusia & Perekrutan
    RoleCategory.OPERATIONS: ["6092"],                          # Manufaktur, Transportasi & Logistik
    RoleCategory.ENGINEERING: ["1209", "1206"],                 # Teknik; Konstruksi
    RoleCategory.EDUCATION: ["6123"],                           # Pendidikan & Pelatihan
    RoleCategory.HEALTHCARE: ["1211"],                          # Kesehatan & Medis
    RoleCategory.HOSPITALITY: ["1212", "6043"],                 # Hospitaliti & Pariwisata; Ritel & Produk Konsumen
}


# Nama kategori JobStreet (dari API pencarian) — untuk teks progres.
JOBSTREET_CLASSIFICATION_NAMES = {
    "6281": "Teknologi Informasi & Komunikasi", "6008": "Pemasaran & Komunikasi", "6304": "Periklanan, Seni & Media",
    "6263": "Desain & Arsitektur", "6362": "Penjualan", "6251": "Administrasi & Dukungan Perkantoran",
    "1204": "Call Center & Layanan Konsumen", "1200": "Akuntansi", "1203": "Perbankan & Layanan Finansial",
    "1214": "Asuransi & Dana Pensiun", "6317": "Sumber Daya Manusia & Perekrutan",
    "6092": "Manufaktur, Transportasi & Logistik", "1209": "Teknik", "1206": "Konstruksi",
    "6123": "Pendidikan & Pelatihan", "1211": "Kesehatan & Medis", "1212": "Hospitaliti & Pariwisata",
    "6043": "Ritel & Produk Konsumen",
}


# ---------- target dari Profil (CV aktif) ----------

@dataclass(frozen=True)
class Target:
    categories: tuple[RoleCategory, ...] = ()   # urut: paling kuat didukung CV dulu
    keywords: tuple[str, ...] = ()
    skills: tuple[str, ...] = ()
    experience_years: int = 0
    home_region: str | None = None             # kode wilayah domisili di CV/Profil
    patterns: dict = field(default_factory=dict, compare=False, hash=False)

    @property
    def is_empty(self) -> bool:
        return not self.categories and not self.keywords


_current = Target()


def current_target() -> Target:
    return _current


def set_target(categories, keywords, skills, experience_years, home_region: str | None = None) -> Target:
    global _current
    cats = []
    for c in categories or []:
        try:
            cat = RoleCategory(c)
        except ValueError:
            continue
        if cat != RoleCategory.OTHER and cat not in cats:
            cats.append(cat)
    skills = tuple(s for s in (skills or []) if s and s.strip())
    _current = Target(tuple(cats), tuple(k for k in (keywords or []) if k and k.strip()), skills,
                      int(experience_years or 0), home_region, _skill_patterns(skills))
    return _current


async def load_target(session) -> Target:
    """Baca target dari Profil (satu baris) dan jadikan target aktif di proses ini."""
    from sqlalchemy import select

    from app.models import UserProfile

    profile = await session.scalar(select(UserProfile).limit(1))
    if profile is None:
        return set_target([], [], [], 0)
    return set_target(profile.target_categories or [], profile.target_keywords or [], profile.skills or [],
                      profile.experience_years, home_region(profile.address))


def home_region(address: str | None) -> str | None:
    """'Manado, Indonesia' / 'Kota Bekasi, Jawa Barat' → kode wilayah domisili."""
    from app.services.wilayah import resolve

    parts = [p.strip() for p in (address or "").split(",") if p.strip()]
    for i, part in enumerate(parts):
        code = resolve(part, parts[i + 1] if i + 1 < len(parts) else None)
        if code:
            return code
    return None


def search_keywords(target: Target | None = None, limit: int = 14) -> list[str]:
    """Keyword scraping: yang tersimpan di Profil, atau (bila kosong) diturunkan dari bidang & skill CV."""
    target = target or _current
    out: list[str] = []

    def add(word: str) -> None:
        w = re.sub(r"\s+", " ", word).strip().lower()
        if w and w not in out:
            out.append(w)

    for k in target.keywords:
        add(k)
    if not out:
        for cat in target.categories:
            for k in CATEGORY_KEYWORDS.get(cat, [])[:2]:
                add(k)
        for name in _skill_names(target.skills):
            if name in SKILL_KEYWORDS:
                add(SKILL_KEYWORDS[name])
    return out[:limit]


# ---------- skill ----------

# Alias teknologi umum → nama kanonik. Hanya kamus ejaan; skill orang tetap dari CV-nya.
TECH_ALIASES: dict[str, tuple[str, ...]] = {
    "Go": (r"\bgolang\b", r"\bgo\b(?!\s*-?\s*to)", r"\bgin\b"),
    "Next.js": (r"next\.?js",),
    "React": (r"\breact(\.?js)?\b",),
    "Vue.js": (r"\bvue(\.?js)?\b",),
    "TypeScript": (r"\btypescript\b",),
    "JavaScript": (r"\bjavascript\b",),
    "Tailwind": (r"\btailwind",),
    "Node.js": (r"node\.?js", r"\bexpress(\.?js)?\b"),
    "PHP": (r"\bphp\b", r"\blaravel\b", r"\bcodeigniter\b"),
    "PostgreSQL": (r"postgre(s|sql)",),
    "MySQL": (r"\bmysql\b",),
    "REST API": (r"\brest(ful)?\s*api\b",),
    "Docker": (r"\bdocker\b",),
    "Git": (r"\bgit\b", r"\bgithub\b", r"\bgitlab\b"),
    "Flutter": (r"\bflutter\b",),
    "Dart": (r"\bdart\b",),
    "Firebase": (r"\bfirebase\b",),
    "Python": (r"\bpython\b",),
    "Java": (r"\bjava\b(?!\s*script)",),
    "Kotlin": (r"\bkotlin\b",),
    "JWT/RBAC": (r"\bjwt\b", r"\brbac\b"),
    "Figma": (r"\bfigma\b",),
    "Swagger": (r"\bswagger\b", r"\bopenapi\b"),
    "SQL": (r"\bsql\b",),
    "Excel": (r"\bexcel\b", r"\bspreadsheet\b"),
    "Power BI": (r"power\s?bi\b",),
    "Tableau": (r"\btableau\b",),
    "SAP": (r"\bsap\b",),
    "Canva": (r"\bcanva\b",),
    "Photoshop": (r"photoshop",),
    "Adobe Premiere": (r"premiere",),
    "CapCut": (r"capcut",),
    "SEO": (r"\bseo\b",),
    "Google Analytics": (r"google analytics",),
    "Meta Ads": (r"(meta|facebook|instagram) ads",),
    "CRM": (r"\bcrm\b",),
}
_ALIAS_NAMES = {
    "golang": "Go", "go": "Go", "gin": "Go", "nextjs": "Next.js", "next": "Next.js", "reactjs": "React",
    "react.js": "React", "vue": "Vue.js", "vuejs": "Vue.js", "ts": "TypeScript", "js": "JavaScript",
    "tailwind css": "Tailwind", "tailwindcss": "Tailwind", "node": "Node.js", "nodejs": "Node.js",
    "express": "Node.js", "express.js": "Node.js", "laravel": "PHP", "codeigniter": "PHP", "postgres": "PostgreSQL",
    "restful api": "REST API", "rest": "REST API", "docker compose": "Docker", "github": "Git", "gitlab": "Git",
    "jwt": "JWT/RBAC", "rbac": "JWT/RBAC", "openapi": "Swagger", "microsoft excel": "Excel", "ms excel": "Excel",
    "sap erp": "SAP", "adobe photoshop": "Photoshop", "premiere pro": "Adobe Premiere", "facebook ads": "Meta Ads",
    "instagram ads": "Meta Ads",
}
# Soft skill tidak dipakai untuk skor (muncul di hampir semua iklan, tidak membedakan).
_SOFT_SKILL = re.compile(
    r"^(communication|komunikasi|teamwork|team ?work|kerja ?sama|problem solving|analytical thinking|critical thinking|"
    r"attention to detail|detail oriented|leadership|kepemimpinan|time management|manajemen waktu|adaptab\w*|"
    r"interpersonal|public speaking|presentation|presentasi|creativ\w*|kreatif|disiplin|discipline|jujur|honest\w*|"
    r"fast learner|cepat belajar|hard ?working|multitasking|self.?motivated)$", re.I)


def _clean_skill(skill: str) -> str:
    return re.sub(r"\s*\(.*?\)\s*", " ", skill).strip(" .,-")


def _canonical(skill: str) -> str | None:
    low = _clean_skill(skill).lower()
    for name in TECH_ALIASES:
        if low == name.lower():
            return name
    return _ALIAS_NAMES.get(low)


def _skill_names(skills) -> list[str]:
    out = []
    for s in skills:
        name = _canonical(s) or _clean_skill(s)
        if name and name not in out:
            out.append(name)
    return out


def _skill_patterns(skills) -> dict[str, list[re.Pattern]]:
    """{nama tampilan: [pola]} untuk skill CV. 'CRM & Customer Data Management' → 'CRM' + 'Customer Data Management'."""
    out: dict[str, list[re.Pattern]] = {}
    for raw in skills:
        for part in re.split(r"\s*(?:&|/|,|\band\b|\bdan\b)\s*", _clean_skill(raw)):
            part = part.strip()
            if len(part) < 2 or _SOFT_SKILL.match(part):
                continue
            canon = _canonical(part)
            if canon:
                out.setdefault(canon, [re.compile(p, re.I) for p in TECH_ALIASES[canon]])
                continue
            words = re.sub(r"\b(support|skills?|basic|dasar|advanced|lanjutan)\b", "", part, flags=re.I).strip()
            if len(words) < 2:
                continue
            literal = re.escape(words).replace(r"\ ", r"\s+")
            out.setdefault(part, [re.compile(rf"(?<![\w]){literal}(?![\w])", re.I)])
    return out


_BUILTIN_PATTERNS = {name: [re.compile(p, re.I) for p in pats] for name, pats in TECH_ALIASES.items()}


def matched_skills(text: str, skills=None) -> list[str]:
    """Skill CV yang disebut di teks. skills=None → skill CV aktif (Profil)."""
    patterns = _current.patterns if skills is None else _skill_patterns(skills)
    return [name for name, pats in patterns.items() if any(p.search(text or "") for p in pats)]


def known_skills(text: str) -> list[str]:
    """Nama teknologi umum yang tertulis di teks (untuk membaca CV tanpa AI)."""
    return [name for name, pats in _BUILTIN_PATTERNS.items() if any(p.search(text or "") for p in pats)]


# ---------- klasifikasi lowongan ----------

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

# QA/QC pabrik & konstruksi bukan QA software: butuh konteks software atau kategori sumber IT.
_QA_NON_SOFTWARE = re.compile(r"\b(qc|quality control|qa\s*/\s*qc|inspector|inspektor|garment|food|pangan|"
                              r"konstruksi|construction|pabrik|factory|manufactur\w*|produksi|packaging|iso)\b", re.I)
_QA_SOFTWARE = re.compile(r"software|aplikasi|application|\bweb\b|mobile|automation|selenium|test case|sdet|\bapi\b|"
                          r"cypress|postman|regression|uat\b", re.I)

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
        r"cloud (engineer|architect)|cyber ?security|it security|information security|penetration|soc analyst|"
        r"ui/?ux|ux designer|ui designer|"
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

# Bidang non-IT dari judul. Urutan penting (mis. "Admin Keuangan" → keuangan, "Sales Admin" → sales).
_NON_IT_TITLE: list[tuple[RoleCategory, re.Pattern]] = [
    (RoleCategory.CUSTOMER_SERVICE, re.compile(
        r"customer (service|care|support|relation|experience|success)|call ?cent(er|re)|contact cent(er|re)|"
        r"\bcs\b|teller|frontliner|layanan pelanggan|customer officer|complaint", re.I)),
    (RoleCategory.HR, re.compile(
        r"\bhr\b|\bhrd\b|\bhrga\b|human resource|recruit|rekrut|talent acquisition|people (ops|operation|partner)|"
        r"payroll|personalia|general affair|\bga\b staff|staff ga\b|compensation|benefit specialist", re.I)),
    (RoleCategory.FINANCE, re.compile(
        r"account(ing|ant)\b|akuntan|akuntansi|finance|finansial|keuangan|\btax\b|pajak|auditor|\baudit\b|treasury|"
        r"collection|collector|penagihan|credit (analyst|officer)|kredit|invoice|billing|budget|controller|"
        r"actuar|aktuaria|underwriter|insurance|asuransi", re.I)),
    (RoleCategory.CREATIVE, re.compile(
        r"graphic design|desain(er)? grafis|(?<!ui )(?<!ux )(?<!product )designer|video ?editor|editor video|"
        r"videograf|photograph|fotograf|content (creator|writer|producer)|konten kreator|kreator konten|copywriter|"
        r"illustrat|ilustrator|motion graphic|animator|creative|kreatif|editor\b|multimedia|desain", re.I)),
    (RoleCategory.SALES, re.compile(
        r"\bsales\b|account executive|account manager|business development|\bbd\b|telesales|telemarketing|"
        r"penjualan|key account|relationship manager|canvass|\bspg\b|\bspb\b|sales promotion|marketing executive|"
        r"tenaga pemasar|agent|agen\b|property consultant|marketing (officer|staff) (kredit|properti|property)", re.I)),
    (RoleCategory.MARKETING, re.compile(
        r"marketing|pemasaran|social media|sosial media|sosmed|digital market|\bbrand|\bseo\b|\bsem\b|"
        r"ads specialist|campaign|\bkol\b|influencer|public relation|\bpr\b|communication|komunikasi|"
        r"market research|riset pasar|e-?commerce (specialist|staff|admin|executive)|live ?host|host live|"
        r"community (manager|specialist)|growth", re.I)),
    (RoleCategory.ADMIN, re.compile(
        r"\badmin\b(?!istrator)|administrasi|administration|administrative|back ?office|data entry|entri data|"
        r"sekretaris|secretary|receptionist|resepsionis|office (staff|assistant|manager|boy|girl)|document control|"
        r"arsip|archiv|clerk|personal assistant", re.I)),
    (RoleCategory.EDUCATION, re.compile(
        r"\bguru\b|teacher|tutor|pengajar|dosen|lecturer|instruktur|instructor|trainer|pendidik|mentor|"
        r"kepala sekolah|wali kelas|coach", re.I)),
    (RoleCategory.HEALTHCARE, re.compile(
        r"perawat|\bnurse|bidan|midwife|apoteker|pharmacist|asisten apoteker|tenaga teknis kefarmasian|\bttk\b|"
        r"dokter|doctor|\bdr\.|medis|medical|radiograf|analis kesehatan|ahli teknologi laboratorium|atlm|"
        r"fisioterapis|physiotherap|therapist|terapis|caregiver|nutrisionis|gizi|psikolog", re.I)),
    (RoleCategory.HOSPITALITY, re.compile(
        r"barista|waiter|waitress|\bserver\b(?!.*(engineer|admin))|\bcook\b|\bchef\b|kitchen|dapur|crew|cashier|kasir|"
        r"housekeep|front office|bellboy|bell boy|concierge|hotel|restaurant|restoran|\bf&b\b|food and beverage|"
        r"store (staff|manager|leader|keeper)|sales associate|pramuniaga|retail|tour|travel|ticketing|"
        r"steward|bartender|cook helper|baker", re.I)),
    (RoleCategory.OPERATIONS, re.compile(
        r"operasional|operation|logistic|logistik|gudang|warehouse|supply chain|purchasing|procurement|pengadaan|"
        r"\bppic\b|inventory|inventori|produksi|production|driver|sopir|kurir|courier|ekspedisi|shipping|"
        r"export|import|exim|dispatcher|checker|helper|packing|picker|operator|stock|quality control|\bqc\b", re.I)),
    (RoleCategory.ENGINEERING, re.compile(
        r"engineer|teknik (sipil|mesin|elektro|industri|kimia)|civil|mechanical|electrical|drafter|surveyor|"
        r"technician|teknisi|mekanik|mechanic|\bhse\b|\bk3\b|estimator|quantity surveyor|site manager|"
        r"pengawas|mandor|foreman|arsitek|architect|maintenance|instrument", re.I)),
]

# Bidang non-IT dari kategori asli situs sumber (JobStreet, Glints, Kalibrr, Dealls, …).
_NON_IT_SOURCE: list[tuple[RoleCategory, re.Pattern]] = [
    (RoleCategory.CUSTOMER_SERVICE, re.compile(r"call cent|layanan konsumen|layanan pelanggan|customer service|"
                                               r"customer support|customer experience", re.I)),
    (RoleCategory.HR, re.compile(r"sumber daya manusia|human resource|\bhr\b|perekrutan|recruit|people", re.I)),
    (RoleCategory.FINANCE, re.compile(r"akuntansi|accounting|finance|keuangan|finansial|perbankan|banking|asuransi|"
                                      r"insurance|pajak|tax|audit", re.I)),
    (RoleCategory.ENGINEERING, re.compile(r"\barsitektur\b(?!.*desain)|konstruksi|construction|teknik|engineering|"
                                          r"pertambangan|mining|energi|energy|sipil|mesin", re.I)),
    (RoleCategory.CREATIVE, re.compile(r"periklanan|seni|\bmedia\b|\bart|design|desain|creative|kreatif|"
                                       r"content|konten|photograph|video", re.I)),
    (RoleCategory.SALES, re.compile(r"penjualan|\bsales\b|business development|pengembangan bisnis", re.I)),
    (RoleCategory.MARKETING, re.compile(r"pemasaran|marketing|komunikasi|communication|public relation|brand", re.I)),
    (RoleCategory.ADMIN, re.compile(r"administrasi|administration|perkantoran|\boffice\b|clerical|admin", re.I)),
    (RoleCategory.EDUCATION, re.compile(r"pendidikan|education|pelatihan|training|teaching|pengajaran", re.I)),
    (RoleCategory.HEALTHCARE, re.compile(r"kesehatan|medis|health|medical|farmasi|pharma|perawat|nursing", re.I)),
    (RoleCategory.HOSPITALITY, re.compile(r"hospitali|pariwisata|tourism|hotel|ritel|retail|f&b|food|restaurant|"
                                          r"produk konsumen|consumer", re.I)),
    (RoleCategory.OPERATIONS, re.compile(r"manufaktur|manufacturing|transportasi|transport|logistik|logistic|"
                                         r"supply chain|operation|operasional|purchasing|pengadaan|produksi|"
                                         r"warehouse|gudang", re.I)),
]


def is_it_source_category(source_category: str | None) -> bool:
    return bool(source_category and _SOURCE_IT.search(source_category))


def _classify_non_it(title: str, source_category: str | None) -> RoleCategory:
    for category, pattern in _NON_IT_TITLE:
        if pattern.search(title):
            return category
    # Kategori sumber: subkategori (setelah " / ") lebih spesifik daripada kategori utama.
    parts = [p.strip() for p in (source_category or "").split("/") if p.strip()]
    for part in reversed(parts):
        for category, pattern in _NON_IT_SOURCE:
            if pattern.search(part):
                return category
    return RoleCategory.OTHER


def _classify_it(title: str, text: str | None, source_category: str | None) -> RoleCategory:
    """Logika IT lama (judul → deskripsi → kategori sumber). OTHER = bukan pekerjaan IT."""
    for category, pattern in _ROLE_RULES:
        if pattern.search(title):
            # "Sales Mobile", "Marketing Web" dsb. bukan pengembang.
            return RoleCategory.OTHER if _NON_TECH.search(title) and not _GENERIC_DEV.search(title) else category
    for category, pattern in _IT_ROLE_RULES:
        if pattern.search(title):
            if (category == RoleCategory.QA and not is_it_source_category(source_category)
                    and not _QA_SOFTWARE.search(f"{title}\n{text or ''}")
                    and (_QA_NON_SOFTWARE.search(title) or source_category)):
                return RoleCategory.OTHER  # QA/QC pabrik, konstruksi, makanan (kategori sumber non-IT)
            if category == RoleCategory.IT_OTHER and re.search(r"\b(sap|erp|crm)\b", title, re.I) and _NON_TECH.search(title):
                return RoleCategory.OTHER  # "Sales SAP Business One", "Marketing CRM" = penjualan
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


def classify_role(title: str, text: str | None = None, source_category: str | None = None) -> RoleCategory:
    """Bidang lowongan: IT (per peran) bila jelas pekerjaan IT, selain itu bidang non-IT."""
    category = _classify_it(title, text, source_category)
    if category != RoleCategory.OTHER:
        return category
    return _classify_non_it(title, source_category)


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


def suitable_levels(experience_years: int) -> list[str]:
    """Level lowongan yang masuk akal untuk lama pengalaman di CV (untuk filter bawaan)."""
    if experience_years >= 5:
        return ["junior", "mid", "senior", "unknown"]
    if experience_years >= 3:
        return ["entry", "junior", "mid", "unknown"]
    return ["entry", "junior", "unknown"]


# ---------- skor ----------

_GENERIC_SKILLS = {"REST API", "Git", "Docker", "JavaScript", "SQL", "Excel", "JWT/RBAC", "Swagger", "Figma"}


def _level_points(level: ExperienceLevel | None, years: int) -> tuple[int, int, str | None]:
    """(poin, batas skor, alasan) untuk level lowongan dibanding lama pengalaman di CV."""
    if level is None:
        return 15, 100, None
    if years >= 5:
        table = {ExperienceLevel.ENTRY: (10, 80, "level di bawah pengalamanmu"), ExperienceLevel.JUNIOR: (20, 100, None),
                 ExperienceLevel.MID: (25, 100, None), ExperienceLevel.SENIOR: (25, 100, None)}
    elif years >= 3:
        table = {ExperienceLevel.ENTRY: (15, 100, None), ExperienceLevel.JUNIOR: (25, 100, None),
                 ExperienceLevel.MID: (25, 100, None), ExperienceLevel.SENIOR: (5, 55, "level senior (5+ tahun)")}
    else:
        table = {ExperienceLevel.ENTRY: (25, 100, None), ExperienceLevel.JUNIOR: (25, 100, None),
                 ExperienceLevel.MID: (5, 65, "butuh pengalaman 3+ tahun"),
                 ExperienceLevel.SENIOR: (0, 30, "level senior")}
    return table[level]


def score_job(
    category: RoleCategory, level: ExperienceLevel | None, text: str, target: Target | None = None
) -> tuple[int, str, list[str]]:
    """Kembalikan (skor 0–100, alasan, skill CV yang cocok) terhadap target CV aktif."""
    target = target or _current
    skills = matched_skills(text) if target is _current else [
        name for name, pats in target.patterns.items() if any(p.search(text or "") for p in pats)]
    reasons: list[str] = []

    score = 0
    if not target.categories:
        reasons.append("target bidang belum ada (unggah CV di Profil)")
    elif category in target.categories:
        score += 40 if target.categories.index(category) < 3 else 32
    elif any(category in _RELATED.get(c, set()) or c in _RELATED.get(category, set()) for c in target.categories):
        score += 15
        reasons.append("bidang dekat dengan CV")
    elif category in IT_CATEGORIES and any(c in IT_CATEGORIES for c in target.categories):
        score += 15
        reasons.append("IT, bukan bidang utama di CV")
    else:
        reasons.append("di luar bidang CV")

    points, cap, level_reason = _level_points(level, target.experience_years)
    score += points
    if level_reason:
        reasons.append(level_reason)

    score += min(35, len(skills) * 7)

    if category in IT_CATEGORIES:
        owned = {s.lower() for s in skills}
        core = [s for s in skills if s not in _GENERIC_SKILLS]  # teknologi utama, bukan istilah umum
        off_stack = sorted({m.lower() for m in _OFF_STACK.findall(text)} - owned)
        if off_stack and not core:
            score -= 20
            reasons.append("stack utama di luar CV: " + ", ".join(off_stack))

    score = min(score, cap)
    reason = ("Cocok: " + ", ".join(skills)) if skills else "Tidak ada skill CV yang disebut"
    if reasons:
        reason += " · " + "; ".join(reasons)
    return max(0, min(score, 100)), reason, skills
