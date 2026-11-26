"""Halaman Swipe: antrean lowongan yang belum diputuskan + green flag per lowongan.

Keputusan memakai kolom job.status yang sudah ada (tanpa migrasi):
  geser kanan → saved, geser kiri → hidden, urungkan → seen.
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Job
from app.models.enums import EmploymentType, ExperienceLevel, JobStatus, WorkMode

UNDECIDED = (JobStatus.NEW, JobStatus.SEEN)
DECIDED = (JobStatus.SAVED, JobStatus.HIDDEN)
MAX_SKILLS = 6
MAX_BENEFITS = 4
FRESH_HOURS = 24


def queue_condition(now: datetime | None = None):
    now = now or datetime.now(UTC)
    return and_(Job.status.in_(UNDECIDED), or_(Job.expires_at.is_(None), Job.expires_at >= now))


QUEUE_ORDER = (Job.match_score.desc().nulls_last(), Job.posted_at.desc().nulls_last(), Job.id.desc())


async def queue_count(session: AsyncSession) -> int:
    return await session.scalar(select(func.count(Job.id)).where(queue_condition())) or 0


async def next_job(session: AsyncSession) -> Job | None:
    return await session.scalar(select(Job).where(queue_condition()).order_by(*QUEUE_ORDER).limit(1))


def source_url(job: Job) -> str | None:
    """Halaman lowongan di situs sumber: apply_url, atau URL listing pertama."""
    if (job.apply_url or "").startswith(("http://", "https://")):
        return job.apply_url
    for listing in job.listings or []:
        if (listing.url or "").startswith(("http://", "https://")):
            return listing.url
    return None


def decide(job: Job, action: str) -> bool:
    """Terapkan keputusan swipe. False = tidak diubah (mis. sudah dilamar)."""
    if action in ("like", "nope"):
        if job.status not in UNDECIDED:
            return False
        job.status = JobStatus.SAVED if action == "like" else JobStatus.HIDDEN
        return True
    if action == "undo":
        if job.status not in DECIDED:
            return False
        job.status = JobStatus.SEEN
        return True
    raise ValueError(action)


def _value(v) -> str | None:
    return getattr(v, "value", v)


def matched_skills(job: Job, my_skills) -> list[str]:
    """Skill yang diminta lowongan dan ada di profil/CV (tanpa beda huruf besar-kecil), maks. 6."""
    mine = {s.strip().casefold() for s in (my_skills or []) if s and s.strip()}
    out, seen = [], set()
    for s in list(job.required_skills or []) + list(job.skills or []):
        key = (s or "").strip().casefold()
        if key and key in mine and key not in seen:
            seen.add(key)
            out.append(s.strip())
    return out[:MAX_SKILLS]


def green_flags(job: Job, my_skills=(), now: datetime | None = None) -> list[dict]:
    """Poin positif yang terbaca langsung dari data lowongan. Tidak ada tebakan: data kosong → flag tidak muncul.

    Tiap flag: key (jenis/ikon), text (kalimat lengkap), label + value (petak ringkas), items (daftar, bila ada).
    """
    from app.web.templating import date_id, reltime, rupiah_long, rupiah_short

    now = now or datetime.now(UTC)
    flags: list[dict] = []

    def add(key: str, text: str, label: str, value: str = "", items: list[str] | None = None) -> None:
        flags.append({"key": key, "text": text, "label": label, "value": value, "items": items or []})

    if job.salary_min or job.salary_max:
        long_pay, short_pay = rupiah_long(job.salary_min, job.salary_max), rupiah_short(job.salary_min, job.salary_max)
        if job.salary_is_estimated:
            add("salary", f"Perkiraan gaji {long_pay}", "Perkiraan gaji", short_pay)
        else:
            add("salary", f"Gaji dicantumkan: {long_pay}", "Gaji dicantumkan", short_pay)
    mode = _value(job.work_mode)
    if mode == WorkMode.REMOTE.value:
        add("remote", "Bisa kerja remote", "Kerja", "Remote")
    elif mode == WorkMode.HYBRID.value:
        add("hybrid", "Hybrid: sebagian bisa dari rumah", "Kerja", "Hybrid")
    if _value(job.experience_level) in (ExperienceLevel.ENTRY.value, ExperienceLevel.JUNIOR.value):
        add("junior", "Ramah fresh graduate / junior", "Level", "Fresh grad / junior")
    skills = matched_skills(job, my_skills)
    if skills:
        add("skills", "Skill kamu yang dicari: " + ", ".join(skills), "Skill kamu yang dicari", items=skills)
    benefits = [b.strip() for b in (job.benefits or []) if b and b.strip()][:MAX_BENEFITS]
    if benefits:
        add("benefits", "Benefit: " + ", ".join(benefits), "Benefit", items=benefits)
    if job.hr_email:
        add("email", "Bisa lamar langsung lewat email HR", "Lamar", "Langsung ke email HR")
    if job.posted_at and timedelta(0) <= now - job.posted_at < timedelta(hours=FRESH_HOURS):
        add("fresh", "Baru diposting, lamar duluan", "Baru diposting", reltime(job.posted_at) + " lalu")
    if _value(job.employment_type) == EmploymentType.FULLTIME.value:
        add("fulltime", "Karyawan penuh waktu", "Status", "Karyawan tetap")
    if job.expires_at and job.expires_at >= now:
        add("deadline", f"Batas lamar {date_id(job.expires_at)}", "Batas lamar", date_id(job.expires_at))
    return flags
