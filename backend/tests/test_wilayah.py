from app.services.jobs import JobFilters, build_query
from app.services.wilayah import data, grouped_options, label, resolve


def test_data_lengkap_kepmendagri_2025():
    raw = data()["raw"]
    assert len(raw["provinces"]) == 38
    assert len(raw["regencies"]) == 514


def test_resolve_variasi_penulisan():
    cases = {
        ("Jakarta Barat", "DKI Jakarta"): "31.73",
        ("Jakarta Selatan", "Jakarta Raya"): "31.74",
        ("Kebayoran Lama", "Jakarta Raya"): "31.74",   # kecamatan
        ("Kemayoran", None): "31.71",
        ("Bekasi", "Jawa Barat"): "32.75",             # tanpa awalan → kota
        ("Kab. Bekasi", "Jawa Barat"): "32.16",
        ("Cikarang Pusat", "Jawa Barat"): "32.16",
        ("Kabupaten Bandung", "Jawa Barat"): "32.04",
        ("Bandung", "Jawa Barat"): "32.73",
        ("South Tangerang", "Banten"): "36.74",
        ("Tangerang Regency", "Banten"): "36.03",
        ("BSD City", None): "36.74",
        ("Batam Kota", "Kepulauan Riau"): "21.71",
        ("Jakarta Raya", None): "31",                  # hanya provinsi
        ("Bali", None): "51",
        ("DI Yogyakarta", None): "34",
    }
    for (city, prov), code in cases.items():
        assert resolve(city, prov) == code, (city, prov)
    assert resolve("Indonesia", None) is None
    assert resolve(None, None) is None


def test_label_dan_dropdown():
    assert label("31.73") == "Jakarta Barat"
    assert label("32.75") == "Kota Bekasi"
    assert label("32.16") == "Kab. Bekasi"
    assert label("31") == "DKI Jakarta"
    groups = grouped_options({"31.73": 5, "31": 2})
    jkt = next(g for g in groups if g["code"] == "31")
    assert jkt["count"] == 7
    assert [i["name"] for i in jkt["items"]][:1] == ["Jakarta Barat"]
    assert sum(len(g["items"]) for g in groups) == 514


def test_filter_wilayah_sql():
    sql = str(build_query(JobFilters(loc="wil:31.73")).compile(compile_kwargs={"literal_binds": True}))
    assert "region_code = '31.73'" in sql
    sql = str(build_query(JobFilters(loc="wil:31")).compile(compile_kwargs={"literal_binds": True}))
    assert "LIKE '31.%'" in sql
    # nilai aneh diabaikan (tidak jadi filter)
    sql = str(build_query(JobFilters(loc="wil:abc")).compile(compile_kwargs={"literal_binds": True}))
    assert "region_code" not in sql.split("WHERE", 1)[1]
