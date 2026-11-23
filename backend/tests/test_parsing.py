from app.models.enums import EmploymentType, ExperienceLevel, RoleCategory
from app.services.parsing import (
    dedup_hash,
    extract_hr_email,
    html_to_text,
    localize_place,
    parse_employment_type,
    parse_salary_label,
    split_location,
)
from app.services.targeting import classify_role, detect_level, score_job
from app.web.templating import rupiah_short


def test_salary_label_range_with_nbsp():
    assert parse_salary_label("Rp\xa010.000.000 – Rp\xa015.000.000 per month") == (10_000_000, 15_000_000)


def test_salary_label_single_and_juta():
    assert parse_salary_label("Rp 5.500.000 per month") == (5_500_000, 5_500_000)
    assert parse_salary_label("Rp 8 – 12 juta") == (8_000_000, 12_000_000)


def test_salary_label_yearly_and_hourly():
    assert parse_salary_label("Rp 120.000.000 per year") == (10_000_000, 10_000_000)
    assert parse_salary_label("Rp 50.000 per hour") == (None, None)
    assert parse_salary_label(None) == (None, None)


def test_employment_type():
    assert parse_employment_type("Full time") is EmploymentType.FULLTIME
    assert parse_employment_type("Kontrak/Temporer") is EmploymentType.CONTRACT
    assert parse_employment_type("Internship") is EmploymentType.INTERNSHIP
    assert parse_employment_type("") is None


def test_localize_place_matches_jobstreet_naming():
    assert localize_place("South Jakarta") == "Jakarta Selatan"
    assert localize_place("Central Jakarta") == "Jakarta Pusat"
    assert localize_place("West Java") == "Jawa Barat"
    assert localize_place("Bekasi") == "Bekasi"


def test_split_location():
    assert split_location("Jakarta Barat, Jakarta Raya") == ("Jakarta Barat", "Jakarta Raya")
    assert split_location("Bekasi") == ("Bekasi", None)


def test_html_to_text_lists():
    text = html_to_text("<ul><li>Go</li><li>PostgreSQL</li></ul><p>Kirim ke <b>hr@abc.co.id</b></p>")
    assert "• Go" in text and "• PostgreSQL" in text and "hr@abc.co.id" in text


def test_extract_hr_email_ignores_portal_domains():
    assert extract_hr_email("hubungi support@kalibrr.com atau hr@pt-abc.co.id.") == "hr@pt-abc.co.id"
    assert extract_hr_email("tidak ada email") is None


def test_dedup_hash_ignores_company_noise():
    a = dedup_hash("PT. Maju Jaya Tbk", "Backend Developer", "Jakarta Selatan")
    b = dedup_hash("Maju Jaya", "backend developer", "jakarta selatan")
    assert a == b


def test_classify_role():
    assert classify_role("Flutter Developer") is RoleCategory.MOBILE
    assert classify_role("Full Stack Engineer (Next.js/Go)") is RoleCategory.FULLSTACK
    assert classify_role("Frontend Developer - React") is RoleCategory.FRONTEND
    assert classify_role("Backend Engineer Golang") is RoleCategory.BACKEND
    assert classify_role("Sales Executive Software") is RoleCategory.SALES  # bukan IT


def test_classify_generic_titles_from_description():
    assert classify_role("Software Engineer", "Stack: React, Node.js, PostgreSQL") is RoleCategory.FULLSTACK
    assert classify_role("Programmer", "Menguasai PHP Laravel dan MySQL") is RoleCategory.BACKEND
    assert classify_role("Junior Programmer", "") is RoleCategory.FULLSTACK
    assert classify_role("Application Developer", "Flutter & Dart") is RoleCategory.MOBILE
    assert classify_role(".Net Developer", "C# ASP.NET") is RoleCategory.BACKEND
    assert classify_role("QA Engineer", "") is RoleCategory.QA
    assert classify_role("Data Warehouse (DW) Developer", "SQL ETL") is RoleCategory.DATA
    assert classify_role("Full Time Pengajar UTBK Numerasi Bekasi", "") is RoleCategory.EDUCATION


def test_classify_all_it_jobs():
    it = {
        "WEB DEVELOPER": RoleCategory.FRONTEND, "PROGRAMMER STAF": RoleCategory.FULLSTACK,
        "Technical Support": RoleCategory.IT_SUPPORT, "Marketing dan IT Support": RoleCategory.IT_SUPPORT,
        "NOC Engineer": RoleCategory.IT_SUPPORT, "Teknisi Perbaikan Printer (JAKSEL)": RoleCategory.IT_SUPPORT,
        "Software Development Engineer In Test": RoleCategory.QA, "Business Intelligence Analyst": RoleCategory.DATA,
        "System Analyst": RoleCategory.IT_OTHER, "DevOps Engineer": RoleCategory.IT_OTHER,
        "UI/UX Designer": RoleCategory.IT_OTHER, "Staf TI": RoleCategory.IT_OTHER,
        "Java Developer": RoleCategory.BACKEND, "Android Engineer": RoleCategory.MOBILE,
    }
    for title, expected in it.items():
        assert classify_role(title) is expected, title
    non_it = {"Barista Mobile": RoleCategory.HOSPITALITY, "Mobile Waiter": RoleCategory.HOSPITALITY,
              "Server Restaurant": RoleCategory.HOSPITALITY, "Mekanik Mobil": RoleCategory.ENGINEERING,
              "Sales Executive": RoleCategory.SALES}
    for title, expected in non_it.items():
        assert classify_role(title) is expected, title
    # Kategori asli situs: judul umum di kategori IT JobStreet tetap dianggap IT.
    assert classify_role("Vendor Management Officer", "", "Teknologi Informasi & Komunikasi / Manajemen") \
        is RoleCategory.IT_OTHER
    assert classify_role("Network Admin", "", None) is RoleCategory.IT_SUPPORT
    # Sales di kategori IT situs sumber tetap muncul sebagai IT; tanpa kategori IT tetap Non-IT.
    assert classify_role("Jr Account Executive", "", "Teknologi Informasi & Komunikasi / Penjualan") is RoleCategory.IT_OTHER
    assert classify_role("Jr Account Executive", "", "Penjualan / Akun") is RoleCategory.SALES
    assert classify_role("Staf Unit Operasional Sistem – Database Admin") is RoleCategory.DATA


def test_detect_level():
    assert detect_level("Senior Backend Engineer") is ExperienceLevel.SENIOR
    assert detect_level("Backend Developer Intern") is ExperienceLevel.ENTRY
    assert detect_level("Backend Developer", "Minimal 5 tahun pengalaman") is ExperienceLevel.SENIOR
    assert detect_level("Backend Developer", "Pengalaman 1-2 years") is ExperienceLevel.JUNIOR
    assert detect_level("Backend Developer", "") is None
    # Ambil syarat terberat, dan abaikan angka tahun yang bukan soal pengalaman.
    req = "• 4+ years of hands-on experience in Java\n• 1+ year experience with Docker"
    assert detect_level("Backend Developer", req) is ExperienceLevel.MID
    assert detect_level("Backend Developer", "Perusahaan berdiri 10 tahun") is None


def test_score_penalizes_off_stack():
    java, reason, _ = score_job(RoleCategory.BACKEND, ExperienceLevel.JUNIOR, "Java Spring Boot, REST API, Docker")
    go, _, _ = score_job(RoleCategory.BACKEND, ExperienceLevel.JUNIOR, "Golang, REST API, Docker")
    assert java < go and "java" in reason


def test_score_prefers_target_junior_with_cv_skills():
    good, _, skills = score_job(RoleCategory.BACKEND, ExperienceLevel.JUNIOR, "Golang, PostgreSQL, Docker, REST API")
    senior, _, _ = score_job(RoleCategory.BACKEND, ExperienceLevel.SENIOR, "Golang, PostgreSQL, Docker")
    other, _, _ = score_job(RoleCategory.OTHER, None, "Excel")
    assert "Go" in skills and "PostgreSQL" in skills
    assert good >= 75 > senior and senior <= 30 and other < 50


def test_rupiah_short():
    assert rupiah_short(8_000_000, 12_000_000) == "Rp 8–12 jt"
    assert rupiah_short(8_500_000, 8_500_000) == "Rp 8,5 jt"
    assert rupiah_short(None, None) == "—"


def test_classify_role_it_titles_outside_it_category():
    # Judul IT yang dipasang pengiklan di kategori non-IT (mis. Kesehatan, Administrasi).
    hc = "Kesehatan & Medis / Perangkat Farmasi & Medis"
    assert classify_role("Application Support Staff (Jayapura)", None, hc) is RoleCategory.IT_SUPPORT
    assert classify_role("Electronic Data Processing Staff") is RoleCategory.IT_SUPPORT
    assert classify_role("Staff EDP") is RoleCategory.IT_SUPPORT
    assert classify_role("Data Science Analyst") is RoleCategory.DATA
    # tetap non-IT (bidangnya masing-masing)
    assert classify_role("Data Entry Intern") is RoleCategory.ADMIN
    assert classify_role("Application Engineer - Analytical Instruments") is RoleCategory.ENGINEERING
    assert classify_role("Digital Marketing") is RoleCategory.MARKETING
