from email import message_from_bytes

import pytest

from app.core.config import get_settings
from app.models import Application, Job, UserProfile
from app.models.enums import ApplyMode, RoleCategory
from app.services import mailer
from sample_texts import SAMPLE_EVIDENCE, SAMPLE_INTRO_EN, SAMPLE_INTRO_ID, SAMPLE_STRENGTH_EN, SAMPLE_STRENGTH_ID
from app.services.letter_template import (
    Evidence,
    build_template_letter,
    pick_evidence,
)
from app.services.profile import ProfileError, save_uploaded_cv, split_list
from app.services.letters import can_send, detect_language, run_checks, word_count


def _job(**kw) -> Job:
    base = dict(title="Backend Developer", company_name="PT Contoh Fintech", role_category=RoleCategory.BACKEND,
                skills=["Go", "PostgreSQL"], description="Kami mencari backend developer yang memiliki pengalaman "
                "dengan Go dan PostgreSQL untuk tim kami di Jakarta dan mampu bekerja dengan tim.",
                requirements=None, apply_url="https://contoh/1")
    base.update(kw)
    return Job(**base)


def _profile() -> UserProfile:
    return UserProfile(
        full_name="Nama Kandidat", email="kandidat@example.com", phone="+62800000000",
        intro_id=SAMPLE_INTRO_ID, intro_en=SAMPLE_INTRO_EN,
        strength_id=SAMPLE_STRENGTH_ID, strength_en=SAMPLE_STRENGTH_EN,
        skills=["Go", "PostgreSQL", "PHP", "MySQL", "React", "Flutter", "Node.js"],
    )


def _app(letter: str, mode=ApplyMode.EMAIL, to="hr@contoh.co.id") -> Application:
    return Application(cover_letter_text=letter, apply_mode=mode, sent_to=to, email_subject="Lamaran")


@pytest.fixture
def smtp_on(monkeypatch):
    monkeypatch.setattr(get_settings(), "smtp_user", "me@example.com")
    monkeypatch.setattr(get_settings(), "smtp_password", "x")


def test_template_letter_passes_own_checks(smtp_on):
    job = _job()
    letter = build_template_letter(job, _profile(), "id", SAMPLE_EVIDENCE)
    checks = run_checks(_app(letter), job, _profile(), cv_file_ok=True, emails_today=0)
    assert can_send(checks), [c.text for c in checks if c.level != "ok"]
    assert 230 <= word_count(letter) <= 360
    assert letter.startswith("Perihal: Lamaran Pekerjaan sebagai Backend Developer\n\n"
                             "Yth. Bapak/Ibu Tim Rekrutmen\nPT Contoh Fintech\ndi Tempat\n\nDengan hormat,")
    assert "Atas perhatian dan waktu yang Bapak/Ibu berikan" in letter
    assert letter.rstrip().endswith("kandidat@example.com")


def test_template_picks_evidence_matching_category():
    mobile = _job(title="Flutter Developer", role_category=RoleCategory.MOBILE, skills=["Flutter", "Dart"])
    assert "Flutter" in pick_evidence(mobile, SAMPLE_EVIDENCE)[0].id


def test_checks_block_missing_company_and_placeholder(smtp_on):
    job = _job()
    checks = run_checks(_app("Yth. [Nama HRD], saya melamar posisi Backend Developer."), job, _profile(), True, 0)
    errors = [c.text for c in checks if c.level == "error"]
    assert any("Nama perusahaan" in e for e in errors)
    assert any("placeholder" in e for e in errors)
    assert not can_send(checks)


def test_company_match_ignores_legal_prefix(smtp_on):
    job = _job(company_name="PT. Contoh Fintech Tbk")
    letter = build_template_letter(_job(company_name="Contoh Fintech"), _profile(), "id", SAMPLE_EVIDENCE)
    checks = run_checks(_app(letter), job, _profile(), True, 0)
    assert any(c.level == "ok" and "Nama perusahaan" in c.text for c in checks)


def test_cliches_and_quota_and_smtp(monkeypatch):
    monkeypatch.setattr(get_settings(), "smtp_user", "")
    job = _job()
    letter = build_template_letter(job, _profile(), "id", SAMPLE_EVIDENCE) + "\nSaya sangat antusias!"
    checks = run_checks(_app(letter), job, _profile(), True, emails_today=999)
    texts = " | ".join(c.text for c in checks)
    assert "sangat antusias" in texts and "tanda seru" in texts
    assert "Kuota harian habis" in texts
    assert "SMTP belum diatur" in texts


def test_portal_mode_does_not_need_email(smtp_on):
    job = _job()
    letter = build_template_letter(job, _profile(), "id", SAMPLE_EVIDENCE)
    checks = run_checks(_app(letter, mode=ApplyMode.PORTAL, to=None), job, _profile(), cv_file_ok=False, emails_today=0)
    assert can_send(checks)


def test_detect_language():
    assert detect_language(_job()) == "id"
    en = _job(description="We are looking for a backend engineer with experience in Go and the ability to work "
                          "with our team. You will have ownership of services for the platform and the data.")
    assert detect_language(en) == "en"
    # Baris ringkasan buatan connector tidak dihitung; sinyal sedikit → id
    assert detect_language(_job(description="Pengalaman: 1–3 tahun\nSkill: Go")) == "id"


def test_build_message_attaches_cv(tmp_path, smtp_on):
    cv = tmp_path / "cv.pdf"
    cv.write_bytes(b"%PDF-1.4 test")
    msg = mailer.build_message(from_name="Nama Kandidat", reply_to="kandidat@example.com", to="hr@contoh.co.id",
                               subject="Lamaran", body="Isi surat", attachment=cv, attachment_name="CV - Nama.pdf")
    parsed = message_from_bytes(msg.as_bytes())
    assert parsed["From"] == "Nama Kandidat <me@example.com>"
    assert parsed["Reply-To"] == "kandidat@example.com"
    files = [p.get_filename() for p in parsed.walk() if p.get_filename()]
    assert files == ["CV - Nama.pdf"]


def test_profile_evidence_pool_is_used_and_empty_pool_means_none():
    job = _job()
    own = [Evidence("Bukti milik saya tentang Go.", "My own Go proof.", frozenset({"backend"}), frozenset({"Go"}))]
    letter = build_template_letter(job, _profile(), "id", own)
    assert "Bukti milik saya tentang Go." in letter and "MedCredix" not in letter
    assert pick_evidence(job, [], 3) == []          # semua bukti dinonaktifkan → tidak pakai bawaan
    assert pick_evidence(job, None, 3) == []         # profil belum punya bukti → tidak ada klaim pengalaman


def test_template_without_profile_sentences_skips_them():
    bare = UserProfile(full_name="Nama", email="n@example.com")
    letter = build_template_letter(_job(), bare, "en", SAMPLE_EVIDENCE)
    body = letter.split("Sincerely,")[0]  # ruang tanda tangan (2 baris kosong) memang disengaja
    assert "None" not in letter and "\n\n\n" not in body
    assert letter.endswith("Sincerely,\n\n\nNama\nn@example.com")
    assert letter.startswith("Re: Application for the Backend Developer position\n\nDear Hiring Team at PT Contoh Fintech,")


def test_split_list():
    assert split_list("Go, PostgreSQL,\nDocker , ") == ["Go", "PostgreSQL", "Docker"]


@pytest.mark.asyncio
async def test_upload_validation_rejects_non_pdf_and_large():
    with pytest.raises(ProfileError, match="PDF"):
        await save_uploaded_cv(None, _profile(), "cv.pdf", b"bukan pdf", "x")
    with pytest.raises(ProfileError, match="terlalu besar"):
        await save_uploaded_cv(None, _profile(), "cv.pdf", b"%PDF" + b"0" * (6 * 1024 * 1024), "x")
    with pytest.raises(ProfileError, match="kosong"):
        await save_uploaded_cv(None, _profile(), "cv.pdf", b"", "x")


def test_signature_name_email_portfolio_without_phone():
    from app.services.letter_template import signature

    p = UserProfile(full_name="Nama Kandidat", email="kandidat@example.com", phone="+62800000000",
                    portfolio_url="https://www.kandidat.com/")
    assert signature(p, "id") == "Hormat saya,\n\n\nNama Kandidat\nkandidat@example.com\nkandidat.com"
    assert "+62" not in build_template_letter(_job(), p, "id", SAMPLE_EVIDENCE)
