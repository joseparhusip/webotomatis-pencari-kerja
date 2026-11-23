"""Isi surat lamaran: tertuju ke perusahaan yang benar, mengacu ke kebutuhan iklan, dan jujur soal skill."""

from app.models import Job, JobListing, Source, UserProfile
from app.models.enums import RoleCategory, WorkMode
from sample_texts import SAMPLE_EVIDENCE, SAMPLE_INTRO_ID, SAMPLE_STRENGTH_ID
from app.services.letter_template import (
    _join,
    build_template_letter,
    clean_title,
    job_targets,
    skill_key,
)


def _profile(**kw) -> UserProfile:
    base = dict(full_name="Nama Kandidat", email="kandidat@example.com", intro_id=SAMPLE_INTRO_ID,
                strength_id=SAMPLE_STRENGTH_ID,
                skills=["Go", "PostgreSQL", "PHP", "MySQL", "React", "Flutter", "Node.js"])
    base.update(kw)
    return UserProfile(**base)


def _job(**kw) -> Job:
    base = dict(title="Mobile Developer", company_name="PT Contoh Mobile", role_category=RoleCategory.MOBILE,
                location_city="Kota Bekasi", work_mode=WorkMode.ONSITE, apply_url="u", dedup_hash="h",
                description="Membangun aplikasi mobile untuk pelanggan.", requirements=None, skills=[],
                required_skills=["React Native", "Flutter", "Laravel", "Mobile Development", "Komunikasi yang baik"],
                benefits=[])
    base.update(kw)
    job = Job(**base)
    job.listings = [JobListing(source=Source(name="glints"))]
    return job


def test_header_addresses_company_and_city():
    letter = build_template_letter(_job(), _profile(), "id", SAMPLE_EVIDENCE)
    assert letter.startswith("Perihal: Lamaran Pekerjaan sebagai Mobile Developer\n\n"
                             "Yth. Bapak/Ibu Tim Rekrutmen\nPT Contoh Mobile\ndi Bekasi\n\nDengan hormat,")
    assert "melalui Glints" in letter


def test_remote_job_is_addressed_di_tempat():
    letter = build_template_letter(_job(work_mode=WorkMode.REMOTE), _profile(), "id", SAMPLE_EVIDENCE)
    assert "\ndi Tempat\n" in letter


def test_only_owned_skills_are_claimed():
    have, gaps, _ = job_targets(_job(), _profile())
    assert "Flutter" in have and "Mobile Development" in have   # Flutter → mobile development wajar
    assert "React Native" not in have and "Laravel" not in have  # React ≠ React Native, PHP ≠ Laravel
    assert gaps == ["React Native", "Laravel"]                   # soft skill tidak disebut sebagai kekurangan


def test_letter_mentions_requirements_and_is_honest_about_gaps():
    letter = build_template_letter(_job(), _profile(), "id", SAMPLE_EVIDENCE)
    assert "Posisi ini membutuhkan penguasaan Flutter" in letter
    assert "Untuk React Native dan Laravel yang juga disebutkan dalam kualifikasi" in letter
    fit = letter.split("Posisi ini membutuhkan penguasaan")[1].split(".")[0]
    assert "React Native" not in fit and "Laravel" not in fit


def test_evidence_matches_role():
    backend = _job(title="Backend Developer", role_category=RoleCategory.BACKEND,
                   required_skills=["PostgreSQL", "Go"])
    letter = build_template_letter(backend, _profile(), "id", SAMPLE_EVIDENCE)
    fit = letter.split("Dengan hormat,")[1]
    assert "MedCredix" in fit
    assert fit.index("MedCredix") < fit.index("Atas perhatian")


def test_indonesian_list_follows_puebi():
    assert _join(["A", "B", "C"], "id") == "A, B, dan C"
    assert _join(["A", "B"], "id") == "A dan B"
    assert _join(["A", "B", "C"], "en") == "A, B, and C"


def test_skill_key_normalisation():
    assert skill_key("React.js") == skill_key("ReactJS") == skill_key("react") == "react"
    assert skill_key("Golang") == "go" and skill_key("Tailwind CSS") == "tailwind"
    assert skill_key("React Native") != skill_key("React")


def test_clean_title_removes_ad_noise():
    assert clean_title("URGENT Backend Developer (ASAP)") == "Backend Developer"
    assert clean_title("PROGRAMMER  STAF IT") == "Programmer Staf IT"
    assert clean_title("Full Stack Developer (Golang, React.js & Next.js)") == "Full Stack Developer (Golang, React.js & Next.js)"


def test_position_from_job_title_in_every_letter_and_check():
    from app.models import Application
    from app.models.enums import ApplyMode
    from app.services.letters import can_send, default_subject, run_checks

    job = _job(title="PROGRAMMER  STAF", role_category=RoleCategory.FULLSTACK)
    p = _profile()
    letter = build_template_letter(job, p, "id", SAMPLE_EVIDENCE)
    assert letter.startswith("Perihal: Lamaran Pekerjaan sebagai Programmer Staf\n\n")
    assert default_subject(job, p, "id") == "Lamaran Pekerjaan - Programmer Staf - Nama Kandidat"

    app = Application(cover_letter_text=letter, apply_mode=ApplyMode.PORTAL, email_subject="x")
    assert any(c.level == "ok" and "Programmer Staf" in c.text for c in run_checks(app, job, p, True, 0))

    # Surat yang tidak menyebut posisi sama sekali tidak boleh dikirim.
    tanpa_posisi = letter.replace("Programmer Staf", "posisi tersebut")
    app.cover_letter_text = tanpa_posisi
    checks = run_checks(app, job, p, True, 0)
    assert not can_send(checks) and any("belum disebut" in c.text for c in checks)
