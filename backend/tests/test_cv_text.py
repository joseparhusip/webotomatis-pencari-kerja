"""Pemetaan CV dari teks (tanpa AI). Data CV di sini rekaan; formatnya mengikuti CV yang umum dipakai.

Prinsip: yang tertulis di CV wajib terisi; yang tidak tertulis dibiarkan kosong.
"""

from app.services.cv_scan import pattern_scan

# Format 1: "Perusahaan - Lokasi   tanggal", posisi di baris berikutnya, ringkasan di bawah kontak.
CV_A = """SITI RAHMAWATI
+6281234567890 | siti.rahma@gmail.com | https://www.linkedin.com/in/siti-rahma/
Lulusan Ilmu Komunikasi dengan pengalaman mengelola media sosial brand dan membuat konten video pendek
untuk Instagram dan TikTok.
Pengalaman
Toko Contoh Fashion -  Bandung, Indonesia Jan 2022 - Dec 2022
Social Media Admin
Membuat konten Instagram Reels dan TikTok setiap minggu, serta menjalankan Instagram Ads untuk promosi produk
baru.
Menganalisis performa konten untuk menentukan jadwal unggah terbaik.
PT Contoh Media -  Jakarta, Indonesia Feb 2023 - Sekarang
Content Creator Intern - Menulis naskah video dan mengedit video dengan CapCut.
Pendidikan
Universitas Contoh - Bandung, Indonesia Aug 2018 - Jul 2022
Sarjana Ilmu Komunikasi, 3.45/4.00
Skills: Canva, CapCut, Copywriting, Instagram Ads, Microsoft Excel
(Advanced), Public Speaking
"""

# Format 2: "Posisi, Perusahaan  tanggal", bagian Profile, Core Skills berkelompok, proyek "Nama | Peran".
CV_B = """Andi Pratama
Backend Developer
github.com/andipratama | linkedin.com/in/andipratama
Profile
Backend developer with experience building REST APIs using Go and PostgreSQL for logistics and fintech
products.
Core Skills
Backend Development (Go/Gin, Node.js/Express,
PHP)
Database Design (PostgreSQL, MySQL)
Work Experience
Backend Developer, PT Contoh Logistik Mar 2024 – Present
Built REST API endpoints with Go (Gin) and PostgreSQL.
Software Engineer Intern, PT Contoh Fintech Jun 2023 – Aug 2023
Wrote unit tests and fixed bugs in payment services.
Education
Bachelor of Computer Science, Universitas Contoh 2019 – 2023
GPA: 3.60/4.00
Projects
Aplikasi Kasir | Full Stack Developer Jan 2023 – Feb 2023
Built a point-of-sale web app with React and Node.js.
Organizational Experience
Ketua Himpunan, Himpunan Mahasiswa Contoh Oct 2021 – Sep 2022
Led a team of 20 people.
"""


def test_format_perusahaan_lokasi_dan_posisi_di_baris_berikutnya():
    s = pattern_scan(CV_A, [])
    f = s.fields
    assert f["full_name"] == "Siti Rahmawati"
    assert f["email"] == "siti.rahma@gmail.com" and f["phone"].startswith("+62812")
    assert f["linkedin_url"].endswith("siti-rahma/")
    assert f["address"] == "" and f["portfolio_url"] == ""            # tidak tertulis → kosong
    assert f["summary"].startswith("Lulusan Ilmu Komunikasi") and "TikTok." in f["summary"]
    assert [(e["company"], e["role"], e["start"], e["end"]) for e in s.experiences] == [
        ("Toko Contoh Fashion", "Social Media Admin", "Jan 2022", "Dec 2022"),
        ("PT Contoh Media", "Content Creator Intern", "Feb 2023", "Sekarang"),
    ]
    first = s.experiences[0]["highlights"]
    assert len(first) == 2 and first[0].endswith("produk baru.")       # baris terpotong disambung
    assert s.experiences[1]["highlights"] == ["Menulis naskah video dan mengedit video dengan CapCut."]
    assert s.education == [{"institution": "Universitas Contoh", "degree": "S1", "major": "Ilmu Komunikasi",
                            "start": "Aug 2018", "end": "Jul 2022", "gpa": "3.45"}]
    assert "Microsoft Excel (Advanced)" in f["skills"] and "CapCut" in f["skills"]
    assert "Public Speaking" in f["skills"]


def test_format_posisi_koma_perusahaan_dan_skill_berkelompok():
    s = pattern_scan(CV_B, [])
    f = s.fields
    assert f["full_name"] == "Andi Pratama"
    assert f["email"] == "" and f["phone"] == ""                         # tidak tertulis → kosong
    assert f["summary"].startswith("Backend developer with experience")
    assert [(e["role"], e["company"], e["end"]) for e in s.experiences] == [
        ("Backend Developer", "PT Contoh Logistik", "Present"),
        ("Software Engineer Intern", "PT Contoh Fintech", "Aug 2023"),
    ]   # pengalaman organisasi tidak masuk pengalaman kerja
    assert s.education[0]["institution"] == "Universitas Contoh" and s.education[0]["gpa"] == "3.60"
    assert s.education[0]["degree"] == "S1" and s.education[0]["major"] == "Computer Science"
    assert [(p["name"], p["role"]) for p in s.projects] == [("Aplikasi Kasir", "Full Stack Developer")]
    assert {"Go/Gin", "Node.js/Express", "PHP", "PostgreSQL", "MySQL"} <= set(f["skills"])
    assert "Backend Development (Go/Gin" not in f["skills"] and "Go" not in f["skills"]   # tidak dobel


def test_ocr_bullet_terbaca_sebagai_huruf_e():
    ocr = CV_A.replace("Membuat konten", "e Membuat konten").replace("Skills:", "e Skills:")
    s = pattern_scan(ocr, [])
    assert s.experiences[0]["highlights"][0].startswith("Membuat konten")
    assert "CapCut" in s.fields["skills"]


def test_kata_terpotong_tanda_hubung_di_skill():
    from app.services.cv_text import _skill_section_items

    assert _skill_section_items(["Authentication & Security (JWT, RBAC, multi-", "tenancy)"]) == ["JWT, RBAC, multi-tenancy"]


def test_nama_bergelar_dan_domisili_terbaca():
    from app.services.cv_text import parse_address, parse_name

    assert parse_name("DESI PUTRI, S.Kom\nBekasi, Jawa Barat · desi@gmail.com") == "Desi Putri"
    assert parse_name("Curriculum Vitae\nAndi Pratama\nBackend Developer") == "Andi Pratama"
    assert parse_address("DESI PUTRI\nBekasi, Jawa Barat · desi@gmail.com · 0812-3456-7890") == "Bekasi, Jawa Barat"
    assert parse_address("Rizky\nAlamat: Kota Surabaya, Jawa Timur\nEmail: r@gmail.com") == "Kota Surabaya, Jawa Timur"
    assert parse_address("Andi\nandi@gmail.com | github.com/andi\nPengalaman\nPT X - Jakarta Jan 2020 - Dec 2021") == ""


def test_kalimat_surat_dan_bukti_tanpa_ai_dari_fakta_cv():
    from app.services.cv_scan import draft_conclusions

    s = pattern_scan(CV_A, [])
    draft_conclusions(s)
    assert s.letter["intro_id"] == ("Saya merupakan lulusan S1 Ilmu Komunikasi dari Universitas Contoh yang saat ini "
                                    "bekerja sebagai Content Creator Intern di PT Contoh Media.")
    assert "Canva" in s.letter["strength_id"] and "Public Speaking" not in s.letter["strength_id"]  # soft skill tidak
    assert s.evidence[0]["text_id"].startswith("Sebagai Content Creator Intern di PT Contoh Media, saya menulis naskah")
    assert all(e["source"] in CV_A for e in s.evidence)                      # semua dari CV
