"""Lokasi lowongan → kode wilayah resmi Kemendagri (provinsi "31" atau kabupaten/kota "31.74").

Data: app/data/wilayah.json, dibuat dari github.com/cahyadsn/wilayah (Kepmendagri No 300.2.2-2430 Tahun 2025):
38 provinsi, 514 kabupaten/kota, dan nama kecamatan → kabupaten/kota.

Situs lowongan menulis lokasi dengan banyak cara ("Jakarta Selatan", "Kab. Tangerang", "South Tangerang",
"Kebayoran Lama", "Cikarang Pusat", "Jakarta Raya"). Urutan pencocokan:
  1. nama kabupaten/kota (awalan Kab./Kabupaten/Kota/Regency/City menentukan pilihan bila namanya kembar,
     mis. Kota Bekasi vs Kabupaten Bekasi; tanpa awalan → kota),
  2. nama kecamatan (mis. Kebayoran Lama → Jakarta Selatan), dibantu provinsi bila namanya ada di beberapa tempat,
  3. provinsi saja (mis. "Jakarta Raya", "Bali", "Jawa Tengah").
"""

import json
import re
from functools import lru_cache
from pathlib import Path

DATA_FILE = Path(__file__).resolve().parent.parent / "data" / "wilayah.json"

# Jabodetabek: seluruh DKI Jakarta + Bogor, Depok, Tangerang, Tangerang Selatan, Bekasi (kota & kabupaten).
JABODETABEK_PROVINCE = "31"
JABODETABEK_REGENCIES = ("32.01", "32.71", "32.76", "32.16", "32.75", "36.03", "36.71", "36.74")

_DIRECTIONS = {"south": "selatan", "north": "utara", "west": "barat", "east": "timur", "central": "pusat"}
_PROVINCE_ALIASES = {
    "jakarta": "31", "jakarta raya": "31", "dki jakarta": "31", "dki": "31", "greater jakarta": "31",
    "daerah khusus jakarta": "31",
    "yogyakarta": "34", "di yogyakarta": "34", "diy": "34", "jogja": "34", "jogjakarta": "34",
    "bangka belitung": "19", "babel": "19", "kepri": "21", "ntb": "52", "ntt": "53",
}
_CITY_ALIASES = {
    "bsd": "36.74", "bsd city": "36.74", "bintaro": "36.74", "alam sutera": "36.74", "gading serpong": "36.03",
    "lippo karawaci": "36.71", "cikarang": "32.16", "jababeka": "32.16", "karawang": "32.15", "jogja": "34.71",
    "solo": "33.72", "sentul": "32.01", "scbd": "31.74", "kuningan jakarta": "31.74",
}


def _norm(text: str | None) -> str:
    t = re.sub(r"[^a-z0-9 ]+", " ", (text or "").lower().replace("kab.", "kabupaten "))
    words = t.split()
    # "South Tangerang" → "tangerang selatan"
    if len(words) >= 2 and words[0] in _DIRECTIONS:
        words = words[1:] + [_DIRECTIONS[words[0]]]
    return " ".join(words)


_KIND = re.compile(r"^(kabupaten administrasi|kabupaten|kab|kota administrasi|kota adm|kota)\s+|\s+(regency|district|city|kota)$")


def _split_kind(name: str) -> tuple[str, str | None]:
    """'kabupaten bekasi' → ('bekasi', 'kab'); 'tangerang regency' → ('tangerang', 'kab')."""
    kind = None
    while (m := _KIND.search(name)):
        word = (m.group(1) or m.group(2)).split()[0]
        kind = kind or ("kab" if word in ("kabupaten", "kab", "regency", "district") else "kota")
        name = (name[:m.start()] + " " + name[m.end():]).strip()
    return name, kind


@lru_cache
def data() -> dict:
    raw = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    regencies: dict[str, list[tuple[str, str]]] = {}
    for code, name in raw["regencies"].items():
        core, kind = _split_kind(_norm(name))
        regencies.setdefault(core, []).append((code, kind or "kab"))
        if " " in core:  # "toli toli" juga dicari sebagai "tolitoli"
            regencies.setdefault(core.replace(" ", ""), []).append((code, kind or "kab"))
    provinces = {_norm(n): c for c, n in raw["provinces"].items()}
    for c, n in raw["provinces"].items():
        short = re.sub(r"^(daerah khusus ibukota|daerah istimewa)\s+", "", _norm(n))
        provinces.setdefault(short, c)
    provinces.update(_PROVINCE_ALIASES)
    districts = {_norm(n): codes for n, codes in raw["districts"].items()}
    return {"raw": raw, "regencies": regencies, "provinces": provinces, "districts": districts}


def _province_code(text: str | None) -> str | None:
    return data()["provinces"].get(_norm(text)) if text else None


def resolve(city: str | None, province: str | None = None) -> str | None:
    """Kode wilayah paling spesifik yang bisa dipastikan, atau None (mis. 'Indonesia')."""
    d = data()
    prov = _province_code(province)
    name = _norm(city)
    if name:
        if name in _CITY_ALIASES:
            return _CITY_ALIASES[name]
        core, kind = _split_kind(name)
        candidates = list(d["regencies"].get(core) or d["regencies"].get(core.replace(" ", "")) or [])
        if prov:
            candidates = [c for c in candidates if c[0].startswith(prov + ".")] or candidates
        if candidates:
            if kind:
                exact = [c for c in candidates if c[1] == kind]
                candidates = exact or candidates
            # Nama tanpa awalan ("Bekasi", "Tangerang") → kotanya; situs lowongan umumnya memaksudkan kota.
            candidates.sort(key=lambda c: c[1] != "kota")
            return candidates[0][0]
        codes = d["districts"].get(name) or d["districts"].get(core) or []
        if prov:
            codes = [c for c in codes if c.startswith(prov + ".")]
        if len(codes) == 1:
            return codes[0]
        if codes and len({c[:2] for c in codes}) == 1:
            return codes[0][:2]
        if (p := _province_code(city)):
            return p
    return prov


def label(code: str) -> str:
    raw = data()["raw"]
    if "." in code:
        return display_regency(raw["regencies"].get(code, code))
    return display_province(raw["provinces"].get(code, code))


def display_province(name: str) -> str:
    return re.sub(r"^Daerah Istimewa\s+", "DI ", re.sub(r"^Daerah Khusus Ibukota\s+", "DKI ", name))


def display_regency(name: str) -> str:
    """'Kota Administrasi Jakarta Barat' → 'Jakarta Barat'; 'Kabupaten Bekasi' → 'Kab. Bekasi'."""
    name = re.sub(r"^Kota Administrasi\s+", "", name)
    name = re.sub(r"^Kabupaten Administrasi\s+", "Kab. ", name)
    return re.sub(r"^Kabupaten\s+", "Kab. ", name)


def grouped_options(counts: dict[str, int] | None = None) -> list[dict]:
    """Untuk dropdown: provinsi (urut nama) → kab/kota (kota dulu, lalu kabupaten)."""
    raw = data()["raw"]
    counts = counts or {}
    groups = []
    for pcode, pname in sorted(((c, display_province(n)) for c, n in raw["provinces"].items()), key=lambda x: x[1]):
        items = [(c, n) for c, n in raw["regencies"].items() if c.startswith(pcode + ".")]
        items.sort(key=lambda x: (not x[1].startswith("Kota"), display_regency(x[1]).replace("Kab. ", "")))
        total = sum(v for k, v in counts.items() if k == pcode or k.startswith(pcode + "."))
        groups.append({
            "code": pcode, "name": pname, "count": total,
            "items": [{"code": c, "name": display_regency(n), "count": counts.get(c, 0)} for c, n in items],
        })
    return groups
