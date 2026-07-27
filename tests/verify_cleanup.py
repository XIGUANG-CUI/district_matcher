"""验证"一进一出"清理逻辑：导出成功后学生流水被清除，区划字典保留。

运行：python tests/verify_cleanup.py
"""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from database.db_manager import DBManager
from services import export_service


def main():
    tmp = tempfile.mkdtemp()
    db_path = os.path.join(tmp, "dm_test.db")
    db = DBManager(db_path)

    # 1) 预备区划字典（应当长期保留）
    db.insert_districts([{
        "id": "prov-110", "pids": "", "pid": "", "district_code": "110000000000",
        "district_name": "北京市", "admin_level": 1,
        "province_code": "110000", "city_code": "",
    }])
    assert db.count_districts() == 1, "区划字典预置失败"

    # 2) 模拟导入学生流水
    db.insert_students([{
        "batch_id": "B1", "student_name": "张三",
        "id_card": "110108199001010011", "address": "北京市海淀区",
    }])
    assert len(db.get_students_by_batch("B1")) == 1

    # 3) 模拟处理结果
    db.insert_result({
        "batch_id": "B1", "student_name": "张三", "id_card": "110108199001010011",
        "address": "北京市海淀区", "province_code": "110000000000",
        "province_name": "北京市", "city_code": "", "city_name": "",
        "district_code": "", "district_name": "", "match_status": "已解析",
        "engine1_result": "x", "engine2_result": "y", "remark": "test",
    })
    assert len(db.query_results(batch_id="B1")) == 1

    # 4) 导出并清理（按批次）
    path = export_service.export_results(db, batch_id="B1", fmt="xlsx", cleanup=True)
    assert os.path.exists(path), "导出文件未生成"
    assert len(db.get_students_by_batch("B1")) == 0, "学生流水未清理"
    assert len(db.query_results(batch_id="B1")) == 0, "结果流水未清理"
    assert db.count_districts() == 1, "区划字典被误删！"

    # 5) 全量场景：再导入一批，全量导出清理
    db.insert_students([{
        "batch_id": "B2", "student_name": "李四",
        "id_card": "310104199002020022", "address": "上海市",
    }])
    db.insert_result({
        "batch_id": "B2", "student_name": "李四", "id_card": "310104199002020022",
        "address": "上海市", "province_code": "", "province_name": "",
        "city_code": "", "city_name": "", "district_code": "", "district_name": "",
        "match_status": "无匹配", "engine1_result": "", "engine2_result": "", "remark": "",
    })
    path2 = export_service.export_results(db, batch_id=None, fmt="csv", cleanup=True)
    assert os.path.exists(path2)
    assert len(db.get_students_by_batch("B2")) == 0, "全量清理失败"
    assert db.count_districts() == 1, "全量清理误删字典"

    # 6) 部分状态导出不触发清理
    db.insert_students([{
        "batch_id": "B3", "student_name": "王五",
        "id_card": "440305199003030033", "address": "广东省深圳市",
    }])
    db.insert_result({
        "batch_id": "B3", "student_name": "王五", "id_card": "440305199003030033",
        "address": "广东省深圳市", "province_code": "", "province_name": "",
        "city_code": "", "city_name": "", "district_code": "", "district_name": "",
        "match_status": "无匹配", "engine1_result": "", "engine2_result": "", "remark": "",
    })
    export_service.export_results(db, batch_id="B3", status="无匹配", fmt="xlsx", cleanup=True)
    assert len(db.get_students_by_batch("B3")) == 1, "部分状态导出不应清理"
    db.delete_batch("B3")

    print("ALL CLEANUP CHECKS PASSED")


if __name__ == "__main__":
    main()
