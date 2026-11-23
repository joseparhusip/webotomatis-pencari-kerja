import json

from app.core import config
from app.models import UserProfile
from app.services.cv_scan import _clean, heuristic_fields, verify_fields
from app.services.profile import apply_cv_fields

CV_TEXT = """BUDI SANTOSO
Bekasi, Jawa Barat · budi.santoso@gmail.com · +62 812-3456-7890
linkedin.com/in/budisantoso · github.com/budis

Pengalaman
PT Contoh Digital — Backend Developer (2021–2024)
Membangun REST API dengan Go dan PostgreSQL, deploy dengan Docker.
Skills: Go, PostgreSQL, Docker, Flutter, Git
"""


def test_clean_never_returns_null():
    out = _clean({"full_name": None, "email": "null", "phone": "N/A", "address": "-",
                  "skills": None, "experience_years": None, "summary": "tidak ada"})
    assert all(v is not None for v in out.values())
    assert out["full_name"] == out["email"] == out["phone"] == out["address"] == out["summary"] == ""
    assert out["skills"] == [] and out["experience_years"] == 0


def test_verify_drops_invented_values():
    ai = _clean({
        "full_name": "Budi Santoso", "email": "budi.santos0@gmail.com",  # typo OCR 0/o
        "phone": "0812 3456 7890", "address": "Kota Bekasi, Jawa Barat",
        "linkedin_url": "https://www.linkedin.com/in/budisantoso/", "portfolio_url": "github.com/karangan",
        "summary": "Backend developer.", "skills": ["Go", "PostgreSQL", "Kubernetes", "Node.js"],
        "experience_years": 3,
    })
    out = verify_fields(ai, CV_TEXT)
    assert out["full_name"] == "Budi Santoso"
    assert out["email"] == "budi.santoso@gmail.com"  # diganti dengan yang benar-benar tertulis
    assert out["phone"] == "0812 3456 7890"           # +62 812… dianggap nomor yang sama
    assert out["linkedin_url"]
    assert out["portfolio_url"] == ""                 # tidak ada di CV → dibuang
    assert out["skills"] == ["Go", "PostgreSQL"]      # Kubernetes & Node.js tidak tertulis


def test_heuristic_fields():
    out = heuristic_fields(CV_TEXT)
    assert out["full_name"] == "Budi Santoso"
    assert out["email"] == "budi.santoso@gmail.com"
    assert out["phone"].startswith("+62 812")
    assert "github.com/budis" in out["portfolio_url"]
    assert "Docker" in out["skills"]


def test_apply_only_fills_empty_unless_overwrite():
    p = UserProfile(full_name="Budi", email="lama@example.com", skills=["Go"], experience_years=0)
    fields = {"full_name": "Budi Santoso", "email": "baru@example.com", "phone": "0812",
              "linkedin_url": "linkedin.com/in/x", "skills": ["go", "Docker"], "experience_years": 3}
    changed = apply_cv_fields(p, fields)
    assert p.full_name == "Budi" and p.email == "lama@example.com"
    assert p.phone == "0812" and p.linkedin_url == "https://linkedin.com/in/x"
    assert p.skills == ["Go", "Docker"] and p.experience_years == 3
    assert "skill" in changed
    apply_cv_fields(p, fields, overwrite=True)
    assert p.full_name == "Budi Santoso" and p.email == "baru@example.com"


def test_runtime_overrides_take_precedence(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "runtime_file", lambda: tmp_path / "config" / "settings.json")
    config._cache.update(key=None, settings=None)
    assert config.get_settings() is config.env_settings()
    config.write_overrides({"smtp_user": "web@example.com", "max_emails_per_day": 7, "database_url": "x"})
    s = config.get_settings()
    assert s.smtp_user == "web@example.com" and s.max_emails_per_day == 7
    assert "database_url" not in json.loads((tmp_path / "config" / "settings.json").read_text())
    config._cache.update(key=None, settings=None)


def test_kesimpulan_ai_diverifikasi_ke_teks_cv():
    """AI hanya menyusun kesimpulan; pengalaman dari AI dipakai hanya bila pemetaan kosong, dan tetap dicek."""
    from app.services.cv_scan import CvScan, add_conclusions

    text = CV_TEXT + "\nProyek: SecuraKost (Vue.js, Tailwind)\nUniversitas Logistik dan Bisnis Internasional, S1, IPK 3,75\n"
    data = {
        "summary_id": "Lulusan S1 dengan pengalaman membangun REST API menggunakan Go.",
        "experiences": [
            {"company": "PT Contoh Digital", "role": "Backend Developer", "start": "2021", "end": "2024",
             "highlights": ["Membangun REST API dengan Go", "Menangani 10 juta request"]},  # 10 juta tidak ada
            {"company": "PT Karangan Fiktif", "role": "CTO"},
        ],
        "education": [{"institution": "Universitas Logistik dan Bisnis Internasional", "gpa": "3,90"}],
        "evidence": [
            {"source": "PT Contoh Digital", "text_id": "Di PT Contoh Digital, saya membangun REST API dengan Go "
             "dan PostgreSQL serta deploy dengan Docker.", "categories": ["backend", "ngawur"], "skills": ["Go"]},
            {"source": "PT Contoh Digital", "text_id": "Di PT Contoh Digital, saya meningkatkan performa 70% "
             "dengan Go dan PostgreSQL.", "categories": ["backend"]},
        ],
        "intro_id": None, "strength_id": "null",
    }
    scan = add_conclusions(CvScan(fields={"skills": ["Go", "PostgreSQL", "Docker"]}, text=text), data, [], "gemini")
    assert [e["company"] for e in scan.experiences] == ["PT Contoh Digital"]
    assert scan.experiences[0]["highlights"] == ["Membangun REST API dengan Go"]
    assert scan.education[0]["gpa"] == ""          # 3,90 tidak tertulis (yang benar 3,75)
    assert len(scan.evidence) == 1                  # bukti "70%" dibuang
    ev = scan.evidence[0]
    assert ev["categories"] == ["backend"] and {"Go", "PostgreSQL", "Docker"} <= set(ev["skills"])
    assert scan.letter == {"intro_id": "", "intro_en": "", "strength_id": "", "strength_en": ""}
    assert scan.fields["summary"].startswith("Lulusan S1") and scan.ai == "gemini"
    assert all(v is not None for v in scan.to_data().values())

    # Pemetaan sudah menemukan pengalaman → salinan AI diabaikan (data CV tidak diubah AI).
    mapped = CvScan(fields={"skills": []}, text=text, experiences=[{"company": "PT Contoh Digital", "role": "Dev",
                                                                    "start": "2021", "end": "2024", "highlights": []}])
    assert add_conclusions(mapped, data, [], "gemini").experiences[0]["role"] == "Dev"


# ---------- lama pengalaman kerja ----------
from datetime import date as _date

from app.services.cv_scan import experience_summary, experience_type, months_text

_TODAY = _date(2026, 11, 23)


def test_pengalaman_magang_ikut_dihitung():
    exps = [
        {"company": "PT A", "role": "Marketing Intern", "employment_type": "Magang", "start": "Oct 2023", "end": "Mar 2024"},
        {"company": "PT B", "role": "IT Application Intern", "employment_type": "Magang", "start": "Feb 2023", "end": "Jul 2023"},
        {"company": "Bank C", "role": "Back Office Intern", "employment_type": "Magang", "start": "Dec 2022", "end": "Jan 2023"},
        {"company": "Toko D", "role": "", "employment_type": "", "start": "Sep 2020", "end": "Dec 2022"},
    ]
    s = experience_summary(exps, _TODAY)
    # Sep 2020–Jan 2023 digabung (Des 2022 tumpang-tindih) = 29 bln, + Feb–Jul 2023 (6) menyambung → 35,
    # + Okt 2023–Mar 2024 (6) = 41 bulan → 3 tahun
    assert s["months"] == 41 and s["years"] == 3 and s["counted"] == 4
    assert s["by_type"] == {"Magang": 14, "Lainnya": 28}


def test_jenis_kerja_dan_format_tanggal():
    exps = [
        {"company": "X", "role": "Backend Developer", "employment_type": "Penuh waktu", "start": "Oktober 2026", "end": "Sekarang"},
        {"company": "Y", "role": "Mobile Developer", "employment_type": "Kontrak", "start": "07/2025", "end": "2025-09"},
        {"company": "Z", "role": "Freelance Web Developer", "employment_type": "", "start": "2024", "end": "2024"},
        {"company": "HIMA", "role": "Ketua", "employment_type": "Organisasi", "start": "2022", "end": "2023"},
        {"company": "W", "role": "Dev", "employment_type": "Part-time", "start": "", "end": ""},
    ]
    s = experience_summary(exps, _TODAY)
    assert s["by_type"] == {"Penuh waktu": 2, "Kontrak": 3, "Freelance": 12}
    assert s["months"] == 17 and s["years"] == 1
    assert "HIMA (organisasi)" in s["skipped"] and "W (tanggal tidak terbaca)" in s["skipped"]


def test_label_jenis_dan_teks_bulan():
    assert experience_type({"employment_type": "", "role": "Software Engineer Intern"}) == "Magang"
    assert experience_type({"employment_type": "Full-time", "role": "Dev"}) == "Penuh waktu"
    assert experience_type({"employment_type": "Contract", "role": "Dev"}) == "Kontrak"
    assert months_text(41) == "3 thn 5 bln" and months_text(12) == "1 thn" and months_text(0) == "0 bln"
