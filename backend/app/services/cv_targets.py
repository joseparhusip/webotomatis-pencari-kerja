"""Target pencarian (bidang + keyword) yang diturunkan dari isi CV — untuk CV siapa pun, IT maupun non-IT.

Setiap bidang hanya masuk target bila ada dasarnya di CV: pengalaman kerja/magang (bobot terbesar),
isi pengalaman, proyek, skill, dan posisi usulan AI yang dasarnya sudah diverifikasi saat scan.
Tidak ada bidang atau keyword bawaan pemilik aplikasi.
"""

import re
from collections import defaultdict
from dataclasses import dataclass, field

from app.models.enums import RoleCategory
from app.services.targeting import (
    CATEGORY_KEYWORDS, IT_CATEGORIES, SKILL_KEYWORDS, _canonical, _clean_skill, classify_role, known_skills,
)

MAX_CATEGORIES = 6
MAX_KEYWORDS = 14
MIN_WEIGHT = 2
SKILL_CAP = 3  # daftar skill panjang tidak boleh mengalahkan pengalaman kerja nyata

# Skill/isi CV → bidang. Hanya kata yang jelas menunjuk bidang tertentu.
_SIGNALS: list[tuple[RoleCategory, re.Pattern]] = [
    (RoleCategory.DATA, re.compile(r"power\s?bi|tableau|data (analy|visuali|cleaning|labell?ing|science|mining)|"
                                   r"\betl\b|pandas|machine learning|\bnlp\b|natural language|statisti|dashboard|"
                                   r"looker|spss|\bsql\b", re.I)),
    (RoleCategory.MARKETING, re.compile(r"digital marketing|social media|media sosial|sosial media|\bseo\b|\bsem\b|"
                                        r"(meta|facebook|instagram|google|tiktok) ads|\bads\b|campaign|market research|"
                                        r"riset pasar|branding|\bbrand\b|influencer|\bkol\b|promot|promosi|marketing", re.I)),
    (RoleCategory.CREATIVE, re.compile(r"photoshop|illustrator|premiere|after effects|capcut|canva|coreldraw|"
                                       r"video (edit|production)|edit(ing)? video|graphic design|desain grafis|"
                                       r"content creat|konten|reels|tiktok video|short video|copywrit|fotografi|"
                                       r"photograph|videograph", re.I)),
    (RoleCategory.SALES, re.compile(r"\bsales\b|penjualan|cross.?sell|up.?sell|negotiat|negosiasi|closing|"
                                    r"\bb2b\b|prospect|target penjualan|online (store|shop)|toko online|shopee|"
                                    r"tokopedia|marketplace", re.I)),
    (RoleCategory.ADMIN, re.compile(r"administra|data entry|entri data|filing|arsip|microsoft office|ms office|"
                                    r"\bexcel\b|surat.?menyurat|back office|document", re.I)),
    (RoleCategory.CUSTOMER_SERVICE, re.compile(r"customer (service|care|complaint|support|relation|data)|\bcrm\b|"
                                               r"keluhan|complaint|melayani nasabah|serving customers|pelanggan", re.I)),
    (RoleCategory.FINANCE, re.compile(r"accounting|akuntansi|\btax\b|pajak|accurate|zahir|myob|jurnal|journal|"
                                      r"financial report|laporan keuangan|budget|anggaran|invoice", re.I)),
    (RoleCategory.HR, re.compile(r"recruit|rekrutmen|payroll|\bhris\b|human (resource|capital)|onboarding karyawan",
                                 re.I)),
    (RoleCategory.OPERATIONS, re.compile(r"\bsap\b|\berp\b|warehouse|gudang|inventory|logisti|supply chain|"
                                         r"procurement|purchasing|lead time", re.I)),
    (RoleCategory.EDUCATION, re.compile(r"mengajar|teaching|tutor|pengajar|guru\b|lesson plan", re.I)),
    (RoleCategory.HEALTHCARE, re.compile(r"keperawatan|nursing|farmasi|pharmac|klinis|clinical|pasien|patient care",
                                         re.I)),
]
# Teknologi → bidang pengembang.
_TECH_CATEGORY = {
    "Go": RoleCategory.BACKEND, "Node.js": RoleCategory.BACKEND, "PHP": RoleCategory.BACKEND,
    "PostgreSQL": RoleCategory.BACKEND, "MySQL": RoleCategory.BACKEND, "Java": RoleCategory.BACKEND,
    "React": RoleCategory.FRONTEND, "Next.js": RoleCategory.FRONTEND, "Vue.js": RoleCategory.FRONTEND,
    "TypeScript": RoleCategory.FRONTEND, "Tailwind": RoleCategory.FRONTEND,
    "Flutter": RoleCategory.MOBILE, "Dart": RoleCategory.MOBILE, "Kotlin": RoleCategory.MOBILE,
    "Power BI": RoleCategory.DATA, "Tableau": RoleCategory.DATA,
}
_ROLE_NOISE = re.compile(r"\(.*?\)|\b(intern(ship)?|magang|trainee|part[- ]?time|freelance|kontrak|contract|"
                         r"full[- ]?time|paruh waktu|penuh waktu|pkl)\b|[|•·].*$", re.I)


@dataclass
class TargetPlan:
    categories: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    basis: dict[str, list[str]] = field(default_factory=dict)   # bidang → dasar di CV (untuk ditampilkan)
    weights: dict[str, float] = field(default_factory=dict)


def clean_role(role: str) -> str:
    """'Marketing Officer Intern (MAGENTA BUMN)' → 'marketing officer'."""
    r = _ROLE_NOISE.sub(" ", role or "")
    return re.sub(r"\s+", " ", r).strip(" -–,").lower()


def derive_targets(scan) -> TargetPlan:
    """Bidang & keyword dari hasil scan CV (CvScan)."""
    weight: dict[RoleCategory, float] = defaultdict(float)
    basis: dict[RoleCategory, list[str]] = defaultdict(list)
    role_keywords: list[tuple[RoleCategory, str]] = []

    def add(cat: RoleCategory, w: float, why: str) -> None:
        if cat == RoleCategory.OTHER:
            return
        weight[cat] += w
        if why and why not in basis[cat]:
            basis[cat].append(why)

    # 1) Pengalaman kerja/magang: judul posisi (bobot 3) + isi tugasnya (sinyal bidang, bobot 1).
    for exp in scan.experiences or []:
        role, company = exp.get("role") or "", exp.get("company") or ""
        highlights = " ".join(exp.get("highlights") or [])
        label = f"{role or 'Pengalaman'} di {company}".strip()
        cleaned = clean_role(role)
        if cleaned:
            cat = classify_role(cleaned, highlights)
            if cat == RoleCategory.IT_OTHER and _SIGNALS[0][1].search(highlights):
                cat = RoleCategory.DATA  # judul IT umum ("IT Application Intern") tapi isinya analisis data
            add(cat, 3, f"Pengalaman: {label}")
            if cat != RoleCategory.OTHER and len(cleaned) >= 4:
                role_keywords.append((cat, cleaned))
        for cat, pattern in _SIGNALS:
            if pattern.search(highlights):
                add(cat, 1, f"Isi pengalaman di {company}" if company else "Isi pengalaman")

    # 2) Proyek: peran di proyek (bobot 1.5).
    for proj in scan.projects or []:
        cleaned = clean_role(proj.get("role") or "")
        if cleaned:
            add(classify_role(cleaned, " ".join(proj.get("tech") or [])), 1.5,
                f"Proyek: {proj.get('name') or ''} ({proj.get('role')})".strip())

    # 3) Skill (dibatasi per bidang).
    skills = list((scan.fields or {}).get("skills") or [])
    skill_hits: dict[RoleCategory, list[str]] = defaultdict(list)
    for s in skills:
        canon = _canonical(s)
        if canon in _TECH_CATEGORY:
            skill_hits[_TECH_CATEGORY[canon]].append(canon)
            continue
        for cat, pattern in _SIGNALS:
            if pattern.search(s):
                skill_hits[cat].append(_clean_skill(s))
                break
    for cat, names in skill_hits.items():
        add(cat, min(SKILL_CAP, len(names)), "Skill: " + ", ".join(names[:5]))

    # 4) Posisi usulan AI (dasarnya sudah dicek ada di CV saat scan).
    ai_titles: list[tuple[RoleCategory, str]] = []
    for t in scan.targets or []:
        try:
            cat = RoleCategory(t.get("category"))
        except ValueError:
            continue
        add(cat, 2, f"{t.get('title')}: {t.get('basis')}")
        ai_titles.append((cat, t.get("title") or ""))

    # Frontend & backend sama-sama kuat → full stack.
    if weight.get(RoleCategory.FRONTEND, 0) >= 3 and weight.get(RoleCategory.BACKEND, 0) >= 3:
        add(RoleCategory.FULLSTACK, min(weight[RoleCategory.FRONTEND], weight[RoleCategory.BACKEND]),
            "Pengalaman frontend dan backend")

    ranked = [c for c, w in sorted(weight.items(), key=lambda x: -x[1]) if w >= MIN_WEIGHT][:MAX_CATEGORIES]
    keywords: list[str] = []

    def kw(word: str) -> None:
        w = re.sub(r"\s+", " ", word).strip().lower()
        if w and len(w) >= 3 and w not in keywords and len(keywords) < MAX_KEYWORDS:
            keywords.append(w)

    for cat, title in ai_titles:
        if cat in ranked:
            kw(title)
    for cat, role in role_keywords:
        if cat in ranked:
            kw(role)
    for cat in ranked:
        for word in CATEGORY_KEYWORDS.get(cat, [])[:2]:
            kw(word)
    for name in known_skills(" ".join(skills)):
        if name in SKILL_KEYWORDS and _TECH_CATEGORY.get(name, RoleCategory.OTHER) in ranked:
            kw(SKILL_KEYWORDS[name])

    return TargetPlan(
        categories=[c.value for c in ranked],
        keywords=keywords,
        basis={c.value: basis[c][:6] for c in ranked},
        weights={c.value: round(weight[c], 1) for c in ranked},
    )


def has_it(categories: list[str]) -> bool:
    return any(RoleCategory(c) in IT_CATEGORIES for c in categories if c in RoleCategory._value2member_map_)
