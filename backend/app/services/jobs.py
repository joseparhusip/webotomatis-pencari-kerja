"""Query daftar lowongan + filter. Dipakai router web (dan nanti router /api)."""

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta

import re

from sqlalchemy import Select, String, and_, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Job, JobListing, ScrapeRun, Source
from app.models.enums import JobStatus, RoleCategory, WorkMode
from app.services.wilayah import JABODETABEK_PROVINCE, JABODETABEK_REGENCIES

PAGE_SIZE = 50
JABODETABEK = ("jakarta", "bekasi", "depok", "bogor", "tangerang")


@dataclass
class JobFilters:
    q: str = ""
    roles: list[str] = field(default_factory=list)
    modes: list[str] = field(default_factory=list)
    levels: list[str] = field(default_factory=list)  # "unknown" = level tidak disebut
    loc: str = ""  # "", "jabodetabek", "remote", "jabodetabek_remote"
    salary_min: int | None = None
    min_score: int | None = None
    posted_days: int | None = None
    statuses: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)  # kosong = semua sumber
    sort: str = "score"
    page: int = 1

    @classmethod
    def defaults(cls) -> "JobFilters":
        """Filter awal sesuai profil target (ARCHITECTURE.md §3)."""
        return cls(
            # Semua pekerjaan IT; Non-IT tidak dicentang.
            roles=[c.value for c in RoleCategory if c != RoleCategory.OTHER],
            levels=["entry", "junior", "unknown"],
            loc="jabodetabek_remote",
            posted_days=3,
            statuses=[JobStatus.NEW.value, JobStatus.SEEN.value, JobStatus.SAVED.value],
        )

    def to_query(self, **override) -> list[tuple[str, str]]:
        """Pasangan query string (untuk link paginasi)."""
        data = {**self.__dict__, **override}
        pairs: list[tuple[str, str]] = [("f", "1")]
        for key, param in (("roles", "role"), ("modes", "mode"), ("levels", "level"), ("statuses", "status"), ("sources", "source")):
            pairs += [(param, v) for v in data[key]]
        for key, param in (("q", "q"), ("loc", "loc"), ("salary_min", "salary_min"),
                           ("min_score", "min_score"), ("posted_days", "posted"),
                           ("sort", "sort"), ("page", "page")):
            if data[key] not in (None, ""):
                pairs.append((param, str(data[key])))
        return pairs


def region_clause(code: str):
    """Provinsi ("31") = semua kab/kota di dalamnya + lowongan yang hanya menyebut provinsinya."""
    if "." in code:
        return Job.region_code == code
    return or_(Job.region_code == code, Job.region_code.like(f"{code}.%"))


def _location_clause(loc: str):
    city_match = or_(
        region_clause(JABODETABEK_PROVINCE),
        Job.region_code.in_(JABODETABEK_REGENCIES),
        # cadangan untuk lokasi yang belum terpetakan ke kode wilayah
        and_(Job.region_code.is_(None), or_(*[Job.location_city.ilike(f"%{c}%") for c in JABODETABEK],
                                            Job.location_province.ilike("%jakarta%"))),
    )
    remote = Job.work_mode == WorkMode.REMOTE
    if loc.startswith("wil:"):  # satu provinsi / kabupaten / kota dari dropdown
        code = loc[4:]
        return region_clause(code) if re.fullmatch(r"\d{2}(\.\d{2})?", code) else None
    return {"jabodetabek": city_match, "remote": remote, "jabodetabek_remote": or_(city_match, remote)}.get(loc)


async def region_counts(session: AsyncSession) -> dict[str, int]:
    """Jumlah lowongan aktif per kode wilayah (untuk angka di dropdown lokasi)."""
    now = datetime.now(UTC)
    rows = await session.execute(
        select(Job.region_code, func.count(Job.id))
        .where(Job.region_code.is_not(None), Job.status != JobStatus.HIDDEN,
               or_(Job.expires_at.is_(None), Job.expires_at >= now))
        .group_by(Job.region_code))
    return dict(rows.all())


_STOP = {"pt", "cv", "tbk", "dan", "and", "di", "the", "of", "&"}


def search_terms(q: str) -> list[str]:
    """'PT Tri Mentari Niaga(Bintang Racing Team)' → ['tri', 'mentari', 'niaga', 'bintang', 'racing', 'team']."""
    words = re.findall(r"[\w.+#]+", q.lower())
    return [w.strip(".") for w in words if w.strip(".") and w.strip(".") not in _STOP][:12]


def search_clause(q: str):
    """Setiap kata harus muncul di salah satu: judul, perusahaan, deskripsi, kualifikasi, skill,
    kategori asli, atau data mentah situs sumber (mis. nama pengiklan JobStreet)."""
    raw_text = (select(JobListing.job_id)
                .where(JobListing.job_id == Job.id)
                .correlate(Job))
    per_word = []
    for w in search_terms(q) or [q.strip().lower()]:
        like = f"%{w}%"
        per_word.append(or_(
            Job.title.ilike(like), Job.company_name.ilike(like), Job.description.ilike(like),
            Job.requirements.ilike(like), Job.source_category.ilike(like),
            func.array_to_string(Job.required_skills, " ").ilike(like),
            raw_text.where(cast(JobListing.raw_payload, String).ilike(like)).exists(),
        ))
    return and_(*per_word)


def build_query(f: JobFilters) -> Select:
    conds = []
    if f.q:
        conds.append(search_clause(f.q))
    if f.roles:
        conds.append(Job.role_category.in_(f.roles))
    if f.modes:
        conds.append(Job.work_mode.in_(f.modes))
    if f.levels:
        known = [lv for lv in f.levels if lv != "unknown"]
        level_conds = [Job.experience_level.in_(known)] if known else []
        if "unknown" in f.levels:
            level_conds.append(Job.experience_level.is_(None))
        conds.append(or_(*level_conds))
    if (clause := _location_clause(f.loc)) is not None:
        conds.append(clause)
    if f.salary_min:
        conds.append(or_(Job.salary_max >= f.salary_min, Job.salary_max.is_(None)))
    if f.min_score:
        conds.append(Job.match_score >= f.min_score)
    if f.posted_days:
        since = datetime.now(UTC) - timedelta(days=f.posted_days)
        conds.append(or_(Job.posted_at >= since, Job.posted_at.is_(None)))
    if f.sources:
        conds.append(Job.id.in_(
            select(JobListing.job_id).join(Source, Source.id == JobListing.source_id).where(Source.name.in_(f.sources))
        ))
    if f.statuses:
        conds.append(Job.status.in_(f.statuses))
    # Lowongan yang batas lamarannya sudah lewat tidak ditampilkan.
    conds.append(or_(Job.expires_at.is_(None), Job.expires_at >= datetime.now(UTC)))
    return select(Job).where(and_(*conds))


async def list_jobs(session: AsyncSession, f: JobFilters) -> tuple[list[Job], int]:
    base = build_query(f)
    total = await session.scalar(select(func.count()).select_from(base.subquery())) or 0
    if f.sort == "newest":
        order = (Job.posted_at.desc().nulls_last(), Job.id.desc())
    else:
        order = (Job.match_score.desc().nulls_last(), Job.posted_at.desc().nulls_last(), Job.id.desc())
    rows = await session.scalars(
        base.order_by(*order).offset((max(f.page, 1) - 1) * PAGE_SIZE).limit(PAGE_SIZE)
    )
    return list(rows), total


async def search_outside_filters(session: AsyncSession, q: str) -> int:
    """Jumlah lowongan aktif yang cocok dengan kata pencarian tanpa filter lain."""
    now = datetime.now(UTC)
    return await session.scalar(select(func.count(Job.id)).where(
        search_clause(q), Job.status != JobStatus.HIDDEN, or_(Job.expires_at.is_(None), Job.expires_at >= now),
    )) or 0


_FILTER_LABELS = {
    "roles": "kategori", "levels": "level", "loc": "lokasi", "posted_days": "tanggal posting",
    "statuses": "status", "modes": "mode kerja", "salary_min": "gaji", "min_score": "skor", "sources": "sumber",
}


async def hidden_matches(session: AsyncSession, f: JobFilters, limit: int = 5) -> list[dict]:
    """Lowongan yang cocok dengan kata pencarian tetapi tidak tampil: filter mana yang menyembunyikannya.

    Tiap filter diuji sendiri-sendiri (kata pencarian + satu filter), jadi alasannya pasti, bukan tebakan.
    """
    if not f.q:
        return []
    now = datetime.now(UTC)
    jobs = list(await session.scalars(
        select(Job).where(search_clause(f.q), Job.status != JobStatus.HIDDEN,
                          or_(Job.expires_at.is_(None), Job.expires_at >= now))
        .order_by(Job.posted_at.desc().nulls_last()).limit(limit)
    ))
    if not jobs:
        return []
    ids = [j.id for j in jobs]
    blocked: dict[int, list[str]] = {i: [] for i in ids}
    for attr, label in _FILTER_LABELS.items():
        value = getattr(f, attr)
        if value in (None, "", []):
            continue
        single = JobFilters(q=f.q, **{attr: value})
        passing = set(await session.scalars(
            build_query(single).with_only_columns(Job.id).where(Job.id.in_(ids))))
        for i in ids:
            if i not in passing:
                blocked[i].append(label)
    return [{"job": j, "reasons": blocked[j.id]} for j in jobs]


async def job_counts(session: AsyncSession) -> dict:
    today = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    total = await session.scalar(select(func.count(Job.id))) or 0
    new_today = await session.scalar(select(func.count(Job.id)).where(Job.created_at >= today)) or 0
    return {"total": total, "new_today": new_today}


async def latest_runs(session: AsyncSession) -> list[ScrapeRun]:
    """Run terakhir per sumber."""
    latest = (
        select(ScrapeRun.source_id, func.max(ScrapeRun.id).label("id"))
        .group_by(ScrapeRun.source_id)
        .subquery()
    )
    rows = await session.scalars(
        select(ScrapeRun).join(latest, ScrapeRun.id == latest.c.id).order_by(ScrapeRun.source_id)
    )
    return list(rows)


RUNS_PER_PAGE = 20


async def recent_runs(session: AsyncSession, page: int = 1) -> tuple[list[ScrapeRun], int, int]:
    """Riwayat scraping per halaman. Kembalikan (baris, halaman sekarang, total halaman)."""
    total = await session.scalar(select(func.count(ScrapeRun.id))) or 0
    pages = max(1, -(-total // RUNS_PER_PAGE))
    page = min(max(page, 1), pages)
    rows = await session.scalars(
        select(ScrapeRun).order_by(ScrapeRun.id.desc())
        .offset((page - 1) * RUNS_PER_PAGE).limit(RUNS_PER_PAGE)
    )
    return list(rows), page, pages


async def close_interrupted_runs(session: AsyncSession) -> int:
    """Run yang tidak pernah selesai (worker mati di tengah jalan) ditutup dengan catatan."""
    rows = list(await session.scalars(select(ScrapeRun).where(ScrapeRun.finished_at.is_(None))))
    for r in rows:
        r.finished_at = r.started_at
        r.error = "Terhenti: worker dimatikan/di-restart sebelum scraping selesai."
    await session.commit()
    return len(rows)


async def all_sources(session: AsyncSession) -> list[Source]:
    return list(await session.scalars(select(Source).order_by(Source.name)))


async def source_counts(session: AsyncSession, f: JobFilters | None = None) -> list[tuple[str, int, int]]:
    """(nama sumber, jumlah yang cocok filter lain, jumlah lowongan aktif) untuk pilihan filter Sumber.

    Angka pertama memakai semua filter yang sedang aktif kecuali filter Sumber itu sendiri, jadi
    sama dengan jumlah yang muncul di daftar bila sumber itu dicentang.
    """
    now = datetime.now(UTC)
    totals = dict((await session.execute(
        select(Source.name, func.count(func.distinct(JobListing.job_id)))
        .join(JobListing, JobListing.source_id == Source.id)
        .join(Job, Job.id == JobListing.job_id)
        .where(or_(Job.expires_at.is_(None), Job.expires_at >= now))
        .group_by(Source.name)
    )).all())
    matching = totals
    if f is not None:
        jobs = build_query(replace(f, sources=[], page=1)).subquery()
        matching = dict((await session.execute(
            select(Source.name, func.count(func.distinct(JobListing.job_id)))
            .join(JobListing, JobListing.source_id == Source.id)
            .join(jobs, jobs.c.id == JobListing.job_id)
            .group_by(Source.name)
        )).all())
    return [(name, matching.get(name, 0), total) for name, total in sorted(totals.items())]
