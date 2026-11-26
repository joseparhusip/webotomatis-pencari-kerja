"""Kecocokan CV dengan SATU lowongan (ditempel dari Glints/JobStreet/Kalibrr/…, atau dipilih dari daftar Lowongan).

Sama seperti Skor CV: syarat lowongan dibaca oleh kode (skill, minimal pengalaman, pendidikan, jurusan, bahasa),
dicocokkan dengan CV oleh aturan tetap, baru AI menyimpulkan — dan jawaban AI disaring terhadap CV & lowongan.
"""

import asyncio
import json
import re
import time

from app.core.config import get_settings
from app.services import cv_scan
from app.services.cv_scan import CvScan, _Ctx, _str

MAX_JOB_CHARS = 15000
MIN_JOB_CHARS = 80
VOCAB_TTL = 3600

JOB_GRADES = ((80, "Sangat cocok"), (65, "Cocok"), (45, "Cukup cocok"), (0, "Kurang cocok"))
JOB_DECISIONS = {
    "lamar": "Lamar sekarang",
    "lamar_sesuaikan": "Lamar, tapi sesuaikan CV dulu",
    "jangan_dulu": "Belum disarankan melamar",
}
# (porsi skill lowongan yang ada di CV, skor maksimal) dan (porsi pengalaman yang terpenuhi, skor maksimal)
SKILL_CAP = (0.25, 49)
EXP_CAP = (0.5, 59)

_EDU_LEVELS = (
    (5, "S3", r"\b(s3|doktor|doctor(al)?|ph\.?d)\b"),
    (4, "S2", r"\b(s2|magister|master'?s?|mba)\b"),
    (3, "S1/D4", r"\b(s1|d4|sarjana|bachelor'?s?|strata[\s-]?1|diploma[\s-]?(iv|4)|sarjana terapan)\b"),
    (2, "D3", r"\b(d[123]|diploma|associate)\b"),
    (1, "SMA/SMK", r"\b(sma|smk|slta|stm|ma|high school|sekolah menengah)\b"),
)
_EDU_LINE = re.compile(r"pendidikan|lulusan|education|degree|jurusan|\b(s1|s2|d3|d4|sma|smk|sarjana|diploma|bachelor)\b", re.I)
_MAJOR = re.compile(r"(?:jurusan|major(?:ing)?\s+in|degree\s+in|di\s+bidang|bidang|background\s+in|program\s+studi)\s+"
                    r"([^.\n;:()]{3,140})", re.I)
_MAJOR_SPLIT = re.compile(r"\s*(?:,|/|\bdan\b|\batau\b|\bor\b|\band\b|&)\s*", re.I)
_MAJOR_NOISE = re.compile(r"\b(terkait|related|sejenis|relevan|relevant|lainnya|other|setara|equivalent|field|fields|"
                          r"yang|bidang|lain)\b", re.I)
_ANY_MAJOR = re.compile(r"semua jurusan|segala jurusan|all majors?|any major|jurusan apa (saja|pun)", re.I)
_EXP = (
    re.compile(r"(?:minimal|min\.?|minimum|at\s+least|berpengalaman|pengalaman(?:\s+kerja)?)[^.\n\d]{0,45}?"
               r"(\d{1,2})\s*(?:[-–]\s*\d{1,2}\s*)?\+?\s*(?:tahun|thn|th\b|years?|yrs?)", re.I),
    re.compile(r"(\d{1,2})\s*\+?\s*(?:tahun|years?|yrs?)\s+(?:of\s+)?(?:pengalaman|experience|berpengalaman)", re.I),
)
_FRESH = re.compile(r"fresh\s*-?\s*grad|lulusan\s+baru|tanpa\s+pengalaman|no\s+(?:prior\s+)?experience|"
                    r"tidak\s+(?:harus|wajib)\s+berpengalaman|belum\s+berpengalaman", re.I)
_ENGLISH_REQ = re.compile(r"bahasa\s+inggris|english", re.I)
_ENGLISH_CV = re.compile(r"\b(english|inggris|toefl|ielts|toeic)\b", re.I)
_EN_WORDS = re.compile(r"\b(the|and|with|for|of|to|in|developed|managed|experience)\b", re.I)
_TITLE_NOISE = {"staff", "staf", "senior", "junior", "intern", "internship", "magang", "trainee", "freelance",
                "kontrak", "contract", "part", "time", "full", "fulltime", "parttime", "wfh", "wfo", "remote",
                "hybrid", "untuk", "the", "and", "dan", "team", "tim", "lead", "head", "urgent", "hiring", "lowongan",
                "kerja", "jakarta", "bandung", "surabaya", "bekasi", "tangerang", "depok", "bogor", "officer", "pt", "cv"}

_vocab_cache: tuple[float, list[str]] = (0.0, [])


class JobMatchError(Exception):
    pass


# ---------- membaca lowongan ----------

async def load_vocab(session) -> list[str]:
    """Nama skill yang dicantumkan situs lowongan (kolom skill) di ≥3 lowongan — kosakata nyata dari database."""
    global _vocab_cache
    if time.time() - _vocab_cache[0] < VOCAB_TTL and _vocab_cache[1]:
        return _vocab_cache[1]
    from sqlalchemy import text

    rows = (await session.execute(text(
        "select s, count(*) c from job, unnest(required_skills) s where length(s) between 2 and 30 "
        "group by s having count(*) >= 3 order by c desc limit 400"))).all()
    _vocab_cache = (time.time(), [r[0] for r in rows])
    return _vocab_cache[1]


def _edu_level(text: str) -> tuple[int, str] | None:
    """Jenjang TERENDAH yang disebut (syarat lowongan biasanya 'minimal …')."""
    found = [(lvl, label) for lvl, label, pat in _EDU_LEVELS if re.search(pat, text or "", re.I)]
    return min(found) if found else None


def parse_job(title: str, text: str, vocab: list[str] | None = None, hints: dict | None = None) -> dict:
    """Syarat lowongan dari teks (tanpa AI). hints = data terstruktur dari situs (lowongan hasil scraping)."""
    from app.services.letter_template import skill_key
    from app.services.targeting import _canonical, _clean_skill, classify_role, known_skills
    from app.web.jobs_export import _SOFT_REQ

    hints = hints or {}
    text = (text or "").strip()[:MAX_JOB_CHARS]
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    title = (title or "").strip() or next((ln for ln in lines[:3] if 3 <= len(ln) <= 100), "")

    skills: list[str] = []
    seen: set[str] = set()

    def add(name: str) -> None:
        # "Go / Golang" = "Go"; "React.js" = "React" — satu skill tidak boleh dihitung dua kali.
        parts = [p for p in re.split(r"\s*/\s*", name) if p.strip()]
        name = (next((_canonical(p) for p in parts if _canonical(p)), None) or _clean_skill(name)).strip()
        key = skill_key(re.sub(r"^(microsoft|ms)\s+", "", name, flags=re.I))
        if name and key and key not in seen and len(name) <= 30 and not _SOFT_REQ.search(name):
            seen.add(key)
            skills.append(name)

    for s in hints.get("required_skills") or []:
        add(s)
    for s in known_skills(text):
        add(s)
    for s in vocab or []:
        if re.search(rf"(?<![\w.]){re.escape(s)}(?!\w)", text, re.I):
            add(s)

    exp_years, exp_source = None, ""
    if hints.get("experience_min") is not None:
        exp_years, exp_source = int(hints["experience_min"]), "data situs"
    else:
        for pat in _EXP:
            m = pat.search(text)
            if m and 0 < int(m.group(1)) <= 30:
                exp_years, exp_source = int(m.group(1)), m.group(0).strip()
                break
    fresh_ok = bool(_FRESH.search(text)) or exp_years == 0
    if fresh_ok and exp_years is None:
        exp_years = 0

    edu_text = " ".join([hints.get("education") or ""] + [ln for ln in lines if _EDU_LINE.search(ln)])
    edu = _edu_level(edu_text)
    majors: list[str] = []
    if not _ANY_MAJOR.search(text):
        for ln in lines:
            if not _EDU_LINE.search(ln):
                continue
            for m in _MAJOR.finditer(ln):
                for part in _MAJOR_SPLIT.split(m.group(1)):
                    part = _MAJOR_NOISE.sub(" ", part)
                    part = re.sub(r"\s+", " ", part).strip(" -,.")
                    if 3 <= len(part) <= 40 and part.lower() not in (x.lower() for x in majors):
                        majors.append(part)
    return {
        "title": title[:200],
        "category": classify_role(title, text).value,
        "skills": skills[:25],
        "exp_years": exp_years,
        "exp_source": exp_source,
        "fresh_ok": fresh_ok,
        "edu_level": edu[0] if edu else None,
        "edu_label": edu[1] if edu else "",
        "majors": majors[:8],
        "any_major": bool(_ANY_MAJOR.search(text)),
        "english": bool(_ENGLISH_REQ.search(text)),
        "chars": len(text),
    }


# ---------- mencocokkan dengan CV ----------

def _cv_lines(scan: CvScan) -> list[str]:
    lines = [h for e in scan.experiences for h in (e.get("highlights") or [])]
    lines += [f"{e.get('role', '')} — {e.get('company', '')}".strip(" —") for e in scan.experiences]
    lines += [" ".join(x for x in (p.get("name"), p.get("role"), p.get("description"), " ".join(p.get("tech") or [])) if x)
              for p in scan.projects]
    lines += [ln.strip() for ln in (scan.text or "").splitlines() if len(ln.strip()) >= 15]
    return [re.sub(r"^•\s*", "", ln).strip() for ln in lines if ln and ln.strip()]


def _evidence(skill: str, lines: list[str]) -> str:
    from app.services.targeting import _skill_patterns

    pats = [p for ps in _skill_patterns([skill]).values() for p in ps] or \
        [re.compile(rf"(?<!\w){re.escape(skill)}(?!\w)", re.I)]
    for ln in lines:
        if any(p.search(ln) for p in pats):
            return ln[:180]
    return ""


def _cv_edu(scan: CvScan) -> tuple[int, str] | None:
    parts = [" ".join(str(e.get(k) or "") for k in ("degree", "major", "institution")) for e in scan.education]
    best = max((lv for lv in (_edu_level(p) for p in parts) if lv), default=None)
    if best is None and any(re.search(r"universitas|university|institut|sekolah tinggi", p, re.I) for p in parts):
        best = (3, "S1/D4")
    return best


def match_job(scan: CvScan, job: dict) -> dict:
    from app.services.cv_score import Criterion
    from app.services.cv_targets import derive_targets
    from app.web.jobs_export import _owned_checker
    from app.web.templating import ROLE_LABELS

    cv_skills = list(scan.fields.get("skills") or [])
    owned = _owned_checker(cv_skills)
    lines = _cv_lines(scan)
    low = (scan.text or "").lower()
    ctx = _Ctx(scan.text or "")
    crit: list[Criterion] = []

    # 1. Skill yang diminta
    req_skills = []
    for s in job["skills"]:
        if owned(s):
            status, ev = "list", _evidence(s, lines)
        elif cv_scan._skill_in_text(s, low, ctx.squashed, ctx.words):
            status, ev = "text", _evidence(s, lines)
        else:
            status, ev = "missing", ""
        req_skills.append({"name": s, "status": status, "evidence": ev})
    c = Criterion("job_skills", "job", "Skill yang diminta lowongan", 40)
    if req_skills:
        have = [r for r in req_skills if r["status"] != "missing"]
        miss = [r["name"] for r in req_skills if r["status"] == "missing"]
        only_text = [r["name"] for r in req_skills if r["status"] == "text"]
        c.grade(len(have) / len(req_skills) / 0.8,
                f"{len(have)} dari {len(req_skills)} skill yang disebut lowongan ada di CV." +
                (f" Belum ada: {', '.join(miss[:8])}." if miss else "") +
                (f" Hanya disebut di uraian, belum di daftar skill: {', '.join(only_text[:5])}." if only_text else ""),
                "Tonjolkan skill yang sudah kamu punya dan diminta lowongan (taruh di daftar skill & ringkasan). "
                "Skill yang belum dikuasai jangan dicantumkan — jadikan target belajar.")
    else:
        c.skip("Lowongan tidak menyebut skill/alat kerja tertentu yang bisa dikenali.")
    crit.append(c)

    # 2. Pengalaman
    years = cv_scan.total_experience(scan.experiences, scan.projects)
    months = years["months"]
    cv_text = cv_scan.months_text(months) if months else "0 bulan"
    c = Criterion("job_experience", "job", "Lama pengalaman", 20)
    if job["exp_years"] is None:
        c.skip("Lowongan tidak menyebut minimal pengalaman.")
    elif job["exp_years"] == 0:
        c.grade(1, f"Lowongan menerima fresh graduate / tanpa pengalaman. Pengalamanmu terhitung {cv_text}.")
    else:
        need = job["exp_years"] * 12
        c.grade(months / need, f"Diminta minimal {job['exp_years']} tahun; pengalamanmu terhitung {cv_text} "
                f"(kerja, magang, freelance, dan proyek bertanggal).",
                "Cantumkan semua pengalaman yang relevan beserta tanggalnya (magang, freelance, proyek). "
                "Bila memang kurang, tetap boleh melamar tetapi tonjolkan hasil kerja yang paling mirip dengan tugas lowongan.")
    crit.append(c)

    # 3. Bidang
    plan = derive_targets(scan)
    c = Criterion("job_field", "job", "Bidang pekerjaan sama dengan pengalamanmu", 10)
    if job["category"] == "other":
        c.skip("Bidang lowongan tidak bisa ditentukan dari judul & isinya.")
    else:
        label = ROLE_LABELS.get(job["category"], job["category"])
        cv_labels = ", ".join(ROLE_LABELS.get(x, x) for x in plan.categories[:4]) or "—"
        c.grade(1 if job["category"] in plan.categories else 0,
                f"Lowongan bidang {label}; bidang yang terlihat dari CV: {cv_labels}.",
                "Bila memang ingin pindah bidang, tonjolkan pengalaman/proyek yang paling dekat dengan bidang lowongan ini.")
    crit.append(c)

    # 4. Nama posisi
    words = [w for w in re.findall(r"[A-Za-z][A-Za-z+#.]{2,}", job["title"]) if w.lower().strip(".") not in _TITLE_NOISE]
    c = Criterion("job_title", "job", "Nama posisi muncul di CV", 10)
    if words:
        role_text = " ".join([e.get("role", "") for e in scan.experiences] + [p.get("role", "") for p in scan.projects]
                             + [scan.fields.get("summary") or "", (scan.text or "")[:400]]).lower()
        found = [w for w in words if re.search(rf"(?<!\w){re.escape(w.lower())}", role_text)]
        c.grade(len(found) / len(words), f"Kata kunci posisi \"{job['title']}\": " +
                (f"ditemukan {', '.join(found)}" if found else "tidak ada") + " di posisi/ringkasan CV." +
                (f" Belum ada: {', '.join(w for w in words if w not in found)}." if len(found) < len(words) else ""),
                "Sesuaikan judul di bawah nama dan ringkasan dengan posisi yang dilamar — selama sesuai pengalamanmu.")
    else:
        c.skip("Nama posisi tidak terbaca.")
    crit.append(c)

    # 5. Pendidikan
    cv_edu = _cv_edu(scan)
    c = Criterion("job_education", "job", "Jenjang pendidikan", 10)
    if not job["edu_level"]:
        c.skip("Lowongan tidak menyebut jenjang pendidikan.")
    elif not cv_edu:
        c.grade(0, f"Diminta minimal {job['edu_label']}; jenjang pendidikan tidak terbaca di CV.",
                "Tulis jenjang & jurusan dengan jelas di bagian Pendidikan (mis. \"S1 Manajemen\").")
    else:
        c.grade(1 if cv_edu[0] >= job["edu_level"] else 0,
                f"Diminta minimal {job['edu_label']}; CV-mu {cv_edu[1]}.",
                "Jenjang di bawah syarat: pertimbangkan lowongan lain, atau tonjolkan sertifikasi & pengalaman setara.")
    crit.append(c)

    # 6. Jurusan
    c = Criterion("job_major", "job", "Jurusan sesuai", 5)
    edu_text = " ".join(" ".join(str(e.get(k) or "") for k in ("degree", "major")) for e in scan.education).lower()
    if job["any_major"]:
        c.grade(1, "Lowongan menerima semua jurusan.")
    elif not job["majors"]:
        c.skip("Lowongan tidak menyebut jurusan tertentu.")
    else:
        hit = [m for m in job["majors"] if any(w in edu_text for w in re.findall(r"[a-z]{4,}", m.lower()))]
        c.grade(1 if hit else 0, f"Jurusan yang diminta: {', '.join(job['majors'])}. " +
                (f"Cocok: {', '.join(hit)}." if hit else "Jurusan di CV tidak termasuk."),
                "Bila jurusan berbeda, tonjolkan pengalaman/sertifikasi yang relevan dengan bidang lowongan.")
    crit.append(c)

    # 7. Bahasa Inggris
    c = Criterion("job_english", "job", "Bahasa Inggris", 5)
    if not job["english"]:
        c.skip("Lowongan tidak menyebut bahasa Inggris.")
    elif _ENGLISH_CV.search(scan.text or ""):
        c.grade(1, "Lowongan menyebut bahasa Inggris; CV mencantumkan kemampuan/tes bahasa Inggris.")
    elif len(_EN_WORDS.findall(scan.text or "")) >= 40:
        c.grade(0.6, "Lowongan menyebut bahasa Inggris; CV ditulis dalam bahasa Inggris tetapi tidak menyebut tingkat kemampuannya.",
                "Tulis tingkat bahasa Inggris (mis. \"English — aktif\" atau skor TOEFL/IELTS bila ada).")
    else:
        c.grade(0, "Lowongan menyebut bahasa Inggris; CV tidak menyebut kemampuan bahasa Inggris.",
                "Bila kamu bisa berbahasa Inggris, cantumkan di bagian Skill/Bahasa beserta tingkatnya.")
    crit.append(c)

    rated = [x for x in crit if x.status != "na"]
    got, full = sum(x.points for x in rated), sum(x.max for x in rated)
    raw = round(got / full * 100) if full else 0
    score, cap, cap_reason = raw, None, ""
    skill_c, exp_c = crit[0], crit[1]
    if skill_c.status != "na" and len(req_skills) >= 3:
        share = sum(r["status"] != "missing" for r in req_skills) / len(req_skills)
        if share < SKILL_CAP[0] and score > SKILL_CAP[1]:
            score, cap = SKILL_CAP[1], SKILL_CAP[1]
            cap_reason = f"baru {round(share * 100)}% skill yang diminta lowongan ada di CV-mu"
    if exp_c.status not in ("na",) and job["exp_years"]:
        share = months / (job["exp_years"] * 12)
        if share < EXP_CAP[0] and score > EXP_CAP[1]:
            score, cap = EXP_CAP[1], EXP_CAP[1]
            cap_reason = (cap_reason + " dan " if cap_reason else "") + \
                f"pengalamanmu ({cv_text}) belum separuh dari yang diminta ({job['exp_years']} tahun)"
    return {
        "job": job,
        "score": score, "raw_score": raw, "cap": cap, "cap_reason": cap_reason,
        "grade": next(label for limit, label in JOB_GRADES if score >= limit),
        "assessed_max": full,
        "criteria": [x.__dict__ for x in crit],
        "skills": req_skills,
        "cv_months": months,
    }


# ---------- penjelasan & kesimpulan ----------

JOB_WHY = {
    "job_skills": "Sebagian skill yang diminta lowongan belum terlihat di CV-mu — sistem rekrutmen dan HR mencari "
                  "kata kunci ini lebih dulu.",
    "job_experience": "Pengalamanmu masih di bawah yang diminta, jadi HR mungkin meragukan kesiapanmu.",
    "job_field": "Pengalamanmu ada di bidang lain, jadi HR harus menebak apakah kemampuanmu bisa dipakai di sini.",
    "job_title": "Nama posisi yang dilamar tidak muncul di CV, jadi CV-mu tidak langsung terlihat cocok saat disaring.",
    "job_education": "Jenjang pendidikan belum memenuhi syarat atau tidak terbaca di CV.",
    "job_major": "Jurusanmu tidak termasuk yang disebut lowongan.",
    "job_english": "Lowongan menyebut bahasa Inggris, tetapi CV belum menunjukkan kemampuanmu.",
}
JOB_MEANING = {
    "Sangat cocok": "CV-mu sangat sesuai dengan lowongan ini. Layak segera dilamar.",
    "Cocok": "CV-mu sesuai dengan sebagian besar syarat. Layak dilamar, apalagi bila beberapa hal di bawah ditonjolkan.",
    "Cukup cocok": "Sebagian syarat terpenuhi, sebagian belum. Masih bisa dicoba, tapi sesuaikan CV dulu.",
    "Kurang cocok": "Banyak syarat utama yang belum terpenuhi. Peluangnya kecil — lebih baik fokus ke lowongan yang lebih sesuai.",
}


def job_explain(jm: dict) -> dict:
    from app.services.cv_score import _num

    crit = jm["criteria"]
    rated = [c for c in crit if c["status"] != "na"]
    got = sum(c["points"] for c in rated)
    full = sum(c["max"] for c in rated) or 1
    score, raw = jm["score"], jm.get("raw_score", jm["score"])
    story = [f"Kecocokan {score} dari 100 ({jm['grade']}). {JOB_MEANING.get(jm['grade'], '')}"]
    if jm.get("cap") is not None and raw > score:
        story.append(f"Dari semua syarat nilainya {raw}, tetapi dibatasi menjadi {score} karena {jm['cap_reason']}.")
    math = f"{_num(got)} dari {full} poin syarat yang bisa dinilai: {_num(got)} ÷ {full} × 100 = {raw}."
    if jm.get("cap") is not None and raw > score:
        math += f" Lalu dibatasi menjadi {score}."
    na = [c["label"] for c in crit if c["status"] == "na"]
    if na:
        math += f" Tidak dinilai karena tidak disebut lowongan: {', '.join(na)}."
    losses = sorted(({"key": c["key"], "label": c["label"], "lost": round(c["max"] - c["points"], 1),
                      "why": JOB_WHY.get(c["key"], ""), "detail": c["detail"]}
                     for c in rated if c["max"] - c["points"] > 0.05), key=lambda x: -x["lost"])
    strengths = [s for s in jm["skills"] if s["status"] != "missing"]
    gaps = [s["name"] for s in jm["skills"] if s["status"] == "missing"]
    return {"story": story, "math": math, "losses": losses, "strengths": strengths, "gaps": gaps}


def allowed_job_decisions(score: int) -> list[str]:
    if score >= 75:
        return ["lamar", "lamar_sesuaikan"]
    if score >= 50:
        return ["lamar_sesuaikan"]
    return ["jangan_dulu", "lamar_sesuaikan"] if score >= 40 else ["jangan_dulu"]


def basic_job_review(jm: dict) -> dict:
    ex = job_explain(jm)
    highlights = [{"requirement": s["name"], "text": f"Kamu punya {s['name']} — tonjolkan di ringkasan & daftar skill.",
                   "cv_line": s["evidence"]} for s in ex["strengths"][:5]]
    adjustments = [{"criterion": c["key"], "text": c["fix"], "before": "", "after": ""}
                   for c in sorted((c for c in jm["criteria"] if c["status"] in ("partial", "miss") and c["fix"]),
                                   key=lambda c: c["points"] - c["max"])[:4]]
    return {"verdict": " ".join(ex["story"]), "decision": allowed_job_decisions(jm["score"])[0],
            "highlights": highlights, "adjustments": adjustments, "ai": ""}


_PROMPT = """Kamu konsultan karier di Indonesia. Sistem SUDAH membaca CV dan lowongan, lalu mencocokkan syaratnya
dengan aturan tetap. Tugasmu: menyimpulkan apakah CV ini layak dikirim ke lowongan ini, dan apa yang perlu ditonjolkan
atau disesuaikan.

ATURAN KERAS:
- Skor kecocokan sudah final ({score}/100, {grade}). Jangan menyebut skor lain.
- Jangan mengarang. Setiap angka harus tertulis di CV, LOWONGAN, atau LAPORAN. Jangan menyarankan mencantumkan skill
  atau pengalaman yang tidak ada di CV — skill yang belum ada disebut sebagai target belajar, bukan untuk ditulis.
- highlights: maksimal 5 syarat lowongan yang SUDAH dipenuhi CV. "cv_line" = salinan PERSIS satu kalimat/poin dari CV
  sebagai buktinya (kosongkan bila tidak ada).
- adjustments: maksimal 4, hanya untuk kriteria berstatus "partial"/"miss" (pakai "key"-nya). "before" = salinan PERSIS
  kalimat dari CV, "after" = kalimat itu disesuaikan dengan bahasa lowongan, TANPA menambah skill/aktivitas/hasil/angka
  baru (pakai penanda [jumlah]/[%] bila perlu). Kosongkan before/after bila tidak ada kalimat yang cocok.
- decision salah satu dari: {decisions}.
- Bahasa Indonesia yang wajar. verdict 2–4 kalimat, sebut nama posisinya."""

_S = {"type": "string"}
_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": _S, "decision": _S,
        "highlights": {"type": "array", "items": {"type": "object", "properties": {
            "requirement": _S, "text": _S, "cv_line": _S}, "required": ["requirement", "text"]}},
        "adjustments": {"type": "array", "items": {"type": "object", "properties": {
            "criterion": _S, "text": _S, "before": _S, "after": _S}, "required": ["criterion", "text"]}},
    },
    "required": ["verdict", "decision", "highlights", "adjustments"],
}


def _for_ai(jm: dict) -> dict:
    return {"posisi": jm["job"]["title"], "score": jm["score"], "grade": jm["grade"],
            "skor_sebelum_dibatasi": jm.get("raw_score"), "alasan_dibatasi": jm.get("cap_reason") or "",
            "criteria": [{k: c[k] for k in ("key", "label", "status", "points", "max", "detail")} for c in jm["criteria"]],
            "skill_lowongan": [{"name": s["name"], "ada_di_cv": s["status"] != "missing"} for s in jm["skills"]]}


def check_job_review(data: dict, jm: dict, cv_text: str, job_text: str) -> dict:
    from app.services.cv_score import _rewrite_ok

    base = basic_job_review(jm)
    from app.services.cv_score import facts_text

    ctx = _Ctx(cv_text + "\n" + job_text + "\n" + facts_text(jm))
    cv_ctx = _Ctx(cv_text)
    ok = lambda s: bool(s) and ctx.numbers_ok(s)  # noqa: E731
    status = {c["key"]: c["status"] for c in jm["criteria"]}
    missing = {s["name"].lower() for s in jm["skills"] if s["status"] == "missing"}
    job_skills = [s["name"] for s in jm["skills"]]

    verdict = _str(data.get("verdict"))
    verdict = verdict if ok(verdict) else ""
    decision = _str(data.get("decision"))
    if decision not in allowed_job_decisions(jm["score"]) or not verdict:
        decision = base["decision"]  # kalimat & label keputusan harus dari sumber yang sama
    highlights = []
    for h in data.get("highlights") or []:
        h = h or {}
        req, msg, line = _str(h.get("requirement")), _str(h.get("text")), _str(h.get("cv_line"))
        if not msg or not ok(msg) or req.lower() in missing:
            continue                                       # "kekuatan" yang ternyata belum ada di CV → dibuang
        if line and not (len(line) >= 10 and cv_ctx.mentions(line)):
            line = ""
        highlights.append({"requirement": req, "text": msg, "cv_line": line})
    adjustments, seen = [], set()
    for a in data.get("adjustments") or []:
        a = a or {}
        key, msg = _str(a.get("criterion")), _str(a.get("text"))
        if status.get(key) not in ("partial", "miss") or key in seen or not ok(msg):
            continue
        before, after = _str(a.get("before")), _str(a.get("after"))
        if not before or len(before) < 10 or not cv_ctx.mentions(before) or \
                not _rewrite_ok(after, cv_text, ctx, cv_ctx, job_skills, before):
            before = after = ""
        seen.add(key)
        adjustments.append({"criterion": key, "text": msg, "before": before, "after": after})
    for p in base["adjustments"]:
        if len(adjustments) >= 4:
            break
        if p["criterion"] not in seen:
            adjustments.append(p)
    return {"verdict": verdict or base["verdict"], "decision": decision,
            "highlights": highlights[:5] or base["highlights"], "adjustments": adjustments[:4]}


async def ai_job_review(jm: dict, cv_text: str, job_text: str) -> dict:
    s = get_settings()
    if not s.ai_ready:
        raise JobMatchError("AI belum aktif")
    prompt = (_PROMPT.format(score=jm["score"], grade=jm["grade"],
                             decisions=", ".join(f'"{d}" ({JOB_DECISIONS[d]})' for d in allowed_job_decisions(jm["score"])))
              + "\n\nLAPORAN:\n" + json.dumps(_for_ai(jm), ensure_ascii=False)
              + "\n\nLOWONGAN:\n<lowongan>\n" + job_text[:MAX_JOB_CHARS] + "\n</lowongan>"
              + "\n\nTEKS CV:\n<cv>\n" + cv_text[:25000] + "\n</cv>")
    call = cv_scan._gemini(prompt, _SCHEMA) if s.ai_provider == "gemini" else cv_scan._claude(prompt, _SCHEMA)
    data = cv_scan._parse_json(await asyncio.wait_for(call, cv_scan.AI_TIMEOUT_SECONDS))
    out = check_job_review(data, jm, cv_text, job_text)
    out["ai"] = s.ai_provider or ""
    return out


def job_from_db(job) -> tuple[str, str, dict]:
    """(judul, teks, hints) dari lowongan hasil scraping."""
    parts = [job.title, job.company_name, job.description or "", job.requirements or ""]
    if job.required_skills:
        parts.append("Skill: " + ", ".join(job.required_skills))
    if job.education:
        parts.append("Pendidikan: " + job.education)
    hints = {"required_skills": list(job.required_skills or []), "experience_min": job.experience_min,
             "education": job.education or ""}
    return job.title, "\n".join(p for p in parts if p), hints
