"""Target pencarian mengikuti CV aktif — CV non-IT tidak boleh diarahkan ke lowongan IT, dan sebaliknya."""

from app.connectors.jobstreet import JobStreetConnector
from app.models.enums import ExperienceLevel, RoleCategory
from app.services import targeting
from app.services.cv_scan import CvScan
from app.services.cv_targets import clean_role, derive_targets
from app.services.targeting import classify_role, score_job, search_keywords, set_target

# CV contoh (rekaan untuk tes): lulusan baru dengan pengalaman marketing, media sosial, dan back office.
MARKETING_CV = CvScan(
    fields={"full_name": "Kandidat Marketing", "skills": [
        "Digital Marketing", "Social Media Management", "Canva", "CapCut", "Market Research",
        "Microsoft Excel", "Communication", "Teamwork"]},
    experiences=[
        {"company": "Toko Online Contoh", "role": "Social Media Admin", "employment_type": "Freelance",
         "start": "Jan 2022", "end": "Dec 2022",
         "highlights": ["Membuat konten Instagram Reels dan TikTok serta menjalankan Instagram Ads."]},
        {"company": "PT Contoh Finansial", "role": "Marketing Officer Intern", "employment_type": "Magang",
         "start": "Jan 2023", "end": "Jun 2023",
         "highlights": ["Melayani nasabah dan melakukan cross-selling produk."]},
        {"company": "Bank Contoh", "role": "Back Office Intern", "employment_type": "Magang",
         "start": "Jul 2023", "end": "Aug 2023", "highlights": ["Memverifikasi data pajak dan dokumen."]},
    ],
)

DEV_CV = CvScan(
    fields={"full_name": "Kandidat Developer", "skills": ["Go", "PostgreSQL", "React", "Next.js", "Flutter", "Docker"]},
    experiences=[
        {"company": "PT Contoh Teknologi", "role": "Backend Developer", "employment_type": "Penuh waktu",
         "start": "Jan 2025", "end": "Sekarang", "highlights": ["Membangun REST API dengan Go dan PostgreSQL."]},
        {"company": "Klinik Contoh", "role": "Mobile Developer Intern", "employment_type": "Magang",
         "start": "Jul 2024", "end": "Sep 2024", "highlights": ["Membuat aplikasi Android dengan Flutter."]},
    ],
    projects=[{"name": "Toko Contoh", "role": "Frontend Developer", "tech": ["React"], "description": "", "url": ""}],
)


def test_klasifikasi_lowongan_non_it():
    cases = {
        "Social Media Specialist": RoleCategory.MARKETING,
        "Digital Marketing Staff": RoleCategory.MARKETING,
        "Content Creator": RoleCategory.CREATIVE,
        "Video Editor": RoleCategory.CREATIVE,
        "Admin Back Office": RoleCategory.ADMIN,
        "Customer Service Officer": RoleCategory.CUSTOMER_SERVICE,
        "Staff Accounting": RoleCategory.FINANCE,
        "Admin Keuangan": RoleCategory.FINANCE,
        "HR Generalist": RoleCategory.HR,
        "Staff Gudang": RoleCategory.OPERATIONS,
        "Barista": RoleCategory.HOSPITALITY,
        "Perawat": RoleCategory.HEALTHCARE,
        "Guru Matematika": RoleCategory.EDUCATION,
        "Civil Engineer": RoleCategory.ENGINEERING,
    }
    for title, expected in cases.items():
        assert classify_role(title) is expected, title
    # IT tetap IT
    assert classify_role("Backend Developer") is RoleCategory.BACKEND
    assert classify_role("Data Analyst") is RoleCategory.DATA
    # QA pabrik/konstruksi bukan QA software; QA software tetap QA
    assert classify_role("QA/QC Inspector", "", "Konstruksi / Penjaminan & Kontrol Mutu") is not RoleCategory.QA
    assert classify_role("QA Engineer", "", "Manufaktur, Transportasi & Logistik / Penjaminan & Kontrol Mutu") \
        is not RoleCategory.QA
    assert classify_role("QA Engineer", "Automation test dengan Selenium untuk aplikasi web") is RoleCategory.QA
    # Kategori sumber dipakai bila judul tidak jelas
    assert classify_role("Staff", "", "Pemasaran & Komunikasi / Pemasaran Digital & Pencarian") is RoleCategory.MARKETING


def test_target_dari_cv_marketing():
    plan = derive_targets(MARKETING_CV)
    assert plan.categories[0] == "marketing"
    assert not {"backend", "frontend", "fullstack", "mobile"} & set(plan.categories)
    assert "admin" in plan.categories
    assert any("marketing" in k for k in plan.keywords)
    assert not any("developer" in k for k in plan.keywords)
    # setiap bidang punya dasar dari CV
    assert all(plan.basis[c] for c in plan.categories)


def test_target_dari_cv_developer():
    plan = derive_targets(DEV_CV)
    assert {"backend", "mobile", "frontend"} <= set(plan.categories)
    assert "marketing" not in plan.categories
    assert "backend developer" in plan.keywords and "mobile developer" in plan.keywords


def test_target_ai_tanpa_dasar_di_cv_dibuang():
    from app.services.cv_scan import _Ctx, _targets

    ctx = _Ctx("Marketing Officer Intern di PT Contoh. Skill: Digital Marketing, Canva. " * 3)
    out = _targets([
        {"title": "Digital Marketing Staff", "category": "marketing", "basis": "Marketing Officer Intern di PT Contoh"},
        {"title": "Backend Developer", "category": "backend", "basis": "pengalaman Golang di Startup X"},
        {"title": "Pilot", "category": "terbang", "basis": "Canva"},
    ], ctx)
    assert [t["title"] for t in out] == ["Digital Marketing Staff"]


def test_skor_mengikuti_bidang_cv():
    marketing_job = "Social Media Specialist. Menguasai Canva, CapCut, dan digital marketing."
    backend_job = "Backend Developer Golang PostgreSQL REST API"
    plan = derive_targets(MARKETING_CV)
    t = set_target(plan.categories, plan.keywords, MARKETING_CV.fields["skills"], 1)
    mk, mk_reason, mk_skills = score_job(RoleCategory.MARKETING, ExperienceLevel.JUNIOR, marketing_job, t)
    be, be_reason, _ = score_job(RoleCategory.BACKEND, ExperienceLevel.JUNIOR, backend_job, t)
    assert mk > be + 30
    assert {"Canva", "CapCut", "Digital Marketing"} <= set(mk_skills)
    assert "di luar bidang CV" in be_reason
    # soft skill tidak dihitung sebagai kecocokan
    assert "Communication" not in mk_skills and "Teamwork" not in mk_skills

    plan = derive_targets(DEV_CV)
    t = set_target(plan.categories, plan.keywords, DEV_CV.fields["skills"], 1)
    mk2, _, _ = score_job(RoleCategory.MARKETING, ExperienceLevel.JUNIOR, marketing_job, t)
    be2, _, _ = score_job(RoleCategory.BACKEND, ExperienceLevel.JUNIOR, backend_job, t)
    assert be2 > mk2 + 30


def test_level_mengikuti_lama_pengalaman():
    fresh = set_target(["marketing"], [], ["Digital Marketing"], 0)
    senior_cv = set_target(["marketing"], [], ["Digital Marketing"], 6)
    text = "Digital marketing"
    assert score_job(RoleCategory.MARKETING, ExperienceLevel.SENIOR, text, fresh)[0] <= 30
    assert score_job(RoleCategory.MARKETING, ExperienceLevel.SENIOR, text, senior_cv)[0] >= 70


def test_keyword_dan_kategori_jobstreet_ikut_target():
    set_target(["marketing", "admin"], [], ["Digital Marketing"], 0)
    kws = search_keywords()
    assert "digital marketing" in kws and not any("developer" in k for k in kws)
    sweeps = JobStreetConnector().keywords(kws)
    assert sweeps[:2] == ["@klasifikasi:6008", "@klasifikasi:6251"]  # Pemasaran; Administrasi
    assert "@klasifikasi:6281" not in sweeps                          # kategori IT tidak disapu

    set_target(["backend", "data"], [], ["Go"], 1)
    assert JobStreetConnector().keywords([])[:1] == ["@klasifikasi:6281"]


def test_tanpa_target_tidak_ada_keyword():
    targeting.set_target([], [], [], 0)
    assert search_keywords() == []


def test_clean_role():
    assert clean_role("Marketing Officer Intern (MAGENTA BUMN)") == "marketing officer"
    assert clean_role("Back Office Intern") == "back office"


def test_jenjang_s1_bukan_angka_karangan():
    from app.services.cv_scan import _Ctx

    ctx = _Ctx("Bachelor of Digital Business graduate (GPA 3.75/4.00) with 2+ years of experience in web development")
    assert ctx.numbers_ok("Saya lulusan S1 Bisnis Digital dengan IPK 3,75/4,00.")
    assert not ctx.numbers_ok("Saya membangun 44 endpoint API.")  # angka yang tidak ada di CV tetap ditolak


def test_deteksi_ganti_orang_tidak_hanya_dari_nama():
    from app.models import UserProfile
    from app.services.profile import _different_person

    p = UserProfile(full_name="Kandidat A", email="a@example.com", linkedin_url="https://linkedin.com/in/kandidat-a")
    assert not _different_person(p, {"full_name": "Kandidat A", "email": "", "linkedin_url": ""})
    # nama CV baru gagal terbaca, tapi email berbeda → orang lain
    assert _different_person(p, {"full_name": "", "email": "b@example.com"})
    # nama sama tapi LinkedIn berbeda → orang lain
    assert _different_person(p, {"full_name": "Kandidat A", "linkedin_url": "linkedin.com/in/orang-lain"})
    # tidak ada penanda yang bisa dibandingkan → tidak bisa dipastikan sama → diperlakukan orang lain
    assert _different_person(p, {"full_name": "", "email": "", "linkedin_url": ""})


def test_pengalaman_sama_untuk_semua_cv_termasuk_proyek():
    from datetime import date

    from app.services.cv_scan import total_experience

    today = date(2026, 11, 23)
    kerja = [{"company": "PT A", "role": "Backend Developer", "start": "Oct 2026", "end": "Present"},
             {"company": "Klinik B", "role": "Mobile Developer", "start": "Jul 2025", "end": "Sep 2025"},
             {"company": "PT C", "role": "Backend Developer", "start": "Jul 2024", "end": "Sep 2024"}]
    proyek = [{"name": "Hibah Kampus", "role": "System Analyst", "start": "May 2023", "end": "Apr 2024"},
              {"name": "Toko Online", "role": "Full Stack Developer", "start": "Nov 2024", "end": "Feb 2025"},
              {"name": "Tanpa tanggal", "role": "Frontend Developer"}]
    only_work = total_experience(kerja, [], today)
    with_projects = total_experience(kerja, proyek, today)
    assert only_work["months"] == 8 and only_work["years"] == 0
    assert with_projects["months"] == 8 + 12 + 4 and with_projects["years"] == 2   # proyek bertanggal ikut dihitung
    assert with_projects["by_type"]["Proyek"] == 16


def test_lowongan_yang_sudah_diproses_disembunyikan_dari_daftar():
    from app.services.jobs import JobFilters, build_query

    def sql(f):
        return str(build_query(f).compile(compile_kwargs={"literal_binds": True}))

    default = sql(JobFilters(statuses=["new", "seen", "saved"]))
    assert "application" in default and "regexp_replace" in default     # draft/terkirim + iklan pasang ulang
    assert "application" not in sql(JobFilters(statuses=["new", "applied"]))   # centang "Dilamar" → tampil
