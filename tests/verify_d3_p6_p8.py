"""D3 / P06 / P07 / P08 修复验证脚本（全程使用临时库与临时文件，不污染真实数据）。

覆盖：
- D3：import_service.preview_student_file 返回列/识别映射/前10行；/import/preview 路由可用。
- P06：import_students 用 to_dict("records") 替代 iterrows()，结果正确（空身份证跳过）。
- P07：import_district_excel 多 sheet 合并为单次事务，缺失省级自动合成。
- P08：export_results 走 get_results_dataframe 直读，导出文件为中文列名。
"""
import os
import sys
import io
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import config
import pandas as pd

_tmp = tempfile.mkdtemp(prefix="dm_verify_")
config.DB_PATH = os.path.join(_tmp, "verify.db")
config.UPLOAD_DIR = _tmp

from database.db_manager import DBManager
from services import import_service, export_service, match_service
import app as app_module  # 触发 Flask app 构建（使用上述临时 DB_PATH）


def _populate_districts(db):
    rows = [
        {"id": "110000000000", "pids": "", "pid": None, "district_code": "110000000000",
         "district_name": "北京市", "admin_level": 1, "province_code": "110000000000", "city_code": None},
        {"id": "110100000000", "pids": "110000000000", "pid": "110000000000", "district_code": "110100000000",
         "district_name": "市辖区", "admin_level": 2, "province_code": "110000000000", "city_code": "110100000000"},
        {"id": "110108000000", "pids": "110000000000,110100000000", "pid": "110100000000", "district_code": "110108000000",
         "district_name": "海淀区", "admin_level": 3, "province_code": "110000000000", "city_code": "110100000000"},
        {"id": "230000000000", "pids": "", "pid": None, "district_code": "230000000000",
         "district_name": "黑龙江省", "admin_level": 1, "province_code": "230000000000", "city_code": None},
        {"id": "230100000000", "pids": "230000000000", "pid": "230000000000", "district_code": "230100000000",
         "district_name": "哈尔滨市", "admin_level": 2, "province_code": "230000000000", "city_code": "230100000000"},
        {"id": "230105000000", "pids": "230000000000,230100000000", "pid": "230100000000", "district_code": "230105000000",
         "district_name": "道外区", "admin_level": 3, "province_code": "230000000000", "city_code": "230100000000"},
    ]
    db.insert_districts(rows)


def test_d3_preview_function():
    csv_path = os.path.join(_tmp, "stu.csv")
    pd.DataFrame([
        {"姓名": "张三", "身份证号": "23010519900101001X", "地址": "黑龙江省哈尔滨市道外区南马路1号"},
        {"姓名": "李四", "身份证号": "110108200003154578", "地址": "北京市海淀区中关村大街1号"},
    ]).to_csv(csv_path, index=False, encoding="utf-8-sig")
    pv = import_service.preview_student_file(csv_path)
    assert pv["detected"]["id_card"] == "身份证号", pv["detected"]
    assert pv["detected"]["address"] == "地址"
    assert len(pv["rows"]) == 2
    assert "姓名" in pv["columns"]
    print("  [D3] preview_student_file 正确：识别列 + 前2行")


def test_d3_preview_route():
    content = "姓名,身份证号,地址\n张三,23010519900101001X,黑龙江哈尔滨道外区\n"
    client = app_module.app.test_client()
    res = client.post("/import/preview",
                      data={"file": (io.BytesIO(content.encode("utf-8-sig")), "s.csv")},
                      content_type="multipart/form-data")
    assert res.status_code == 200, res.status_code
    data = res.get_json()
    assert data["ok"] is True
    assert data["detected"]["id_card"] == "身份证号"
    assert len(data["rows"]) == 1
    print("  [D3] /import/preview 路由可用：返回预览 + 识别映射")


def test_p06_vectorized_import():
    db = DBManager()
    db.reset_db()
    _populate_districts(db)
    csv_path = os.path.join(_tmp, "stu2.csv")
    # 第三条身份证为空，应被跳过
    pd.DataFrame([
        {"姓名": "张三", "身份证号": "23010519900101001X", "地址": "黑龙江省哈尔滨市道外区南马路1号"},
        {"姓名": "李四", "身份证号": "110108200003154578", "地址": "北京市海淀区中关村大街1号"},
        {"姓名": "王五", "身份证号": "", "地址": "地址未知"},
    ]).to_csv(csv_path, index=False, encoding="utf-8-sig")
    info = import_service.import_students(db, csv_path)
    assert info["imported"] == 2, info
    students = db.get_students_by_batch(info["batch_id"])
    assert len(students) == 2
    print("  [P06] import_students 向量化后数量正确（空身份证已跳过）：导入 %d 条" % info["imported"])


def test_p07_merged_transaction():
    db = DBManager()
    db.reset_db()
    xlsx_path = os.path.join(_tmp, "district_multi.xlsx")
    bj = pd.DataFrame([
        {"行政区域代码": "110100000000", "行政区域名称": "市辖区", "行政层级": 2,
         "PID": "110000000000", "PIDS": "110000000000"},
        {"行政区域代码": "110108000000", "行政区域名称": "海淀区", "行政层级": 3,
         "PID": "110100000000", "PIDS": "110000000000,110100000000"},
    ])
    gd = pd.DataFrame([
        {"行政区域代码": "440100000000", "行政区域名称": "市辖区", "行政层级": 2,
         "PID": "440000000000", "PIDS": "440000000000"},
        {"行政区域代码": "440305000000", "行政区域名称": "南山区", "行政层级": 3,
         "PID": "440100000000", "PIDS": "440000000000,440100000000"},
    ])
    with pd.ExcelWriter(xlsx_path, engine="openpyxl") as w:
        bj.to_excel(w, sheet_name="北京市", index=False)
        gd.to_excel(w, sheet_name="广东省", index=False)
    summary = import_service.import_district_excel(db, xlsx_path)
    # 每个 sheet 缺省级 -> 各合成 1 个省级行；2 sheet * 2 行 + 2 合成 = 6
    assert summary["imported"] == 6, summary
    assert summary["sheets"] == 2
    # 合成的省级行：名称取自 sheet 名，且可被缓存查到
    assert db.get_by_district_code("110000000000", 1)["district_name"] == "北京市"
    assert db.get_by_district_code("440000000000", 1)["district_name"] == "广东省"
    print("  [P07] 多 sheet 合并为单次事务：导入 %d 条，缺失省级已合成" % summary["imported"])


def test_p08_export_dataframe():
    db = DBManager()
    db.reset_db()
    _populate_districts(db)
    csv_path = os.path.join(_tmp, "stu3.csv")
    pd.DataFrame([
        {"姓名": "张三", "身份证号": "23010519900101001X", "地址": "黑龙江省哈尔滨市道外区南马路1号"},
        {"姓名": "李四", "身份证号": "110108200003154578", "地址": "北京市海淀区中关村大街1号"},
    ]).to_csv(csv_path, index=False, encoding="utf-8-sig")
    info = import_service.import_students(db, csv_path)
    match_service.process_batch(db, info["batch_id"])

    # 直读 DataFrame（英文列名）
    df_en = db.get_results_dataframe(batch_id=info["batch_id"])
    assert "id_card" in df_en.columns and "match_status" in df_en.columns
    assert len(df_en) == 2

    # 导出（P08：rename 中文列名，单次写出）
    path = export_service.export_results(db, batch_id=info["batch_id"], fmt="xlsx", cleanup=False)
    assert os.path.exists(path)
    df_out = pd.read_excel(path)
    expected_cols = [cn for _, cn in export_service.COLUMNS]
    assert list(df_out.columns) == expected_cols, list(df_out.columns)
    assert len(df_out) == 2
    # 一进一出不被触发（cleanup=False）
    assert db.count_results(batch_id=info["batch_id"]) == 2
    print("  [P8] 导出走直读 DataFrame：文件列名=%s，行数=%d" % (expected_cols, len(df_out)))


def main():
    print("===== D3 / P06 / P07 / P08 验证 =====")
    test_d3_preview_function()
    test_d3_preview_route()
    test_p06_vectorized_import()
    test_p07_merged_transaction()
    test_p08_export_dataframe()
    print("ALL D3/P06/P07/P08 CHECKS PASSED")


if __name__ == "__main__":
    main()
