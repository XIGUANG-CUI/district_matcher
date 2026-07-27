"""收尾验证：P10 t_result 去 JOIN 自包含、P11 删冗余 detail、P12 detail 惰性、
P13 config 缓存、P14 默认 Excel TTL 缓存。

运行：python tests/verify_final.py
"""
import os
import sys
import tempfile
import shutil
import sqlite3

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import config
tmp = tempfile.mkdtemp()
# 防止 import app 时 DBManager() 打开真实库（指向临时）
config.DB_PATH = os.path.join(tmp, "dm_final.db")

from database.db_manager import DBManager
from services import match_service, export_service
import app as app_module  # P13/P14 目标；此时 app.db 指向临时库


def _seed_districts(db):
    db.insert_districts([
        {"id": "p11", "pids": "", "pid": None, "district_code": "110000000000",
         "district_name": "北京市", "admin_level": 1,
         "province_code": "110000000000", "city_code": None},
        {"id": "c1101", "pids": "110000000000", "pid": "110000000000",
         "district_code": "110100000000", "district_name": "市辖区",
         "admin_level": 2, "province_code": "110000000000", "city_code": None},
        {"id": "d110108", "pids": "110000000000,110100000000", "pid": "110100000000",
         "district_code": "110108000000", "district_name": "海淀区",
         "admin_level": 3, "province_code": "110000000000", "city_code": "110100000000"},
        {"id": "p23", "pids": "", "pid": None, "district_code": "230000000000",
         "district_name": "黑龙江省", "admin_level": 1,
         "province_code": "230000000000", "city_code": None},
        {"id": "c2301", "pids": "230000000000", "pid": "230000000000",
         "district_code": "230100000000", "district_name": "哈尔滨市",
         "admin_level": 2, "province_code": "230000000000", "city_code": None},
        {"id": "d230102", "pids": "230000000000,230100000000", "pid": "230100000000",
         "district_code": "230102000000", "district_name": "道外区",
         "admin_level": 3, "province_code": "230000000000", "city_code": "230100000000"},
    ])


def main():
    db = DBManager(os.path.join(tmp, "main.db"))

    # ---- P10: t_result 自包含 batch_id，无 JOIN 查询 ----
    _seed_districts(db)
    db.insert_students([
        {"batch_id": "B1", "student_name": "张三",
         "id_card": "23010219900101001X", "address": "哈尔滨市道外区南马路1号"},
        {"batch_id": "B1", "student_name": "李四",
         "id_card": "110108200003154578", "address": "北京市海淀区中关村大街1号"},
    ])
    stats = match_service.process_batch(db, "B1")
    res = db.query_results(batch_id="B1")
    assert len(res) == 2, "P10 query_results 应返回2条"
    assert all(r["batch_id"] == "B1" for r in res), "P10 结果应带 batch_id"
    assert all(r["id_card"] and r["student_name"] for r in res), "P10 冗余三列应自包含"
    print(f"  [P10] batch_id 自包含查询 OK，stats={stats}")

    # 导出即清理（一进一出）仍正确
    p = export_service.export_results(db, batch_id="B1", fmt="xlsx", cleanup=True)
    assert os.path.exists(p), "P10 导出文件未生成"
    assert db.count_results(batch_id="B1") == 0, "P10 导出后结果应清空"
    assert len(db.get_students_by_batch("B1")) == 0, "P10 导出后学生应清空"
    print("  [P10] 导出即清理 OK（无 JOIN 的 t_result 仍正确清空）")

    # ---- P10 旧库迁移：student_id 旧 schema 打开后自动 ALTER 加 batch_id ----
    old = os.path.join(tmp, "legacy.db")
    con = sqlite3.connect(old)
    # 模拟真实旧库：t_result 含全部列 + student_id 关联，但缺 batch_id
    con.executescript(
        "CREATE TABLE t_result ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "student_id INTEGER, student_name TEXT, id_card TEXT, address TEXT, "
        "province_code TEXT, province_name TEXT, city_code TEXT, city_name TEXT, "
        "district_code TEXT, district_name TEXT, match_status TEXT, "
        "engine1_result TEXT, engine2_result TEXT, remark TEXT, "
        "created_at TEXT DEFAULT (datetime('now','localtime')));")
    con.close()
    db_legacy = DBManager(old)
    cols = [r[1] for r in db_legacy._conn.execute(
        "PRAGMA table_info(t_result)").fetchall()]
    assert "batch_id" in cols, "P10 旧库应 ALTER 加 batch_id"
    db_legacy.insert_result({
        "batch_id": "OLD", "student_name": "老张三",
        "id_card": "110108199001010011", "address": "", "province_code": "",
        "province_name": "", "city_code": "", "city_name": "", "district_code": "",
        "district_name": "", "match_status": "已解析",
        "engine1_result": "", "engine2_result": "", "remark": ""})
    assert db_legacy.count_results(batch_id="OLD") == 1, "P10 新格式写入应成功"
    print("  [P10] 旧库 ALTER 迁移 + 新格式写入 OK")

    # ---- P11: 删冗余 engine1_result/engine2_result 动态属性 ----
    r_ok = match_service.run_match(db_legacy, "110108199001010011", "北京市海淀区",
                                    with_detail=True)
    assert not hasattr(r_ok, "engine1_result"), "P11 MatchResult 不应有 engine1_result 属性"
    assert not hasattr(r_ok, "engine2_result"), "P11 MatchResult 不应有 engine2_result 属性"
    assert r_ok.engine1_detail != "", "P11 with_detail=True 时 engine1_detail 应有内容"
    print("  [P11] 冗余 engine1_result/engine2_result 动态属性已删除 OK")

    # ---- P12: with_detail 惰性 ----（用已 seed 区划的 db；db_legacy 无区划字典）
    r_off = match_service.run_match(db, "110108199001010011", "北京市海淀区",
                                    with_detail=False)
    assert r_off.engine1_detail == "", "P12 with_detail=False 不应构建 detail"
    assert r_off.province_code, "P12 即便不构建 detail，匹配结果仍应有省代码"
    r_on = match_service.run_match(db, "110108199001010011", "北京市海淀区",
                                   with_detail=True)
    assert r_on.engine1_detail != "", "P12 with_detail=True 应构建 detail"
    print("  [P12] detail 惰性（with_detail 控制）OK")

    # ---- P13: config.txt 缓存 ----
    # 注：sandbox 禁止删除文件，故不备份/删除，仅覆盖写回原内容。
    cfgp = os.path.join(ROOT, "config.txt")
    orig_cfg = None
    if os.path.exists(cfgp):
        with open(cfgp, "r", encoding="utf-8") as f:
            orig_cfg = f.read()
    try:
        with open(cfgp, "w", encoding="utf-8") as f:
            f.write("PORT=5555\n")
        r1 = app_module._read_config_file()
        assert r1.get("PORT") == "5555", "P13 首次读取应解析"
        with open(cfgp, "w", encoding="utf-8") as f:
            f.write("PORT=7777\n")
        r2 = app_module._read_config_file()
        assert r2.get("PORT") == "5555", "P13 二次调用应命中缓存，不重读"
        print("  [P13] config.txt 模块级缓存 OK")
    finally:
        if orig_cfg is not None:
            with open(cfgp, "w", encoding="utf-8") as f:
                f.write(orig_cfg)
        else:
            with open(cfgp, "w", encoding="utf-8") as f:
                f.write("")

    # ---- P14: 默认 Excel TTL 缓存 ----
    orig_data = config.DATA_DIR
    try:
        td = tempfile.mkdtemp()
        config.DATA_DIR = td
        open(os.path.join(td, "a.xlsx"), "w").close()
        p1 = app_module._find_default_excel()
        assert p1 and p1.endswith("a.xlsx"), "P14 首次应扫到 a.xlsx"
        os.remove(os.path.join(td, "a.xlsx"))
        p2 = app_module._find_default_excel()  # TTL 内应返回缓存
        assert p2 == p1, "P14 TTL 内应返回缓存路径，不重新 glob"
        print("  [P14] 默认 Excel 路径 TTL 缓存 OK")
    finally:
        config.DATA_DIR = orig_data

    print("ALL FINAL CHECKS PASSED")


if __name__ == "__main__":
    main()
