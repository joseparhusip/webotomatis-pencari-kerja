"""Halaman Swipe: keputusan like/nope/undo (endpoint) dan green flag per lowongan."""

from datetime import UTC, datetime, timedelta

import httpx
import pytest

from app.core.db import get_session
from app.main import app
from app.models import Job
from app.models.enums import EmploymentType, ExperienceLevel, JobStatus, WorkMode
from app.services import swipe
from app.web import swipe as swipe_web

@pytest.fixture
def anyio_backend():
    return "asyncio"


NOW = datetime(2026, 11, 26, 9, 0, tzinfo=UTC)


def make_job(**kw) -> Job:
    base = dict(id=1, title="Frontend Developer", company_name="PT Contoh Data", apply_url="https://contoh.test/loker/1",
                status=JobStatus.NEW, required_skills=[], skills=[], benefits=[], listings=[])
    base.update(kw)
    return Job(**base)


# ---------- green flag ----------

def test_green_flag_lengkap():
    job = make_job(salary_min=6_000_000, salary_max=8_000_000, work_mode=WorkMode.REMOTE,
                   experience_level=ExperienceLevel.JUNIOR, required_skills=["React", "TypeScript", "Kotlin"],
                   skills=["react", "Docker"], benefits=["BPJS", "Laptop", "Bonus", "Asuransi", "Makan siang"],
                   hr_email="hr@contoh.test", posted_at=NOW - timedelta(hours=3),
                   employment_type=EmploymentType.FULLTIME, expires_at=NOW + timedelta(days=10))
    flags = {f["key"]: f["text"] for f in swipe.green_flags(job, ["REACT", "typescript", "docker"], now=NOW)}
    assert list(flags) == ["salary", "remote", "junior", "skills", "benefits", "email", "fresh", "fulltime", "deadline"]
    assert flags["salary"] == "Gaji dicantumkan: Rp 6.000.000 – Rp 8.000.000 / bulan"
    assert flags["skills"] == "Skill kamu yang dicari: React, TypeScript, Docker"   # tanpa duplikat react/React
    assert flags["benefits"] == "Benefit: BPJS, Laptop, Bonus, Asuransi"            # maks. 4
    assert flags["deadline"] == "Batas lamar 6 Des 2026"


def test_green_flag_perkiraan_hybrid_dan_kosong():
    est = make_job(salary_min=5_000_000, salary_is_estimated=True, work_mode=WorkMode.HYBRID)
    flags = {f["key"]: f["text"] for f in swipe.green_flags(est, [], now=NOW)}
    assert flags == {"salary": "Perkiraan gaji Rp 5.000.000 / bulan", "hybrid": "Hybrid: sebagian bisa dari rumah"}

    plain = make_job(work_mode=WorkMode.ONSITE, experience_level=ExperienceLevel.SENIOR,
                     posted_at=NOW - timedelta(days=3), employment_type=EmploymentType.CONTRACT)
    assert swipe.green_flags(plain, ["React"], now=NOW) == []


def test_skill_cocok_maksimal_enam():
    skills = [f"S{i}" for i in range(10)]
    assert swipe.matched_skills(make_job(required_skills=skills), [s.lower() for s in skills]) == skills[:6]


def test_url_sumber():
    assert swipe.source_url(make_job()) == "https://contoh.test/loker/1"

    class L:
        url = "https://portal.test/job/9"
    assert swipe.source_url(make_job(apply_url="")) is None
    job = make_job(apply_url="")
    job.__dict__["listings"] = [L()]  # objek listing tiruan, tanpa sesi DB
    assert swipe.source_url(job) == "https://portal.test/job/9"


def test_keputusan_tidak_menimpa_lamaran():
    job = make_job(status=JobStatus.APPLIED)
    assert not swipe.decide(job, "like") and not swipe.decide(job, "undo")
    assert job.status == JobStatus.APPLIED


# ---------- endpoint ----------

class FakeSession:
    def __init__(self, jobs):
        self.jobs = {j.id: j for j in jobs}
        self.commits = 0

    async def get(self, model, job_id):
        return self.jobs.get(job_id)

    async def commit(self):
        self.commits += 1


@pytest.fixture
def env(monkeypatch):
    jobs = [make_job(id=1, title="Frontend Developer", match_score=90, status=JobStatus.NEW),
            make_job(id=2, title="Backend Engineer", match_score=70, status=JobStatus.SEEN,
                     apply_url="https://contoh.test/loker/2")]
    session = FakeSession(jobs)

    def queue():
        return sorted((j for j in jobs if j.status in swipe.UNDECIDED), key=lambda j: -(j.match_score or 0))

    async def next_job(_s):
        q = queue()
        return q[0] if q else None

    async def queue_count(_s):
        return len(queue())

    async def no_profile(_s):
        return None

    async def shell(_s):
        return {}

    monkeypatch.setattr(swipe, "next_job", next_job)
    monkeypatch.setattr(swipe, "queue_count", queue_count)
    monkeypatch.setattr(swipe_web, "get_profile", no_profile)
    monkeypatch.setattr(swipe_web, "shell_context", shell)

    async def override():
        yield session

    app.dependency_overrides[get_session] = override
    yield session, jobs
    app.dependency_overrides.pop(get_session, None)


def client():
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


@pytest.mark.anyio
async def test_halaman_swipe_menampilkan_skor_tertinggi(env):
    async with client() as c:
        r = await c.get("/swipe")
    assert r.status_code == 200
    assert 'data-job="1"' in r.text and "Frontend Developer" in r.text
    assert "2</b> lowongan menunggu keputusanmu" in r.text
    assert 'aria-current="page">Swipe' in r.text


@pytest.mark.anyio
async def test_like_nope_undo(env):
    session, jobs = env
    async with client() as c:
        r = await c.post("/swipe/1/like")
        assert jobs[0].status == JobStatus.SAVED
        assert 'data-last="1"' in r.text and 'data-job="2"' in r.text   # kartu berikutnya langsung muncul
        assert 'data-url="https://contoh.test/loker/2"' in r.text

        r = await c.post("/swipe/2/nope")
        assert jobs[1].status == JobStatus.HIDDEN
        assert 'data-last="2"' in r.text and "Semua lowongan sudah kamu putuskan" in r.text

        r = await c.post("/swipe/2/undo")
        assert jobs[1].status == JobStatus.SEEN
        assert 'data-job="2"' in r.text and 'data-last=""' in r.text    # kartu yang diurungkan tampil lagi
    assert session.commits == 3


@pytest.mark.anyio
async def test_lowongan_tidak_ada(env):
    async with client() as c:
        r = await c.post("/swipe/999/like")
    assert r.status_code == 404
