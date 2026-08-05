"""单条查询业务流端到端验证（Flask 页面 /query）。

运行：python tests/verify_query.py

覆盖：
- GET /query 渲染表单
- POST 仅 6 位区划代码 -> 已解析(仅证件)，输入值回填
- POST 仅地址 -> 已解析（引擎②为主）
- POST 6位码+地址 -> 正确
- POST 非法输入+空地址 -> 异常提示
- POST 全空 -> flash 提示（页面仍 200）
"""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

TMP = tempfile.mkdtemp(prefix="qry_ver_")

import config
config.DB_PATH = os.path.join(TMP, "test.db")
config.DATA_DIR = os.path.join(TMP, "data")
config.UPLOAD_DIR = os.path.join(TMP, "upload")
os.makedirs(config.DATA_DIR, exist_ok=True)
os.makedirs(config.UPLOAD_DIR, exist_ok=True)

from database.db_manager import DBManager
import app as flask_app

db = DBManager(config.DB_PATH)
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

flask_app.app.config["TESTING"] = True
client = flask_app.app.test_client()

# 1) GET 表单
r = client.get("/query")
html = r.get_data(as_text=True)
assert r.status_code == 200 and "6 位区划代码" in html
print("[OK] GET /query 渲染表单（含新说明文案）")

# 2) 仅 6 位码
r = client.post("/query", data={"id_card": "230105", "address": ""})
html = r.get_data(as_text=True)
assert "已解析(仅证件)" in html and "道外区" in html and "230104000000" in html
assert 'value="230105"' in html, "输入应回填"
print("[OK] 仅 6 位码 -> 已解析(仅证件)，输入回填")

# 3) 仅地址
r = client.post("/query", data={"id_card": "", "address": "黑龙江省哈尔滨市道外区水泥路99-4号"})
html = r.get_data(as_text=True)
assert "已解析" in html and "道外区" in html
assert "未输入有效的身份证/区划代码" in html, "引擎①应显示未执行说明"
print("[OK] 仅地址 -> 已解析（引擎②为主，引擎①显示未执行）")

# 4) 6位码 + 地址
r = client.post("/query", data={"id_card": "230105", "address": "黑龙江省哈尔滨市道外区水泥路99-4号"})
html = r.get_data(as_text=True)
assert "正确" in html and "历史映射" in html
print("[OK] 6位码+地址 -> 正确（含历史映射标注）")

# 5) 非法 + 空地址
r = client.post("/query", data={"id_card": "abc123", "address": ""})
html = r.get_data(as_text=True)
assert "异常" in html and "未输入有效的身份证/区划代码或地址" in html
assert "省级" not in html, "异常时不应渲染三级表格"
print("[OK] 非法+空地址 -> 异常提示，不渲染空表格")

# 6) 全空
r = client.post("/query", data={"id_card": "", "address": ""})
html = r.get_data(as_text=True)
assert r.status_code == 200 and "请至少输入身份证/区划代码或地址" in html
print("[OK] 全空 -> flash 提示，页面 200")

print("\nALL QUERY-FLOW CHECKS PASSED")
db.close()