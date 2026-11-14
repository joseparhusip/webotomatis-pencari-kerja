"""Fungsi murni untuk membersihkan data mentah dari sumber lowongan."""

import hashlib
import re

from selectolax.parser import HTMLParser

from app.models.enums import EmploymentType

_NUM = re.compile(r"\d[\d.,]*")
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_COMPANY_NOISE = re.compile(r"\b(pt|cv|tbk|persero|ud|inc|ltd|co)\b\.?", re.I)
_NON_ALNUM = re.compile(r"[^a-z0-9]+")

# Domain yang bukan email HR perusahaan (milik portal lowongan).
_PORTAL_EMAIL_DOMAINS = ("kalibrr.com", "jobstreet.com", "seek.com.au", "glints.com")


def parse_salary_label(label: str | None) -> tuple[int | None, int | None]:
    """'Rp 10.000.000 – Rp 15.000.000 per month' -> (10000000, 15000000), dinormalisasi ke per bulan."""
    if not label:
        return None, None
    text = label.lower().replace("\xa0", " ")
    multiplier = 1_000_000 if re.search(r"\b(jt|juta)\b", text) else 1
    values: list[int] = []
    for raw in _NUM.findall(text):
        digits = re.sub(r"[.,](?=\d{3}\b)", "", raw)  # pemisah ribuan
        digits = digits.replace(",", ".")
        try:
            values.append(int(float(digits) * multiplier))
        except ValueError:
            continue
    values = [v for v in values if v >= 100_000]
    if not values:
        return None, None
    if any(k in text for k in ("per year", "per tahun", "/year", "annually")):
        values = [v // 12 for v in values]
    elif any(k in text for k in ("per hour", "per jam", "per day", "per hari")):
        return None, None
    return min(values), max(values)


def parse_employment_type(text: str | None) -> EmploymentType | None:
    t = (text or "").lower()
    if not t:
        return None
    if "intern" in t or "magang" in t:
        return EmploymentType.INTERNSHIP
    if "kontrak" in t or "contract" in t:
        return EmploymentType.CONTRACT
    if "freelance" in t or "lepas" in t:
        return EmploymentType.FREELANCE
    if "part" in t or "paruh" in t:
        return EmploymentType.PARTTIME
    if "full" in t or "penuh" in t or "permanent" in t or "tetap" in t:
        return EmploymentType.FULLTIME
    return None


def split_location(label: str | None) -> tuple[str | None, str | None]:
    """'Jakarta Barat, Jakarta Raya' -> ('Jakarta Barat', 'Jakarta Raya')."""
    if not label:
        return None, None
    parts = [p.strip() for p in label.split(",") if p.strip()]
    if not parts:
        return None, None
    return parts[0], (parts[-1] if len(parts) > 1 else None)


_DIRECTION = {"South": "Selatan", "North": "Utara", "West": "Barat", "East": "Timur", "Central": "Pusat"}
_PLACE_EN = re.compile(r"^(South|North|West|East|Central)\s+(.+)$")


def localize_place(name: str | None) -> str | None:
    """'South Jakarta' -> 'Jakarta Selatan', 'West Java' -> 'Jawa Barat' (samakan dengan JobStreet)."""
    if not name:
        return None
    name = name.strip().replace("Java", "Jawa").replace("Sumatra", "Sumatera")
    m = _PLACE_EN.match(name)
    return f"{m.group(2)} {_DIRECTION[m.group(1)]}" if m else name


def html_to_text(html: str | None) -> str | None:
    if not html:
        return None
    tree = HTMLParser(html)
    for li in tree.css("li"):
        li.insert_before("\n• ")
    for br in tree.css("br"):
        br.insert_before("\n")
    for block in tree.css("p, div, ul, ol, h1, h2, h3, h4"):
        block.insert_after("\n")
    text = tree.text(separator="")
    text = text.replace("﻿", "").replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    text = re.sub(r"\n\s*\n(?=• )", "\n", text)  # item daftar tidak dipisah baris kosong
    return text.strip() or None


def extract_hr_email(*texts: str | None) -> str | None:
    for text in texts:
        for match in _EMAIL.findall(text or ""):
            email = match.rstrip(".").lower()
            if not email.endswith(_PORTAL_EMAIL_DOMAINS):
                return email
    return None


def normalize_company(name: str) -> str:
    return _NON_ALNUM.sub(" ", _COMPANY_NOISE.sub(" ", name.lower())).strip()


def normalize_text(value: str | None) -> str:
    return _NON_ALNUM.sub(" ", (value or "").lower()).strip()


def dedup_hash(company: str, title: str, city: str | None) -> str:
    key = "|".join((normalize_company(company), normalize_text(title), normalize_text(city)))
    return hashlib.sha1(key.encode()).hexdigest()


# ---------- Halaman detail ----------

def render_draftjs(raw: str | None) -> str | None:
    """Deskripsi Glints (Draft.js JSON) -> teks dengan daftar berpoin/bernomor seperti di situs."""
    import json

    if not raw:
        return None
    try:
        blocks = json.loads(raw).get("blocks", [])
    except (ValueError, AttributeError):
        return None
    lines: list[str] = []
    counter = 0
    for b in blocks:
        kind, text = b.get("type", "unstyled"), (b.get("text") or "").rstrip()
        indent = "  " * int(b.get("depth") or 0)
        if kind == "ordered-list-item":
            counter += 1
            lines.append(f"{indent}{counter}. {text}")
            continue
        counter = 0
        if kind == "unordered-list-item":
            lines.append(f"{indent}• {text}")
        elif kind.startswith("header"):
            lines += ["", text]
        else:
            lines.append(text)
    out = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
    return out or None


# Label pendidikan mengikuti istilah yang dipakai situs lowongan Indonesia.
_EDUCATION = {
    "HIGH_SCHOOL": "Minimal SMA/SMK", "high school": "Minimal SMA/SMK",
    "VOCATIONAL": "Minimal SMK", "DIPLOMA": "Minimal Diploma (D3)", "associate degree": "Minimal Diploma (D3)",
    "BACHELOR_DEGREE": "Minimal Sarjana (S1)", "bachelor degree": "Minimal Sarjana (S1)",
    "MASTER_DEGREE": "Minimal Magister (S2)", "postgraduate degree": "Minimal Magister (S2)",
    "DOCTORATE": "Minimal Doktor (S3)", "DOCTORAL_DEGREE": "Minimal Doktor (S3)",
}


def education_label(code: str | None) -> str | None:
    if not code:
        return None
    return _EDUCATION.get(code) or _EDUCATION.get(code.lower())


def split_csv(value: str | None) -> list[str]:
    return [s.strip() for s in (value or "").split(",") if s.strip()]
