"""OCR lokal (Tesseract) untuk CV berupa gambar / PDF hasil scan — tanpa AI, tanpa kirim file ke luar."""

import logging

log = logging.getLogger(__name__)

MAX_PAGES = 6
DPI = 300


def ocr_pdf(pdf: bytes, max_pages: int = MAX_PAGES) -> str:
    """Teks dari setiap halaman PDF (bahasa Indonesia + Inggris). "" bila OCR tidak tersedia/gagal."""
    try:
        import pypdfium2 as pdfium
        import pytesseract
    except ImportError:
        log.warning("OCR tidak tersedia (pypdfium2/pytesseract belum terpasang)")
        return ""
    try:
        doc = pdfium.PdfDocument(pdf)
        pages = []
        for i in range(min(len(doc), max_pages)):
            image = doc[i].render(scale=DPI / 72).to_pil()
            pages.append(pytesseract.image_to_string(image, lang="ind+eng"))
        return "\n".join(pages).strip()
    except Exception as exc:  # PDF rusak / Tesseract tidak terpasang
        log.warning("OCR gagal: %s", exc)
        return ""
