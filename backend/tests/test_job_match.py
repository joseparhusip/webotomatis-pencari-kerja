"""Kecocokan CV dengan satu lowongan yang ditempel. Data rekaan."""

from app.services import job_match
from app.services.cv_scan import CvScan

JOB = """Digital Marketing Staff
PT Contoh Niaga — Bandung
Tanggung jawab:
• Mengelola akun media sosial dan membuat konten harian
• Menjalankan iklan Meta Ads dan Google Ads
Kualifikasi:
• Pendidikan minimal S1 di bidang Pemasaran, Komunikasi, atau bidang terkait
• Pengalaman di bidang digital marketing minimal 2 tahun
• Menguasai Canva, CapCut, Google Analytics, dan SEO
• Mampu berbahasa Inggris secara aktif
"""

CV = CvScan(
    fields={"full_name": "Kandidat Contoh", "skills": ["Canva", "CapCut", "Meta Ads", "Excel", "Copywriting"],
            "summary": "Digital marketing dengan pengalaman mengelola media sosial."},
    text=("Kandidat Contoh\nDigital Marketing\nPengalaman\nSocial Media Admin, Toko Contoh Jan 2023 - Dec 2023\n"
          "• Membuat 20 konten Instagram per bulan dengan Canva dan CapCut.\n• Menjalankan iklan Meta Ads untuk promo.\n"
          "• Riset kata kunci SEO untuk artikel blog.\n"
          "Pendidikan\nUniversitas Contoh 2019 - 2023\nSarjana Manajemen Pemasaran"),
    experiences=[{"company": "Toko Contoh", "role": "Social Media Admin", "start": "Jan 2023", "end": "Dec 2023",
                  "highlights": ["Membuat 20 konten Instagram per bulan dengan Canva dan CapCut.",
                                 "Menjalankan iklan Meta Ads untuk promo.", "Riset kata kunci SEO untuk artikel blog."]}],
    education=[{"institution": "Universitas Contoh", "degree": "Sarjana", "major": "Manajemen Pemasaran",
                "start": "2019", "end": "2023"}],
)


def test_syarat_lowongan_dibaca_kode():
    job = job_match.parse_job("", JOB, vocab=["Google Ads", "Social Media"])
    assert job["title"] == "Digital Marketing Staff"
    assert {"Canva", "CapCut", "Google Analytics", "SEO", "Meta Ads", "Google Ads"} <= set(job["skills"])
    assert job["exp_years"] == 2 and not job["fresh_ok"]
    assert job["edu_level"] == 3 and job["edu_label"] == "S1/D4"
    assert "Pemasaran" in job["majors"] and "Komunikasi" in job["majors"]
    assert job["english"] and job["category"] == "marketing"


def test_fresh_graduate_dan_data_situs():
    job = job_match.parse_job("Admin Gudang", "Admin gudang. Terbuka untuk fresh graduate. Minimal SMA/SMK. Menguasai Excel.")
    assert job["exp_years"] == 0 and job["fresh_ok"] and job["edu_label"] == "SMA/SMK"
    job = job_match.parse_job("Staff", "Deskripsi tanpa syarat apa pun yang panjang sekali untuk lowongan ini.",
                              hints={"required_skills": ["Microsoft Excel"], "experience_min": 3,
                                     "education": "Minimal Diploma (D3)"})
    assert job["exp_years"] == 3 and job["exp_source"] == "data situs"
    assert job["edu_label"] == "D3" and job["skills"] == ["Excel"]


def test_cocokkan_cv_dengan_lowongan():
    jm = job_match.match_job(CV, job_match.parse_job("", JOB, vocab=["Google Ads"]))
    crit = {c["key"]: c for c in jm["criteria"]}
    skills = {s["name"]: s["status"] for s in jm["skills"]}
    assert skills["Canva"] == "list" and skills["SEO"] == "text"           # SEO hanya di uraian CV
    assert skills["Google Ads"] == "missing" and skills["Google Analytics"] == "missing"
    assert crit["job_experience"]["status"] == "partial"                    # 12 bulan dari 24 bulan
    assert "1 thn" in crit["job_experience"]["detail"] or "12 bln" in crit["job_experience"]["detail"]
    assert crit["job_education"]["status"] == "ok" and crit["job_major"]["status"] == "ok"
    assert crit["job_field"]["status"] == "ok"
    assert crit["job_english"]["status"] == "miss"
    ev = next(s for s in jm["skills"] if s["name"] == "Canva")["evidence"]
    assert "Canva" in ev                                                    # bukti diambil dari kalimat CV
    assert 0 < jm["score"] <= 100 and jm["grade"]


def test_skor_dibatasi_bila_skill_atau_pengalaman_jauh_kurang():
    job = job_match.parse_job("Backend Developer", "Backend Developer. Minimal 5 tahun pengalaman. Menguasai Go, "
                              "PostgreSQL, Docker, Kubernetes, dan Redis. Pendidikan minimal S1.")
    jm = job_match.match_job(CV, job)
    assert jm["score"] <= 49                                               # 0 dari 5 skill, 1 dari 5 tahun
    assert jm["grade"] in ("Kurang cocok", "Cukup cocok")
    ex = job_match.job_explain(jm)
    assert f"= {jm['raw_score']}." in ex["math"] and "Go" in ex["gaps"]
    assert job_match.allowed_job_decisions(jm["score"])[0] in ("jangan_dulu", "lamar_sesuaikan")


def test_jawaban_ai_lowongan_disaring():
    jm = job_match.match_job(CV, job_match.parse_job("", JOB, vocab=["Google Ads"]))
    data = {
        "verdict": "Kamu sangat cocok dengan pengalaman 5 tahun.",                  # angka karangan
        "decision": "lamar",
        "highlights": [
            {"requirement": "Canva", "text": "Sudah dipakai untuk konten.", "cv_line": "Membuat 20 konten Instagram per bulan dengan Canva dan CapCut."},
            {"requirement": "Google Ads", "text": "Kamu menguasai Google Ads.", "cv_line": ""},   # belum ada di CV
            {"requirement": "SEO", "text": "Riset SEO.", "cv_line": "Kalimat yang tidak ada di CV."},
        ],
        "adjustments": [{"criterion": "job_skills", "text": "Tonjolkan iklan digital.",
                         "before": "Menjalankan iklan Meta Ads untuk promo.",
                         "after": "Menjalankan iklan Meta Ads dan Google Ads untuk promo."}],     # menambah skill
    }
    out = job_match.check_job_review(data, jm, CV.text, JOB)
    assert "5 tahun" not in out["verdict"]
    assert out["decision"] in job_match.allowed_job_decisions(jm["score"])
    reqs = [h["requirement"] for h in out["highlights"]]
    assert "Google Ads" not in reqs and "Canva" in reqs
    assert next(h for h in out["highlights"] if h["requirement"] == "SEO")["cv_line"] == ""
    assert out["adjustments"][0]["after"] == ""                             # Google Ads tidak ada di CV


def test_batas_skor_dipakai_bila_syarat_lain_terpenuhi():
    """CV rapi & bidang sama, tapi skill lowongan hampir tidak ada → skor dibatasi 49 dengan alasan jelas."""
    job = job_match.parse_job("Digital Marketing Staff", "Digital Marketing Staff. Pengalaman minimal 1 tahun. "
                              "Menguasai Google Ads, Google Analytics, TikTok Ads, Shopify, dan Mailchimp. "
                              "Pendidikan minimal S1 Pemasaran.", vocab=["TikTok Ads", "Shopify", "Mailchimp", "Google Ads"])
    jm = job_match.match_job(CV, job)
    assert jm["raw_score"] > 49 and jm["score"] == 49 and "skill" in jm["cap_reason"]


def test_skill_alias_tidak_dobel_dan_contoh_tidak_menghapus_tanggal():
    job = job_match.parse_job("Backend", "Backend developer menguasai Go dan PostgreSQL untuk layanan API kantor.",
                              hints={"required_skills": ["Go / Golang", "Postgres"]})
    assert job["skills"].count("Go") == 1 and "Go / Golang" not in job["skills"]
    jm = job_match.match_job(CV, job_match.parse_job("", JOB))
    data = {"verdict": "Skor 85/100, cocok untuk Digital Marketing Staff.", "decision": "lamar_sesuaikan", "highlights": [],
            "adjustments": [{"criterion": "job_experience", "text": "Tonjolkan durasi.",
                             "before": "Social Media Admin, Toko Contoh Jan 2023 - Dec 2023",
                             "after": "Social Media Admin, Toko Contoh [1] thn"}]}
    out = job_match.check_job_review(data, jm, CV.text, JOB)
    assert out["verdict"].startswith("Skor 85/100")                     # "/100" bukan angka karangan
    assert out["adjustments"][0]["after"] == ""                          # tanggal asli hilang → dibuang


def test_penanda_tidak_boleh_mengajak_menambah_skill():
    jm = job_match.match_job(CV, job_match.parse_job("", JOB))
    data = {"verdict": "Cocok untuk Digital Marketing Staff.", "decision": "lamar_sesuaikan", "highlights": [],
            "adjustments": [{"criterion": "job_skills", "text": "Rapikan daftar skill.",
                             "before": "• Menjalankan iklan Meta Ads untuk promo.",
                             "after": "• Menjalankan iklan Meta Ads untuk promo dengan [skill lain]."}]}
    assert job_match.check_job_review(data, jm, CV.text, JOB)["adjustments"][0]["after"] == ""
