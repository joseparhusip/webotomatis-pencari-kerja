import logging
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import SessionLocal
from app.models import Application, CvDocument, Job
from app.models.enums import ApplicationStatus, ApplyMode, JobStatus
from app.services import mailer
from app.services.letters import can_send, default_subject, detect_language, generate_letter, run_checks
from app.services.profile import cv_path, default_cv, evidence_pool, get_profile

log = logging.getLogger(__name__)
WIB = ZoneInfo("Asia/Jakarta")

# Status yang berarti lamaran untuk lowongan itu sudah/akan keluar.
ACTIVE_STATUSES = (
    ApplicationStatus.QUEUED, ApplicationStatus.SENT, ApplicationStatus.REPLIED,
    ApplicationStatus.INTERVIEW, ApplicationStatus.OFFER,
)
TRACKER_ORDER = [
    ApplicationStatus.INTERVIEW, ApplicationStatus.OFFER, ApplicationStatus.REPLIED,
    ApplicationStatus.QUEUED, ApplicationStatus.FAILED, ApplicationStatus.SENT, ApplicationStatus.REJECTED,
]


def _start_of_today_utc() -> datetime:
    now = datetime.now(WIB)
    return now.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(UTC)


async def emails_sent_today(session: AsyncSession) -> int:
    return await session.scalar(
        select(func.count(Application.id)).where(
            Application.apply_mode == ApplyMode.EMAIL,
            Application.status.in_(ACTIVE_STATUSES),
            Application.sent_at >= _start_of_today_utc(),
        )
    ) or 0


async def prepare_drafts(session: AsyncSession, job_ids: list[int]) -> tuple[list[Application], list[str]]:
    """Buat draft untuk tiap lowongan. Kembalikan (draft, catatan lowongan yang dilewati)."""
    profile = await get_profile(session)
    drafts: list[Application] = []
    skipped: list[str] = []
    for job_id in dict.fromkeys(job_ids):
        job = await session.get(Job, job_id)
        if job is None:
            continue
        existing = await session.scalar(
            select(Application).where(Application.job_id == job.id).order_by(Application.id.desc()).limit(1)
        )
        if existing and existing.status in ACTIVE_STATUSES:
            skipped.append(f"{job.title} — {job.company_name} (sudah dilamar)")
            continue
        if existing and existing.status in (ApplicationStatus.DRAFT, ApplicationStatus.FAILED):
            drafts.append(existing)
            continue
        lang = detect_language(job)
        cv = await default_cv(session, job.role_category.value if job.role_category else None)
        app = Application(
            job_id=job.id,
            cv_id=cv.id if cv else None,
            apply_mode=job.apply_mode,
            sent_to=job.hr_email,
            email_subject=default_subject(job, profile, lang),
            letter_language=lang,
            status=ApplicationStatus.DRAFT,
        )
        session.add(app)
        drafts.append(app)
    await session.commit()
    return drafts, skipped


async def draft_ids(session: AsyncSession) -> list[int]:
    return list(await session.scalars(
        select(Application.id).where(Application.status.in_([ApplicationStatus.DRAFT, ApplicationStatus.FAILED]))
        .order_by(Application.id)
    ))


async def fill_letter(session: AsyncSession, app: Application) -> str | None:
    """Buat surat untuk draft. Kembalikan catatan bila AI tidak dipakai/gagal (untuk ditampilkan)."""
    profile = await get_profile(session)
    if profile is None:
        return None
    cv_text = app.cv.parsed_text if app.cv else None
    result = await generate_letter(app.job, profile, cv_text, app.letter_language, await evidence_pool(session))
    app.cover_letter_text, app.letter_source = result.text, result.source
    app.last_error = None
    await session.commit()
    return result.note


async def checks_for(session: AsyncSession, app: Application):
    profile = await get_profile(session)
    cv_ok = bool(app.cv and cv_path(app.cv).is_file())
    return run_checks(app, app.job, profile, cv_ok, await emails_sent_today(session))


async def next_send_delay(session: AsyncSession) -> int:
    """Detik yang perlu ditunggu supaya jarak antar email >= MIN_SECONDS_BETWEEN_EMAILS."""
    last = await session.scalar(
        select(func.max(Application.sent_at)).where(Application.apply_mode == ApplyMode.EMAIL)
    )
    queued = await session.scalar(
        select(func.count(Application.id)).where(Application.status == ApplicationStatus.QUEUED)
    ) or 0
    gap = get_settings().min_seconds_between_emails
    wait = 0
    if last:
        wait = max(0, gap - int((datetime.now(UTC) - last).total_seconds()))
    return wait + queued * gap


async def mark_job_applied(session: AsyncSession, job: Job) -> None:
    job.status = JobStatus.APPLIED


async def send_application(app_id: int) -> str:
    """Dijalankan di worker. Validasi ulang lalu kirim email."""
    async with SessionLocal() as session:
        app = await session.get(Application, app_id)
        if app is None or app.status != ApplicationStatus.QUEUED:
            return "skip"
        checks = await checks_for(session, app)
        # Saat di antrean, "kuota habis" dihitung tanpa lamaran ini sendiri — run_checks memakai status aktif.
        if not can_send(checks):
            app.status = ApplicationStatus.FAILED
            app.last_error = "; ".join(c.text for c in checks if c.level == "error")
            await session.commit()
            return "failed"

        profile = await get_profile(session)
        try:
            msg = mailer.build_message(
                from_name=profile.full_name,
                reply_to=profile.email,
                to=app.sent_to.strip(),
                subject=app.email_subject.strip(),
                body=app.cover_letter_text,
                attachment=cv_path(app.cv),
                attachment_name=f"CV - {profile.full_name}.pdf",
            )
            app.gmail_message_id = await mailer.send(msg)
        except Exception as exc:  # SMTP/IO — simpan pesan supaya terlihat di UI
            log.exception("gagal kirim lamaran %s", app_id)
            app.status = ApplicationStatus.FAILED
            app.last_error = f"{type(exc).__name__}: {exc}"
            await session.commit()
            return "failed"

        app.status = ApplicationStatus.SENT
        app.sent_at = datetime.now(UTC)
        app.last_error = None
        await mark_job_applied(session, app.job)
        await session.commit()
        return "sent"


async def tracker(session: AsyncSession) -> dict[ApplicationStatus, list[Application]]:
    rows = await session.scalars(
        select(Application).where(Application.status != ApplicationStatus.DRAFT)
        .order_by(Application.sent_at.desc().nulls_last(), Application.id.desc())
    )
    groups: dict[ApplicationStatus, list[Application]] = {s: [] for s in TRACKER_ORDER}
    for app in rows:
        groups.setdefault(app.status, []).append(app)
    return groups


async def drafts(session: AsyncSession) -> list[Application]:
    return list(await session.scalars(
        select(Application).where(Application.status == ApplicationStatus.DRAFT).order_by(Application.id)
    ))


async def week_stats(session: AsyncSession) -> dict:
    since = datetime.now(UTC) - timedelta(days=7)
    base = select(func.count(Application.id)).where(Application.sent_at >= since)
    sent = await session.scalar(base.where(Application.status.in_(ACTIVE_STATUSES + (ApplicationStatus.REJECTED,)))) or 0
    replied = await session.scalar(base.where(Application.status.in_(
        [ApplicationStatus.REPLIED, ApplicationStatus.INTERVIEW, ApplicationStatus.OFFER, ApplicationStatus.REJECTED]
    ))) or 0
    interview = await session.scalar(base.where(Application.status.in_(
        [ApplicationStatus.INTERVIEW, ApplicationStatus.OFFER]
    ))) or 0
    return {"sent": sent, "replied": replied, "interview": interview,
            "reply_rate": round(replied * 100 / sent) if sent else 0}


async def cvs_by_id(session: AsyncSession) -> dict[int, CvDocument]:
    return {c.id: c for c in await session.scalars(select(CvDocument))}
