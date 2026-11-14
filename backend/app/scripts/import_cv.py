"""Daftarkan file CV (PDF) yang sudah ada di folder storage.

    python -m app.scripts.import_cv "cv/CV - Nama.pdf" "CV Master"

Path relatif terhadap STORAGE_DIR. CV pertama otomatis jadi default.
"""

import asyncio
import sys

from sqlalchemy import select

from app.core.db import SessionLocal
from app.models import CvDocument
from app.services.profile import cv_path, extract_pdf_text, get_profile


async def main(rel_path: str, title: str) -> None:
    async with SessionLocal() as session:
        profile = await get_profile(session)
        if profile is None:
            sys.exit("Isi profil dulu (tabel user_profile kosong).")
        cv = await session.scalar(select(CvDocument).where(CvDocument.file_path == rel_path))
        if cv is None:
            has_default = await session.scalar(select(CvDocument.id).where(CvDocument.is_default))
            cv = CvDocument(user_id=profile.id, title=title, file_path=rel_path, is_default=not has_default)
            session.add(cv)
        path = cv_path(cv)
        if not path.is_file():
            sys.exit(f"File tidak ditemukan: {path}")
        cv.title = title
        cv.parsed_text = extract_pdf_text(path)
        await session.commit()
        print(f"CV '{title}' tersimpan (id={cv.id}, {len(cv.parsed_text)} karakter teks, default={cv.is_default})")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    asyncio.run(main(sys.argv[1], sys.argv[2]))
