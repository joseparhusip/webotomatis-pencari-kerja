"""Teks dari file Word (.docx) tanpa AI dan tanpa library tambahan (.docx = zip berisi XML).

Urutan teks mengikuti dokumen: header (biasanya nama & kontak), isi (paragraf, tabel, text box), footer.
Bila isinya hampir tidak ada teks (CV berupa gambar yang ditempel di Word), gambar di dalamnya dibaca
dengan OCR lokal (Tesseract) — sama seperti PDF hasil scan.
"""

import io
import logging
import re
import zipfile
import xml.etree.ElementTree as ET

log = logging.getLogger(__name__)

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_MC_FALLBACK = "{http://schemas.openxmlformats.org/markup-compatibility/2006}Fallback"
MAX_XML_BYTES = 20 * 1024 * 1024      # isi XML setelah diekstrak (lindungi dari zip bomb)
MAX_OCR_IMAGES = 6
_IMAGE = re.compile(r"^word/media/[^/]+\.(png|jpe?g|bmp|tiff?|gif)$", re.I)


class DocxError(Exception):
    pass


def is_docx(data: bytes) -> bool:
    if not data.startswith(b"PK\x03\x04"):
        return False
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            return "word/document.xml" in z.namelist()
    except zipfile.BadZipFile:
        return False


def _xml(z: zipfile.ZipFile, name: str) -> ET.Element | None:
    try:
        info = z.getinfo(name)
    except KeyError:
        return None
    if info.file_size > MAX_XML_BYTES:
        raise DocxError("Isi file Word terlalu besar untuk dibaca.")
    return ET.fromstring(z.read(name))


def _paragraph(p: ET.Element, nested: list[str]) -> str:
    parts: list[str] = []

    def walk(el: ET.Element) -> None:
        for child in el:
            tag = child.tag
            if tag == _MC_FALLBACK:          # salinan lama (VML) dari text box yang sama → jangan dobel
                continue
            if tag == f"{_W}t":
                parts.append(child.text or "")
            elif tag == f"{_W}tab":
                parts.append("\t")
            elif tag in (f"{_W}br", f"{_W}cr"):
                parts.append("\n")
            elif tag == f"{_W}txbxContent":  # text box di dalam paragraf → baris tersendiri
                _block(child, nested)
            else:
                walk(child)

    walk(p)
    return "".join(parts)


def _table(tbl: ET.Element, out: list[str]) -> None:
    for tr in tbl.iter(f"{_W}tr"):
        cells: list[list[str]] = []
        for tc in tr.findall(f"{_W}tc"):
            lines: list[str] = []
            _block(tc, lines)
            cells.append([ln for ln in lines if ln.strip()])
        filled = [c for c in cells if c]
        # Baris tabel satu-baris per sel ("PT Contoh | Jan 2024 – Sekarang") digabung seperti satu baris CV biasa.
        if filled and all(len(c) == 1 for c in filled):
            out.append("   ".join(c[0] for c in filled))
        else:
            for c in filled:
                out.extend(c)


def _block(el: ET.Element, out: list[str]) -> None:
    for child in el:
        tag = child.tag
        if tag == _MC_FALLBACK:
            continue
        if tag == f"{_W}p":
            nested: list[str] = []
            text = _paragraph(child, nested)
            out.extend(text.split("\n"))
            out.extend(nested)
        elif tag == f"{_W}tbl":
            _table(child, out)
        else:
            _block(child, out)


def _part_text(z: zipfile.ZipFile, name: str) -> list[str]:
    root = _xml(z, name)
    if root is None:
        return []
    lines: list[str] = []
    _block(root, lines)
    return lines


def _links(z: zipfile.ZipFile) -> list[str]:
    root = _xml(z, "word/_rels/document.xml.rels")
    if root is None:
        return []
    out: list[str] = []
    for rel in root:
        target = rel.get("Target") or ""
        if rel.get("TargetMode") == "External" and re.match(r"^(https?://|mailto:)", target, re.I) and target not in out:
            out.append(target)
    return out


def _ocr_images(z: zipfile.ZipFile) -> str:
    try:
        import pytesseract
        from PIL import Image
    except ImportError:
        return ""
    texts = []
    names = sorted(n for n in z.namelist() if _IMAGE.match(n))
    for name in names[:MAX_OCR_IMAGES]:
        if z.getinfo(name).file_size > MAX_XML_BYTES:
            continue
        try:
            with Image.open(io.BytesIO(z.read(name))) as img:
                img.load()
                if img.width < 300 or img.height < 300:   # ikon / logo kecil, bukan halaman CV
                    continue
                texts.append(pytesseract.image_to_string(img.convert("RGB"), lang="ind+eng"))
        except Exception as exc:
            log.warning("OCR gambar %s di Word gagal: %s", name, exc)
    return "\n".join(t.strip() for t in texts if t.strip())


def docx_text(data: bytes) -> tuple[str, list[str]]:
    """(teks, hyperlink). Melempar DocxError bila file bukan .docx yang valid."""
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise DocxError("File Word rusak atau bukan .docx.") from exc
    with z:
        names = z.namelist()
        if "word/document.xml" not in names:
            raise DocxError("File ini bukan dokumen Word (.docx).")
        try:
            headers = [n for n in sorted(names) if re.match(r"^word/header\d*\.xml$", n)]
            footers = [n for n in sorted(names) if re.match(r"^word/footer\d*\.xml$", n)]
            lines: list[str] = []
            for n in headers:
                lines += _part_text(z, n)
            lines += _part_text(z, "word/document.xml")
            for n in footers:
                lines += _part_text(z, n)
            links = _links(z)
        except ET.ParseError as exc:
            raise DocxError("Isi file Word rusak.") from exc
        # Header yang sama di beberapa section tidak perlu diulang.
        seen: set[str] = set()
        clean: list[str] = []
        for ln in lines:
            ln = ln.replace(" ", " ").rstrip()
            key = ln.strip()
            if key and key in seen and len(key) > 3 and ln in clean[:20]:
                continue
            seen.add(key)
            clean.append(ln)
        text = re.sub(r"\n{3,}", "\n\n", "\n".join(clean)).strip()
        return text, links


def docx_ocr(data: bytes) -> str:
    """OCR gambar di dalam .docx (CV berupa gambar yang ditempel di Word)."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            return _ocr_images(z)
    except zipfile.BadZipFile:
        return ""
