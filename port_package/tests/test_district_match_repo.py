# -*- coding: utf-8 -*-
"""Repository / 录入联动 / 缓存 测试（依赖目标项目 fixture）。

运行：
    python -m pytest tests/test_district_match_repo.py -q
"""
import gzip
import os
import time

import pytest

from models import district as dm
from services.district_match import pack as pack_mod

ROWS = [
    ("110000000000", "北京市", 1, "", "110000000000", ""),
    ("110100000000", "市辖区", 2, "110000000000", "110000000000", "110100000000"),
    ("110108000000", "海淀区", 3, "110100000000", "110000000000", "110100000000"),
    ("230000000000", "黑龙江省", 1, "", "230000000000", ""),
    ("230100000000", "哈尔滨市", 2, "230000000000", "230000000000", "230100000000"),
    ("230104000000", "道外区", 3, "230100000000", "230000000000", "230100000000"),
    ("230104001000", "水泥路街道", 4, "230104000000", "230000000000", "230100000000"),
    ("230104001001", "水泥路社区", 5, "230104001000", "230000000000", "230100000000"),
    ("231100000000", "黑河市", 2, "230000000000", "230000000000", "231100000000"),
    ("231183000000", "嫩江市", 3, "231100000000", "230000000000", "231100000000"),
]


def _write_pack(path):
    with open(path, "wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as gz:
            gz.write(("district_code\tdistrict_name\tadmin_level\tparent_code\t"
                      "province_code\tcity_code\n").encode("utf-8"))
            for row in ROWS:
                gz.write(("\t".join(str(x) for x in row) + "\n").encode("utf-8"))
    return path


@pytest.fixture(scope="module")
def loaded_dict(tmp_path_factory):
    """导入小数据包到临时 district.db，并在用例结束后清理缓存。"""
    tmp = tmp_path_factory.mktemp("dmpack")
    path = _write_pack(str(tmp / "district_pack.tsv.gz"))
    dm.import_dictionary(pack_file=path)
    yield path
    dm.invalidate_cache()


def test_pack_counts(loaded_dict):
    counts = pack_mod.count_pack(loaded_dict)
    assert counts["total"] == len(ROWS)
    assert counts["by_level"][5] == 1
    assert len(pack_mod.sha256_file(loaded_dict)) == 64


def test_repository_queries(loaded_dict):
    repo = dm.DistrictRepository()
    assert repo.get_by_district_code("230104000000", 3)["district_name"] == "道外区"
    assert repo.get_by_id("230100000000")["district_name"] == "哈尔滨市"
    assert repo.reverse_lookup("道外区", 3)["district_code"] == "230104000000"
    assert repo.reverse_lookup("水泥路社区", 5)["district_code"] == "230104001001"
    assert repo.search_by_name("道外区", 3)[0]["district_code"] == "230104000000"


def test_warm_cache_loaded(loaded_dict):
    info = dm.warm_cache()
    assert info["ready"] is True
    assert info["rows"] == 9          # L1–L4 共 9 行（ROWS 中 level<=4）
    assert info["history_rows"] >= 0


def test_fill_district_fields_inplace(loaded_dict):
    data = {"id_card": "230104199001010011",
            "address": "黑龙江省哈尔滨市道外区水泥路1号"}
    out = dm.fill_district_fields(data)
    assert out is data
    assert data["district_province_name"] == "黑龙江省"
    assert data["district_province_code"] == "230000000000"
    assert data["district_city_name"] == "哈尔滨市"
    assert data["district_county_name"] == "道外区"
    assert data["district_county_code"] == "230104000000"
    assert data["district_match_status"] in ("正确", "已解析")


def test_fill_never_raises_when_dict_missing(monkeypatch, tmp_path):
    """字典缺失：7 键留空且不抛异常（12.2 #29）。"""
    monkeypatch.setattr(dm, "DISTRICT_DB_PATH", str(tmp_path / "missing.db"))
    dm.invalidate_cache()
    data = {"id_card": "230104199001010011", "address": "黑龙江省哈尔滨市道外区"}
    out = dm.fill_district_fields(data)
    assert out is data
    for key in dm.DISTRICT_FIELDS:
        assert data[key] == ""
    dm.invalidate_cache()


def test_fill_fields_no_conn_writes(loaded_dict):
    """纯内存：调用前后 students 行数不变、连接无未提交写事务。"""
    conn = dm.get_db()
    before = conn.execute("SELECT COUNT(1) FROM students").fetchone()[0]
    dm.fill_district_fields({"id_card": "230104", "address": ""})
    after = conn.execute("SELECT COUNT(1) FROM students").fetchone()[0]
    assert before == after


def test_fill_many_perf(loaded_dict):
    """3000 行批量补全：一次预热 + 循环内存匹配，单行 < 20 ms（12.2 #30）。"""
    dm.warm_cache()
    rows = [{"id_card": "230104199001010011",
             "address": "黑龙江省哈尔滨市道外区水泥路1号"} for _ in range(3000)]
    started = time.perf_counter()
    dm.fill_district_fields_many(rows)
    elapsed = time.perf_counter() - started
    per_row_ms = elapsed * 1000 / len(rows)
    assert all(r["district_county_code"] == "230104000000" for r in rows)
    assert per_row_ms < 20, "单行 %.2f ms 超过 20 ms" % per_row_ms
    print("\n3000 行耗时 %.3fs，单行 %.3f ms" % (elapsed, per_row_ms))


def test_ensure_student_district_only_writes_result(loaded_dict, db_conn):
    from models.students import create_student

    student_id = create_student({
        "name": "区划测试生", "student_no": "DM0001", "id_card": "230104199001010011",
        "address": "黑龙江省哈尔滨市道外区水泥路1号", "gender": "男",
        "school": 1, "enrollment_year": 2026, "grade": "1", "admin_class": "1班",
        "registration_status": "正式学籍", "attendance_status": "在校",
    })
    before = db_conn.execute(
        "SELECT district_county_code, district_match_status FROM students WHERE id = ?",
        (student_id,)).fetchone()
    assert before[0] == "230104000000"

    # 人为清空 7 列，验证 ensure 不回写 students
    db_conn.execute(
        "UPDATE students SET district_province_name='', district_province_code='', "
        "district_city_name='', district_city_code='', district_county_name='', "
        "district_county_code='', district_match_status='' WHERE id = ?", (student_id,))
    db_conn.commit()

    data = dm.ensure_student_district(student_id, refresh=True)
    assert data["district_county_code"] == "230104000000"
    after = db_conn.execute(
        "SELECT district_county_code FROM students WHERE id = ?", (student_id,)).fetchone()
    assert after[0] == ""
    result = db_conn.execute(
        "SELECT COUNT(1) FROM dm_match_result WHERE student_id = ? AND source='on_demand'",
        (student_id,)).fetchone()
    assert result[0] == 1


def test_get_student_district_prefers_students(loaded_dict, db_conn):
    db_conn.execute(
        "INSERT INTO students (name, id_card, address, district_province_name, "
        "district_province_code, district_city_name, district_city_code, "
        "district_county_name, district_county_code, district_match_status, "
        "school, enrollment_year, registration_status, attendance_status) "
        "VALUES ('甲', '230104199001010022', '', '黑龙江省', '230000000000', "
        "'哈尔滨市', '230100000000', '道外区', '230104000000', '正确', 1, 2026, "
        "'正式学籍', '在校')")
    db_conn.commit()
    row = db_conn.execute("SELECT id FROM students WHERE id_card='230104199001010022'").fetchone()
    data = dm.get_student_district(student_id=row[0])
    assert data["source"] == "students"
    assert data["district_county_code"] == "230104000000"


def test_backfill_only_empty_keeps_values(loaded_dict, db_conn):
    db_conn.execute(
        "INSERT INTO students (name, id_card, address, district_county_code, "
        "school, enrollment_year, registration_status, attendance_status) "
        "VALUES ('乙', '230104199001010033', '黑龙江省哈尔滨市道外区', '', 1, 2026, "
        "'正式学籍', '在校')")
    db_conn.execute(
        "INSERT INTO students (name, id_card, address, district_county_code, "
        "school, enrollment_year, registration_status, attendance_status) "
        "VALUES ('丙', '110108199001010044', '', '110108000000', 1, 2026, "
        "'正式学籍', '在校')")
    db_conn.commit()
    before = db_conn.execute(
        "SELECT district_county_code, district_province_name FROM students "
        "WHERE name='丙'").fetchone()
    result = dm.backfill_students(only_empty=True, limit=100)
    assert result["updated"] >= 1
    after = db_conn.execute(
        "SELECT district_county_code, district_province_name FROM students "
        "WHERE name='丙'").fetchone()
    assert tuple(after) == tuple(before)
    filled = db_conn.execute(
        "SELECT district_county_code FROM students WHERE name='乙'").fetchone()
    assert filled[0] == "230104000000"
