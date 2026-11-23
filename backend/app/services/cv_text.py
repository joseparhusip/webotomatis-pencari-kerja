"""Membaca struktur CV dari teks PDF tanpa AI (cadangan saat AI tidak aktif / sedang gangguan).

Hanya menyalin yang tertulis: ringkasan, pengalaman (perusahaan, posisi, tanggal, isi tugas), proyek, dan
daftar skill. Tidak menerjemahkan, tidak merangkum, tidak menebak. Format CV yang umum dikenali:

    Perusahaan - Lokasi            Sep 2020 - Dec 2022      (posisi di baris berikutnya)
    Posisi, Perusahaan             Oct 2026 – Present
    Nama Proyek | Peran            Jul 2026 – Aug 2026
"""

import re

_MON = (r"(?:jan(?:uari|uary)?|feb(?:ruari|ruary)?|mar(?:et|ch)?|apr(?:il)?|mei|may|jun(?:i|e)?|jul(?:i|y)?|"
        r"agu(?:stus)?|agt|aug(?:ust)?|sep(?:t(?:ember)?)?|okt(?:ober)?|oct(?:ober)?|nov(?:ember)?|nop(?:ember)?|"
        r"des(?:ember)?|dec(?:ember)?)\.?")
_DATE = rf"(?:{_MON}\s+)?(?:19|20)\d{{2}}"
_END = rf"(?:{_DATE}|present|sekarang|now|current|saat ini|hingga kini)"
_RANGE = re.compile(rf"\s*\(?({_DATE})\s*[-–—]+\s*({_END})\)?\s*$", re.I)

_HEADERS = {
    "experience": r"(pengalaman( kerja| profesional| magang)?|work experiences?|experiences?|professional experiences?|"
                  r"employment( history)?|riwayat (pekerjaan|kerja)|internship experiences?|career history)",
    "projects": r"(projects?|proyek|project experiences?|portfolio projects?|personal projects?)",
    "education": r"(pendidikan|education|riwayat pendidikan)",
    "summary": r"(profile|profil|summary|ringkasan|about me|tentang saya|professional summary)",
    "skills": r"(skills?|core skills|keahlian|kemampuan|technical skills|hard skills)",
    "other": r"(pengalaman organisasi|organi[sz]ational experiences?|organisasi|organization|"
             r"certifications?( & skills| and skills)?|sertifikasi|awards?( & certifications)?|penghargaan|"
             r"portfolio|languages?|bahasa|references?|referensi|interests?|minat|volunteer(ing)?|courses?|"
             r"training|pelatihan|achievements?|prestasi)",
}
_HEADER_RE = {k: re.compile(rf"^\s*{v}\s*:?\s*$", re.I) for k, v in _HEADERS.items()}
_ROLE_WORD = re.compile(r"\b(developer|engineer|programmer|intern|magang|officer|staff|staf|analyst|analis|manager|"
                        r"designer|admin|assistant|asisten|specialist|consultant|teacher|guru|marketing|sales|"
                        r"executive|supervisor|lead|coordinator|koordinator|kasir|cashier|operator|teknisi|"
                        r"technician|accountant|akuntan|editor|writer|creator|host|representative|associate)\b", re.I)
_COMPANY_BLURB = re.compile(r"\b(is an? (\w+ ){0,3}(company|institution|firm|organi[sz]ation|startup|bank|group)|"
                            r"adalah (sebuah )?(perusahaan|lembaga|institusi|bank))\b", re.I)
_CONTACT = re.compile(r"@|https?://|www\.|linkedin|github|\+?\d[\d\s().-]{8,}|\|")


# Bullet di awal baris: simbol asli, atau hasil OCR yang salah baca "•" menjadi "e"/"o"/"«"/"©" (diikuti huruf kapital).
_BULLET = re.compile(r"^\s*(?:[•\-–▪●◦*·]|(?:[eo«»©°]|eo)(?=\s+[A-Z(]))\s+")


def normalize(text: str) -> str:
    """Rapikan teks hasil PDF/OCR: bullet jadi '• ', spasi berlebih dibuang."""
    lines = []
    for raw in (text or "").splitlines():
        line = re.sub(r"[ \t]+", " ", raw).rstrip()
        if _BULLET.match(line):
            line = "• " + _BULLET.sub("", line)
        lines.append(line)
    return "\n".join(lines)


def section_of(line: str) -> str | None:
    s = line.strip()
    if not s or len(s) > 45:
        return None
    # "Pengalaman organisasi" dicek sebelum "Pengalaman".
    for key in ("other", "experience", "projects", "education", "summary", "skills"):
        if _HEADER_RE[key].match(s):
            return key
    return None


def split_sections(text: str) -> dict[str, list[str]]:
    """{'_head': baris sebelum judul pertama, 'experience': [...], ...}"""
    out: dict[str, list[str]] = {"_head": []}
    current = "_head"
    for raw in normalize(text).splitlines():
        line = raw.rstrip()
        key = section_of(line)
        if key:
            current = key
            out.setdefault(current, [])
            continue
        out.setdefault(current, []).append(line)
    return out


def _join_wrapped(lines: list[str]) -> list[str]:
    """Baris yang terpotong lebar halaman disambung; kalimat baru dimulai setelah titik atau bullet."""
    out: list[str] = []
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        bullet = bool(re.match(r"^[•\-–▪●◦*]\s+", line))
        line = re.sub(r"^[•\-–▪●◦*]\s+", "", line)
        if out and not bullet and not out[-1].endswith((".", ":", "!", "?")):
            out[-1] = f"{out[-1]} {line}"
        else:
            out.append(line)
    return out


def _entries(lines: list[str]) -> list[tuple[str, str, str, list[str]]]:
    """[(judul, mulai, selesai, baris isi)] — entri baru dimulai di baris yang diakhiri rentang tanggal."""
    entries: list[tuple[str, str, str, list[str]]] = []
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        m = _RANGE.search(line)
        head = line[: m.start()].strip(" -–|,") if m else ""
        if m and head:
            entries.append((head, m.group(1).strip(), m.group(2).strip(), []))
        elif entries:
            entries[-1][3].append(line)
    return entries


def parse_experiences(lines: list[str]) -> list[dict]:
    out = []
    for head, start, end, body in _entries(lines):
        company = location = role = ""
        if re.search(r"\s[-–]\s", head):
            company, location = [p.strip() for p in re.split(r"\s+[-–]\s+", head, maxsplit=1)]
        elif "," in head:
            first, rest = [p.strip() for p in head.split(",", 1)]
            if _ROLE_WORD.search(first) and not _ROLE_WORD.search(rest.split(",")[0]):
                role, company = first, rest
            else:
                company = head
        elif " at " in head.lower() or " di " in head.lower():
            role, company = [p.strip() for p in re.split(r"\s+(?:at|di)\s+", head, maxsplit=1, flags=re.I)]
        else:
            company = head
        body = list(body)
        if not role and body:
            first = re.sub(r"^•\s*", "", body[0].strip())
            m = re.match(r"^(.{3,60}?)\s+[-–]\s+(.+)$", first)
            if m and _ROLE_WORD.search(m.group(1)):              # "Back Office Intern - Printed and verified ..."
                role, body[0] = m.group(1).strip(), m.group(2).strip()
            elif len(first) <= 70 and not first.endswith(".") and _ROLE_WORD.search(first):
                role, body = first, body[1:]
        highlights = [h for h in _join_wrapped(body) if len(h) >= 15 and not _COMPANY_BLURB.search(h)]
        if company or role:
            out.append({"company": company[:120], "role": role[:120], "employment_type": "", "location": location[:80],
                        "start": start, "end": end, "highlights": highlights[:8]})
    return out[:12]


def parse_projects(lines: list[str]) -> list[dict]:
    out = []
    for head, start, end, body in _entries(lines):
        name, role = (head.split("|", 1) + [""])[:2] if "|" in head else (head, "")
        desc = _join_wrapped(body)
        out.append({"name": name.strip(" :")[:120], "role": role.strip()[:80], "tech": [],
                    "description": desc[0][:300] if desc else "", "url": "", "start": start, "end": end})
    return out[:12]


_DEGREE = re.compile(r"\b(bachelor|sarjana|s1|s2|s3|d1|d2|d3|d4|diploma|associate|master|magister|doctor|doktor|"
                     r"sma|smk|ma|high school|vocational)\b", re.I)
_DEGREE_CODE = {"bachelor": "S1", "sarjana": "S1", "s1": "S1", "master": "S2", "magister": "S2", "s2": "S2",
                "doctor": "S3", "doktor": "S3", "s3": "S3", "diploma": "D3", "associate": "D3"}
_GPA = re.compile(r"(?:(?:gpa|ipk)\s*:?\s*)?(\d[.,]\d{1,2})\s*/\s*4(?:[.,]0{1,2})?|(?:gpa|ipk)\s*:?\s*(\d[.,]\d{1,2})", re.I)


def parse_education(lines: list[str]) -> list[dict]:
    """'Universitas X - Kota  Aug 2020 - Jun 2024' + 'Bachelor of Computer Science, 3.76/4.00',
    atau 'Bachelor of Digital Business, Universitas Y  2022 – 2026' + 'GPA: 3.75/4.00'."""
    out = []
    for head, start, end, body in _entries(lines):
        institution, degree_text = head, ""
        if "," in head and _DEGREE.search(head.split(",")[0]):
            degree_text, institution = [p.strip() for p in head.split(",", 1)]
        elif re.search(r"\s[-–]\s", head):
            institution = re.split(r"\s+[-–]\s+", head, maxsplit=1)[0].strip()
        if not degree_text and body and _DEGREE.search(body[0]):
            degree_text = body[0].strip()
        gpa_m = _GPA.search(" ".join([head] + body[:3]))
        gpa = (gpa_m.group(1) or gpa_m.group(2)) if gpa_m else ""
        degree_text = _GPA.sub("", degree_text).strip(" ,;-")
        d = _DEGREE.search(degree_text)
        degree = _DEGREE_CODE.get(d.group(1).lower(), d.group(1).upper()) if d else ""
        major = re.sub(rf"^.*?\b{re.escape(d.group(1))}\b\s*(of|in|bidang|jurusan)?\s*", "", degree_text, flags=re.I) \
            if d else degree_text
        out.append({"institution": institution[:150], "degree": degree, "major": major.strip(" ,")[:120],
                    "start": start, "end": end, "gpa": gpa})
    return out[:6]


_TITLE_SUFFIX = re.compile(r",?\s*\b(s\.?\s?(kom|t|e|h|pd|si|ak|ip|sos|ked|farm|psi|i\.?kom|tr\.?\w*)|"
                           r"a\.?\s?md\.?\w*|m\.?\s?(kom|t|m|sc|si|ba|pd)|b\.?\s?(sc|a|eng)|ph\.?d|mba|ir|dr|drs|dra)\.?\b\.?",
                           re.I)
_NOT_NAME = re.compile(r"curriculum|vitae|resume|\bcv\b|profil|profile|summary|ringkasan|contact|kontak|"
                       r"data diri|developer|engineer|analyst|designer|manager|staff|marketing|specialist|intern|"
                       r"\bat\b|@|https?://|www\.|\d", re.I)


def parse_name(text: str) -> str:
    """Nama di bagian atas CV: baris pendek pertama yang bukan judul/kontak/jabatan. Gelar (S.Kom, A.Md) dibuang."""
    for raw in normalize(text).splitlines()[:8]:
        line = re.sub(r"^•\s*", "", raw.strip())
        if not line or section_of(line):
            continue
        cleaned = _TITLE_SUFFIX.sub("", line).strip(" ,.-|")
        words = cleaned.split()
        if (1 <= len(words) <= 5 and not _NOT_NAME.search(cleaned) and len(cleaned) <= 60
                and all(re.fullmatch(r"[A-Za-zÀ-ÿ'.\-]+", w) for w in words)):
            return cleaned.title() if cleaned.isupper() else cleaned
    return ""


_ADDRESS_LABEL = re.compile(r"^\s*(?:alamat|domisili|address|location|lokasi|tempat tinggal)\s*:\s*(.+)$", re.I | re.M)


def parse_address(text: str) -> str:
    """Domisili: baris 'Alamat/Domisili/Location: …', atau potongan baris kontak yang merupakan kota/kab/provinsi
    Indonesia (dicek ke data wilayah Kemendagri), mis. 'Bekasi, Jawa Barat · email · HP'."""
    from app.services.targeting import home_region

    m = _ADDRESS_LABEL.search(text or "")
    if m:
        return m.group(1).strip()[:120]
    head = normalize(text).splitlines()[:10]
    for line in head:
        if section_of(line):
            break
        for part in re.split(r"\s*[|·•●▪/]\s*|\s{2,}", line):
            part = part.strip(" ,.-")
            if (not part or len(part) > 60 or re.search(r"@|https?://|www\.|linkedin|github|\d{4,}|\+\d", part)):
                continue
            if home_region(part):
                return part
    return ""


def parse_summary(sections: dict[str, list[str]]) -> str:
    """Bagian 'Profile/Summary', atau paragraf di bawah kontak sebelum judul pertama."""
    lines = sections.get("summary") or []
    if not lines:
        head = [ln.strip() for ln in sections.get("_head", []) if ln.strip()]
        started = False
        for ln in head[1:]:                      # baris 0 = nama
            if _CONTACT.search(ln):
                continue
            if not started and len(ln) <= 30:    # judul singkat di bawah nama (mis. "Backend Developer")
                continue
            started = True                       # paragraf ringkasan: ambil sampai judul bagian pertama
            lines.append(ln)
    text = " ".join(_join_wrapped(lines)).strip()
    return text[:900] if len(text) >= 60 else ""


def _skill_section_items(lines: list[str]) -> list[str]:
    """Bagian Skills: baris yang terpotong di dalam kurung disambung; 'Kelompok (a, b & c)' → 'a, b, c'."""
    items: list[str] = []
    for raw in lines:
        line = re.sub(r"^•\s*", "", raw.strip())
        if not line:
            continue
        if items and items[-1].count("(") > items[-1].count(")"):
            # "multi-" + "tenancy)" → "multi-tenancy)" (kata yang terpotong tanda hubung di akhir baris)
            items[-1] = f"{items[-1]}{line}" if items[-1].endswith("-") else f"{items[-1]} {line}"
        else:
            items.append(line)
    out = []
    for item in items:
        m = re.match(r"^[^()]{2,60}\((.+)\)\s*$", item)
        out.append(re.sub(r"\s+&\s+", ", ", m.group(1)) if m else item)
    return out


def parse_skill_list(text: str, sections: dict[str, list[str]]) -> list[str]:
    """Daftar 'Skills: a, b, c' (bisa beberapa baris) atau isi bagian Skills, dipisah koma."""
    chunks: list[str] = []
    m = re.search(r"(?im)^\s*[•\-]?\s*(?:skills?|keahlian|kemampuan|hard skills?|technical skills?)\s*:\s*"
                  r"(.+(?:\n(?!\s*•|\s*\S+\s*:).+){0,4})", normalize(text))
    if m:
        chunks.append(re.sub(r"\s*\n\s*", " ", m.group(1)))  # daftar berkoma yang terpotong lebar halaman
    chunks += _skill_section_items(sections.get("skills") or [])
    out: list[str] = []
    for chunk in chunks:
        # Pisah di koma/titik koma yang TIDAK di dalam kurung: "Microsoft Excel (Advanced)" tetap utuh.
        for part in re.split(r"[,;•\n](?![^()]*\))|\s{2,}", chunk):
            part = re.sub(r"^[^\w(]+|[^\w)+#]+$", "", part).strip()
            if part.startswith("(") and out:  # "(Advanced)" milik skill sebelumnya
                out[-1] = f"{out[-1]} {part}"
                continue
            if 2 <= len(part) <= 50 and part.lower() not in {o.lower() for o in out}:
                out.append(part)
    return out[:40]
