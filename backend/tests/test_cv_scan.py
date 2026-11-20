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


def test_build_scan_drops_invented_history_and_numbers():
    from app.services.cv_scan import build_scan

    text = CV_TEXT + "\nProyek: SecuraKost (Vue.js, Tailwind)\nUniversitas Logistik dan Bisnis Internasional, S1, IPK 3,75\n"
    data = {
        "full_name": "Budi Santoso", "email": "budi.santoso@gmail.com", "skills": ["Go"], "experience_years": 3,
        "experiences": [
            {"company": "PT Contoh Digital", "role": "Backend Developer", "start": "2021", "end": "2024",
             "highlights": ["Membangun REST API dengan Go", "Menangani 10 juta request"]},  # 10 juta tidak ada
            {"company": "PT Karangan Fiktif", "role": "CTO"},
        ],
        "projects": [{"name": "SecuraKost", "tech": ["Vue.js", "React"]}, {"name": "Proyek Palsu"}],
        "education": [{"institution": "Universitas Logistik dan Bisnis Internasional", "gpa": "3,90"}],
        "evidence": [
            {"source": "PT Contoh Digital", "text_id": "Di PT Contoh Digital, saya membangun REST API dengan Go "
             "dan PostgreSQL serta deploy dengan Docker.", "categories": ["backend", "ngawur"], "skills": ["Go"]},
            {"source": "PT Contoh Digital", "text_id": "Di PT Contoh Digital, saya meningkatkan performa 70% "
             "dengan Go dan PostgreSQL.", "categories": ["backend"]},
        ],
        "intro_id": None, "strength_id": "null",
    }
    scan = build_scan(data, text, [], "gemini")
    assert [e["company"] for e in scan.experiences] == ["PT Contoh Digital"]
    assert scan.experiences[0]["highlights"] == ["Membangun REST API dengan Go"]
    assert [p["name"] for p in scan.projects] == ["SecuraKost"] and scan.projects[0]["tech"] == ["Vue.js"]
    assert scan.education[0]["gpa"] == ""          # 3,90 tidak tertulis (yang benar 3,75)
    assert len(scan.evidence) == 1                  # bukti "70%" dibuang
    ev = scan.evidence[0]
    assert ev["categories"] == ["backend"] and {"Go", "PostgreSQL", "Docker"} <= set(ev["skills"])
    assert scan.letter == {"intro_id": "", "intro_en": "", "strength_id": "", "strength_en": ""}
    assert all(v is not None for v in scan.to_data().values())
