"""历史代码映射库化（t_history_mapping）端到端验证。

运行：python tests/verify_history.py

覆盖：
- 首次建库自动种子（16 条）
- 引擎①旧码直查（230105 -> 230104 道外区，mapped 标记）
- 引擎②地址别名翻译（太平区 -> 道外区）
- 决策器全流程（正确）
- Flask 页面 /history、模板下载 /template/history
- REST API：list / add / delete / reset
- Excel 批量导入
"""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

TMP = tempfile.mkdtemp(prefix="his_ver_")

import config
config.DB_PATH = os.path.join(TMP, "test.db")
config.DATA_DIR = os.path.join(TMP, "data")
config.UPLOAD_DIR = os.path.join(TMP, "upload")
os.makedirs(config.DATA_DIR, exist_ok=True)
os.makedirs(config.UPLOAD_DIR, exist_ok=True)

import pandas as pd
from database.db_manager import DBManager
from database import history_data
from engine import engine1, engine2
from services import match_service, import_service
import app as flask_app  # 触发 Flask app 构建（使用上述临时 DB_PATH）

db = DBManager(config.DB_PATH)

# ---------- 1) 内置种子 ----------
assert db.count_history_mappings() == len(history_data.HISTORY_SEED) == 16, db.count_history_mappings()
alias = db.get_history_alias_map()
code = db.get_history_code_map()
assert alias["太平区"] == "道外区"
assert code["230105"] == "230104"
print("[OK] 内置种子 16 条自动写入，别名/代码映射正确")

# ---------- 2) 区划字典（真实代码：道外区=230104） ----------
db.insert_districts([
    {"id": "230000000000", "pids": "", "pid": None,
     "district_code": "230000000000", "district_name": "黑龙江省",
     "admin_level": 1, "province_code": "230000000000", "city_code": None},
    {"id": "230100000000", "pids": "230000000000", "pid": "230000000000",
     "district_code": "230100000000", "district_name": "哈尔滨市",
     "admin_level": 2, "province_code": "230000000000",
     "city_code": "230100000000"},
    {"id": "230104000000", "pids": "230000000000,230100000000",
     "pid": "230100000000", "district_code": "230104000000",
     "district_name": "道外区", "admin_level": 3,
     "province_code": "230000000000", "city_code": "230100000000"},
])

# ---------- 3) 引擎①旧码直查 ----------
e1 = engine1.engine1_match(db, "23010519890714291X")
assert e1.district.matched and e1.district.mapped, e1
assert e1.district.name == "道外区" and e1.district.code == "230104000000", e1
print("[OK] 引擎① 230105(太平区) 经历史映射命中 230104(道外区)，mapped=True")

# ---------- 4) 引擎②地址别名 ----------
e2 = engine2.engine2_parse(db, "哈尔滨市太平区南马路1号", "23010519890714291X")
assert e2.district == "道外区" and e2.district_code == "230104000000", e2
print("[OK] 引擎② 地址旧名「太平区」经 DB 别名翻译命中道外区")

# ---------- 5) 决策器全流程 ----------
res = match_service.run_match(
    db, "23010519890714291X", "黑龙江省哈尔滨市道外区水泥路99-4号",
    with_detail=True)
assert res.match_status == "正确", res.match_status
assert res.district_name == "道外区" and res.district_code == "230104000000", res
assert "历史映射" in res.engine1_detail, res.engine1_detail
print("[OK] 决策器：引擎②解析 + 引擎①历史映射校验一致 -> 正确，详情标注历史映射")

# ---------- 6) Flask 页面 / 模板 ----------
flask_app.app.config["TESTING"] = True
client = flask_app.app.test_client()
r = client.get("/history")
html = r.get_data(as_text=True)
assert r.status_code == 200 and "太平区" in html and "道外区" in html
print("[OK] GET /history 页面渲染，含内置种子记录")

r = client.get("/template/history")
assert r.status_code == 200 and "spreadsheetml" in r.content_type
print("[OK] GET /template/history 模板下载")

# ---------- 7) REST API ----------
j = client.get("/api/history/list").get_json()
assert j["code"] == 0 and len(j["data"]) == 16
r = client.post("/api/history/add", json={
    "old_code": "999999", "new_code": "888888",
    "old_name": "旧名API", "new_name": "新名API"})
assert r.get_json()["ok"], r.get_json()
added = [x for x in flask_app.db.list_history_mappings() if x["old_code"] == "999999"]
assert len(added) == 1
r = client.post("/api/history/delete", json={"id": added[0]["id"]})
assert r.get_json()["ok"], r.get_json()
r = client.post("/api/history/reset")
assert r.get_json()["ok"], r.get_json()
assert flask_app.db.count_history_mappings() == 16
print("[OK] API list/add/delete/reset 正常")

# ---------- 8) Excel 批量导入 ----------
xlsx = os.path.join(TMP, "h.xlsx")
pd.DataFrame([
    {"旧代码": "111111", "新代码": "222222", "旧名称": "甲", "新名称": "乙",
     "变更类型": "测试", "变更日期": "2020", "备注": ""},
    {"旧代码": "333333", "新代码": "444444", "旧名称": "", "新名称": ""},
    {"旧代码": "", "新代码": "", "旧名称": "残缺", "新名称": ""},
]).to_excel(xlsx, index=False)
summary = import_service.import_history_mappings(db, xlsx)
assert summary == {"imported": 2, "skipped": 1}, summary
assert db.get_history_code_map()["111111"] == "222222"
print("[OK] Excel 导入：成功 2 条 / 跳过 1 条，upsert 生效")

print("\nALL HISTORY-MAPPING CHECKS PASSED")
db.close()