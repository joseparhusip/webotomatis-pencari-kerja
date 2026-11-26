"""Skor CV: baca Word/PDF, penilaian oleh aturan, perbandingan pasar, dan penyaringan jawaban AI. Data rekaan."""

import io
import zipfile

import pytest

from app.services import cv_score
from app.services.cv_scan import CvScan
from app.services.docx_text import docx_text

W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
MC = 'xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006"'
R = 'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'


def _p(text: str) -> str:
    return f"<w:p><w:r><w:t xml:space=\"preserve\">{text}</w:t></w:r></w:p>"


def make_docx(body: str, header: str = "", rels: str = "") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("word/document.xml", f"<w:document {W} {MC} {R}><w:body>{body}</w:body></w:document>")
        if header:
            z.writestr("word/header1.xml", f"<w:hdr {W}>{header}</w:hdr>")
        z.writestr("word/_rels/document.xml.rels",
                   '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   f"{rels}</Relationships>")
    return buf.getvalue()


def test_docx_header_tabel_textbox_dan_tautan():
    textbox = ("<w:p><w:r><mc:AlternateContent><mc:Choice><w:drawing><w:txbxContent>" + _p("Skill: Excel, Canva")
               + "</w:txbxContent></w:drawing></mc:Choice><mc:Fallback><w:pict><w:txbxContent>" + _p("Skill: Excel, Canva")
               + "</w:txbxContent></w:pict></mc:Fallback></mc:AlternateContent></w:r></w:p>")
    table = ("<w:tbl><w:tr><w:tc>" + _p("Toko Contoh - Bandung") + "</w:tc><w:tc>" + _p("Jan 2023 - Des 2023")
             + "</w:tc></w:tr></w:tbl>")
    data = make_docx(_p("Pengalaman") + table + textbox, header=_p("Kandidat Contoh") + _p("kandidat@example.com"),
                     rels='<Relationship Id="r1" Type="hyperlink" Target="https://linkedin.com/in/kandidat-contoh" '
                          'TargetMode="External"/>')
    text, links = docx_text(data)
    lines = text.splitlines()
    assert lines[0] == "Kandidat Contoh"                         # header (nama & kontak) di urutan pertama
    assert "Toko Contoh - Bandung   Jan 2023 - Des 2023" in lines  # satu baris tabel = satu baris CV
    assert text.count("Skill: Excel, Canva") == 1               # text box tidak dobel (Fallback dilewati)
    assert links == ["https://linkedin.com/in/kandidat-contoh"]


def test_jenis_file_dan_batas_ukuran():
    assert cv_score.check_upload(b"%PDF-1.7 ...", "cv.pdf") == "pdf"
    assert cv_score.check_upload(make_docx(_p("x")), "cv.docx") == "docx"
    with pytest.raises(cv_score.CvScoreError, match=".doc"):
        cv_score.check_upload(b"\xd0\xcf\x11\xe0" + b"0" * 100, "cv.doc")
    with pytest.raises(cv_score.CvScoreError, match="PDF atau Word"):
        cv_score.check_upload(b"GIF89a....", "cv.gif")
    with pytest.raises(cv_score.CvScoreError, match="Maksimal 5 MB"):
        cv_score.check_upload(b"%PDF" + b"0" * cv_score.MAX_BYTES, "besar.pdf")


GOOD = CvScan(
    fields={"full_name": "Kandidat Contoh", "email": "kandidat@example.com", "phone": "0812 3456 7890",
            "linkedin_url": "linkedin.com/in/kandidat-contoh",
            "summary": ("Staf administrasi dengan pengalaman mengelola data penjualan dan laporan harian menggunakan Excel "
                        "dan SAP. Terbiasa menyusun laporan stok, melayani pelanggan, dan bekerja dengan target bulanan."),
            "skills": ["Excel", "SAP", "Canva", "Power BI", "CRM", "SQL", "Google Sheets", "Komunikasi"]},
    text=("Kandidat Contoh\nPengalaman\nAdmin Penjualan, PT Contoh Jan 2023 - Dec 2024\n" + "Kata isi. " * 300
          + "\nPendidikan\nUniversitas Contoh\nSkill\nExcel, SAP"),
    experiences=[{"company": "PT Contoh", "role": "Admin Penjualan", "start": "Jan 2023", "end": "Dec 2024",
                  "highlights": ["Menyusun 30 laporan penjualan per bulan dengan Excel dan Power BI.",
                                 "Mengelola data 200 pelanggan di CRM perusahaan.",
                                 "Membuat dashboard stok harian dengan SQL dan Google Sheets."]}],
    education=[{"institution": "Universitas Contoh", "degree": "S1", "major": "Manajemen", "start": "2018", "end": "2022"}],
)


def test_skor_deterministik_dan_pasar_tidak_dinilai_tanpa_data():
    a = cv_score.build_report(GOOD, "pdf", None)
    b = cv_score.build_report(GOOD, "pdf", None)
    assert a["score"] == b["score"]                              # CV sama → skor sama
    assert a["assessed_max"] == 85                               # 15 poin pasar tidak dinilai, bukan dianggap 0
    crit = {c["key"]: c for c in a["criteria"]}
    assert crit["market_relevance"]["status"] == "na"
    assert crit["contact_email"]["status"] == "ok" and crit["exp_metrics"]["status"] == "ok"
    assert len(a["criteria"]) == 23
    assert a["score"] >= 80


def test_field_kosong_dinilai_kosong_bukan_dikarang():
    thin = CvScan(fields={"full_name": "Kandidat Tipis", "skills": ["Komunikasi", "Kerja sama"]},
                  text="Kandidat Tipis\nSaya pekerja keras.")
    r = cv_score.build_report(thin, "docx", None)
    crit = {c["key"]: c for c in r["criteria"]}
    for key in ("contact_email", "contact_phone", "summary_present", "exp_present", "edu_present"):
        assert crit[key]["status"] == "miss", key
    assert crit["skills_hard"]["status"] == "miss"               # soft skill bukan skill teknis
    assert r["score"] < 30


def test_ocr_menurunkan_keterbacaan_ats():
    scan = CvScan(**{**GOOD.__dict__, "method": "ocr"})
    crit = {c["key"]: c for c in cv_score.build_report(scan, "pdf", None)["criteria"]}
    assert crit["ats_text"]["status"] == "miss"


def test_pasar_dihitung_dari_lowongan():
    rows = [("Admin Penjualan", "Menguasai Excel dan SAP", None, ["Microsoft Excel"]),
            ("Staff Admin", "Wajib Excel, paham Power BI dan accurate", None, []),  # hanya di deskripsi
            ("Admin Gudang", "Menguasai SAP dan Accurate", None, ["Accurate"]),
            ("Admin", "Bisa Accurate", None, ["Accurate"])]
    out = cv_score._market_counts(rows, ["Excel", "SAP"])
    assert out["jobs"] == 4 and out["jobs_with_cv_skill"] == 3
    top = {t["name"]: t for t in out["top"]}
    assert top["Excel"]["owned"] and top["Excel"]["count"] == 2
    assert top["Accurate"]["count"] == 3 and not top["Accurate"]["owned"]


def test_jawaban_ai_disaring():
    report = cv_score.build_report(GOOD, "pdf", None)
    status = {c["key"]: c["status"] for c in report["criteria"]}
    weak = next(k for k, v in status.items() if v in ("partial", "miss"))
    data = {
        "verdict": "CV ini mendapat 97 poin dan siap dipakai.",               # angka karangan → diganti
        "decision": "siap" if report["score"] < 80 else "perbaiki_dulu",       # di luar pilihan yang sah
        "strengths": [{"criterion": "contact_email", "text": "Kontak lengkap."},
                      {"criterion": weak, "text": "Ini bukan kekuatan."},     # kriteria lemah → dibuang
                      {"criterion": "tidak_ada", "text": "Kriteria rekaan."}],
        "priorities": [{"criterion": weak, "text": "Perbaiki bagian ini.",
                        "before": "Kalimat yang tidak ada di CV sama sekali", "after": "Versi baru"},
                       {"criterion": "contact_email", "text": "Sudah ok, bukan prioritas."}],
    }
    out = cv_score.check_ai_review(data, report, GOOD.text)
    assert "97" not in out["verdict"]
    assert out["decision"] in cv_score.allowed_decisions(report["score"])
    assert [s["criterion"] for s in out["strengths"]] == ["contact_email"]
    assert out["priorities"][0]["criterion"] == weak
    assert out["priorities"][0]["before"] == "" and out["priorities"][0]["after"] == ""
    assert all(p["criterion"] != "contact_email" for p in out["priorities"])


def test_keputusan_mengikuti_skor():
    assert cv_score.allowed_decisions(90) == ["siap", "perbaiki_sedikit"]
    assert cv_score.allowed_decisions(60) == ["perbaiki_sedikit", "perbaiki_dulu"]
    assert cv_score.allowed_decisions(40) == ["perbaiki_dulu"]


def test_contoh_perbaikan_tidak_boleh_menambah_fakta():
    """Kasus nyata dari Gemini: tautan karangan, skill baru, dan aktivitas baru di contoh 'sesudah' dibuang."""
    text = ("Rina Contoh\nrina@example.com | 0812 0000 1111\nPengalaman\nSocial Media Admin, Toko Contoh Jan 2023 - Dec 2023\n"
            "• Menjalankan iklan Meta Ads untuk promo musiman.\n• Membantu riset pasar untuk produk baru.\n"
            "Skill\nCanva, CapCut, Meta Ads, Excel")
    report = {"score": 70, "grade": "Baik", "groups": [], "market": {"top": [{"name": "TikTok", "count": 9, "owned": False}]},
              "criteria": [{"key": k, "label": k, "status": "partial", "points": 1, "max": 3, "detail": "", "fix": "x"}
                           for k in ("exp_metrics", "contact_link", "market_coverage", "exp_bullets", "skills_proven")]}
    data = {"verdict": "Cukup baik.", "decision": "perbaiki_sedikit", "strengths": [], "priorities": [
        {"criterion": "exp_metrics", "text": "Tambahkan angka.", "before": "• Menjalankan iklan Meta Ads untuk promo musiman.",
         "after": "• Menjalankan iklan Meta Ads untuk promo musiman yang meningkatkan konversi sebesar [%]."},
        {"criterion": "contact_link", "text": "Tambahkan LinkedIn.", "before": "rina@example.com | 0812 0000 1111",
         "after": "rina@example.com | 0812 0000 1111 | linkedin.com/in/rinakandidat"},
        {"criterion": "skills_proven", "text": "Sebut skill.", "before": "• Membantu riset pasar untuk produk baru.",
         "after": "• Membantu riset pasar untuk produk baru dengan TikTok Analytics."},
        {"criterion": "exp_bullets", "text": "Perjelas.", "before": "• Membantu riset pasar untuk produk baru.",
         "after": "• Membantu riset pasar untuk [jumlah] produk baru guna menganalisis tren konsumen dan kompetitor."},
    ]}
    out = {p["criterion"]: p for p in cv_score.check_ai_review(data, report, text)["priorities"]}
    assert out["exp_metrics"]["after"].endswith("sebesar [%].")      # dirapikan + penanda → diterima
    assert out["contact_link"]["after"] == ""                        # tautan karangan + bukan kriteria tulisan
    assert out["skills_proven"]["after"] == ""                       # skill yang tidak ada di CV
    assert out["exp_bullets"]["after"] == ""                         # menambah aktivitas baru
    assert out["contact_link"]["text"] == "Tambahkan LinkedIn."      # sarannya tetap ada, hanya contohnya dibuang


def test_pilihan_bidang_dibersihkan():
    assert cv_score.clean_fields(["marketing", "marketing", "other", "pilot", "backend"]) == ["marketing", "backend"]
    assert cv_score.clean_fields(None) == []


def _market(mode, cats, detected, jobs=100, hit=20):
    return {"categories": cats, "label": " / ".join(cats), "mode": mode, "detected": detected,
            "detected_label": " / ".join(detected), "jobs": jobs, "jobs_with_cv_skill": hit,
            "top": [{"name": "Excel", "count": 40, "pct": 40, "owned": True},
                    {"name": "Accurate", "count": 30, "pct": 30, "owned": False}], "cv_skill_demand": []}


def test_penjelasan_skor_cocok_dengan_hitungan():
    report = cv_score.build_report(GOOD, "pdf", _market("auto", ["admin"], ["admin"], hit=70))
    ex = cv_score.explain(report)
    rated = [c for c in report["criteria"] if c["status"] != "na"]
    lost = sum(c["max"] - c["points"] for c in rated)
    assert abs(sum(x["lost"] for x in ex["losses"]) - lost) < 0.3       # rincian = total poin yang hilang
    assert ex["losses"] == sorted(ex["losses"], key=lambda x: -x["lost"])  # yang paling berdampak di atas
    assert f"= {report['score']}." in ex["math"]
    assert ex["potential"] >= report["score"]
    assert ex["story"][0].startswith(f"Skormu {report['score']} dari 100")
    assert "100 lowongan aktif bidang admin" in ex["market"] and "Accurate (30 lowongan)" in ex["market"]
    assert all(x["why"] for x in ex["losses"])                            # setiap kehilangan punya alasan


def test_bidang_pilihan_berbeda_dengan_isi_cv_diberi_peringatan():
    ex = cv_score.explain(cv_score.build_report(GOOD, "pdf", _market("manual", ["backend"], ["admin"], hit=3)))
    assert ex["field_tone"] == "warn" and "berbeda dengan yang terlihat dari isi CV" in ex["field_note"]
    ex = cv_score.explain(cv_score.build_report(GOOD, "pdf", _market("manual", ["admin"], ["admin"], hit=70)))
    assert ex["field_tone"] == "" and "sesuai dengan isi CV" in ex["field_note"]


def test_penjelasan_bila_pasar_tidak_dinilai():
    ex = cv_score.explain(cv_score.build_report(GOOD, "pdf", _market("manual", ["hr"], ["admin"], jobs=5, hit=1)))
    assert "tidak dinilai" in ex["math"] and "baru 5" in ex["market"]


def test_skor_dibatasi_bila_cv_tidak_nyambung_dengan_bidang():
    fit = cv_score.build_report(GOOD, "pdf", _market("manual", ["admin"], ["admin"], hit=70))
    off = cv_score.build_report(GOOD, "pdf", _market("manual", ["backend"], ["admin"], hit=3))     # 3%
    mid = cv_score.build_report(GOOD, "pdf", _market("manual", ["sales"], ["admin"], hit=20))      # 20%
    assert fit["cap"] is None and fit["score"] > 69
    assert off["score"] == 54 and off["raw_score"] > 54 and off["grade"] == "Perlu perbaikan"
    assert mid["score"] <= 69
    ex = cv_score.explain(off)
    assert f"= {off['raw_score']}." in ex["math"] and "dibatasi menjadi 54" in ex["math"]
    assert ex["potential"] <= 54
