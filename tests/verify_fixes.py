"""验证 D1-D6 修复：列映射、分页计数、SDK 独立集成、身份证校验。

运行：python tests/verify_fixes.py
"""
import os
import sys
import tempfile
import csv

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from database.db_manager import DBManager
from services import import_service, match_service
from sdk import district_matcher as sdk_mod  # D5: 包内独立 import


def main():
    tmp = tempfile.mkdtemp()
    db_path = os.path.join(tmp, "fix_test.db")
    db = DBManager(db_path)

    # ---------- D1: 列映射接入 ----------
    csv_path = os.path.join(tmp, "stu.csv")
    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["证件号", "住址", "姓名"])
        w.writerow(["110108199001010011", "北京市海淀区", "张三"])
    info = import_service.import_students(
        db, csv_path,
        mapping={"id_card": "证件号", "address": "住址", "name": "姓名"})
    rows = db.get_students_by_batch(info["batch_id"])
    assert len(rows) == 1, "D1 导入行数异常"
    assert rows[0]["id_card"] == "110108199001010011", "D1 列映射未生效"
    assert rows[0]["student_name"] == "张三"

    # ---------- D2: 分页计数 ----------
    bid2 = "B2"
    db.insert_students([
        {"batch_id": bid2, "student_name": f"s{i}",
         "id_card": f"id{i}", "address": ""}
        for i in range(120)
    ])
    stus = db.get_students_by_batch(bid2)
    res_rows = [{
        "batch_id": bid2, "student_name": st["student_name"],
        "id_card": st["id_card"], "address": "",
        "province_code": "", "province_name": "",
        "city_code": "", "city_name": "", "district_code": "", "district_name": "",
        "match_status": "已解析", "engine1_result": "", "engine2_result": "", "remark": "",
    } for st in stus]
    # P10：t_result 自包含 batch_id，用批量写入新签名
    db.insert_results_batch(res_rows)

    total = db.count_results(batch_id=bid2)
    assert total == 120, f"D2 count_results 应为120，实际 {total}"
    p1 = db.query_results(batch_id=bid2, page=1, page_size=50)
    p3 = db.query_results(batch_id=bid2, page=3, page_size=50)
    assert len(p1) == 50, "D2 第1页应为50条"
    assert len(p3) == 20, "D2 第3页应为20条"

    # ---------- D6: 身份证格式校验 ----------
    r_bad = match_service.run_match(db, "not_an_id", "")
    assert r_bad.match_status == "异常", "D6 非法身份证应标异常"
    r_ok = match_service.run_match(db, "110108199001010011", "北京市海淀区")
    assert r_ok.match_status != "异常", "D6 合法身份证不应标异常"

    # ---------- D5: SDK 独立集成 ----------
    assert hasattr(sdk_mod, "DistrictMatcher"), "D5 SDK 未暴露 DistrictMatcher"
    matcher = sdk_mod.DistrictMatcher(db_path=db_path)
    mres = matcher.match("not_an_id", "")
    assert mres["match_status"] == "异常", "D5 SDK match 异常分支失败"

    print("ALL FIX CHECKS PASSED")


if __name__ == "__main__":
    main()
