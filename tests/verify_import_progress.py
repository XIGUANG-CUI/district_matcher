"""验证导入进度机制：on_progress 回调 + 后台任务 /api/import/start + /api/import/progress。"""
import os
import sys
import csv
import time
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import config
TMP = tempfile.mkdtemp(prefix="imp_ver_")
config.DB_PATH = os.path.join(TMP, "test.db")
config.DATA_DIR = os.path.join(TMP, "data")
config.UPLOAD_DIR = os.path.join(TMP, "upload")
os.makedirs(config.DATA_DIR, exist_ok=True)
os.makedirs(config.UPLOAD_DIR, exist_ok=True)

import pandas as pd
from database.db_manager import DBManager
from services import import_service

db = DBManager(config.DB_PATH)

# ---------------- 1) 区划导入 on_progress ----------------
sheets = {
    "北京市": [
        {"ID": 1, "行政区域代码": "110000000000", "行政区域名称": "北京市", "行政层级": 1},
        {"ID": 2, "行政区域代码": "110100000000", "行政区域名称": "市辖区", "行政层级": 2},
        {"ID": 3, "行政区域代码": "110108000000", "行政区域名称": "海淀区", "行政层级": 3},
    ],
    "广东省": [
        {"ID": 4, "行政区域代码": "440000000000", "行政区域名称": "广东省", "行政层级": 1},
        {"ID": 5, "行政区域代码": "440300000000", "行政区域名称": "深圳市", "行政层级": 2},
        {"ID": 6, "行政区域代码": "440305000000", "行政区域名称": "南山区", "行政层级": 3},
    ],
}
dist_xlsx = os.path.join(TMP, "dist.xlsx")
with pd.ExcelWriter(dist_xlsx, engine="openpyxl") as w:
    for name, rows in sheets.items():
        pd.DataFrame(rows).to_excel(w, sheet_name=name, index=False)

calls = []
def cb(done, total, info):
    calls.append((done, total, info.get("phase") if isinstance(info, dict) else None))

summary = import_service.import_district_excel(db, dist_xlsx, on_progress=cb)
assert summary["imported"] == 6, summary
assert len(calls) >= 3, calls
assert calls[0][0] == 0
assert calls[-1][1] == 2 and calls[-1][0] == 2 and calls[-1][2] == "done", calls[-1]
dones = [c[0] for c in calls]
assert dones == sorted(dones), dones
print("[OK] 区划 on_progress 触发 %d 次，阶段=%s" % (len(calls), sorted(set(c[2] for c in calls))))
db.reset_db()

# ---------------- 2) 学生导入 on_progress ----------------
stu_csv = os.path.join(TMP, "stu.csv")
pd.DataFrame(
    [{"姓名": f"学生{i}", "身份证号": f"{i:018d}", "地址": f"地址{i}"} for i in range(350)]
).to_csv(stu_csv, index=False, encoding="utf-8-sig")

calls2 = []
def cb2(done, total, info):
    calls2.append((done, total, info.get("phase") if isinstance(info, dict) else None))

sinfo = import_service.import_students(db, stu_csv, on_progress=cb2)
assert sinfo["imported"] == 350, sinfo
assert calls2[-1][2] == "done" and calls2[-1][0] == calls2[-1][1] == 350, calls2[-1]
assert any(c[2] == "writing" for c in calls2)
print("[OK] 学生 on_progress 触发 %d 次，阶段=%s" % (len(calls2), sorted(set(c[2] for c in calls2))))
db.reset_db()

# ---------------- 3) Flask 路由：/api/import/start + /api/import/progress ----------------
import app as flask_app
flask_app.app.config["TESTING"] = True
client = flask_app.app.test_client()

# 3a 学生上传
with open(stu_csv, "rb") as f:
    r = client.post("/api/import/start",
                    data={"kind": "student", "file": (f, "stu.csv")})
j = r.get_json()
assert j.get("ok"), j
tid = j["task_id"]
final = None
deadline = time.time() + 20
while time.time() < deadline:
    pr = client.get("/api/import/progress?task_id=" + tid).get_json()
    if pr["status"] in ("done", "error"):
        final = pr
        break
    time.sleep(0.15)
print("[route] 学生导入进度终态:", final)
assert final and final["status"] == "done", final
assert final["imported"] == 350, final

# 3b 区划上传
with open(dist_xlsx, "rb") as f:
    r = client.post("/api/import/start",
                    data={"kind": "district", "action": "upload", "file": (f, "dist.xlsx")})
j = r.get_json()
assert j.get("ok"), j
tid2 = j["task_id"]
final2 = None
deadline = time.time() + 20
while time.time() < deadline:
    pr = client.get("/api/import/progress?task_id=" + tid2).get_json()
    if pr["status"] in ("done", "error"):
        final2 = pr
        break
    time.sleep(0.15)
print("[route] 区划导入进度终态:", final2)
assert final2 and final2["status"] == "done", final2
assert final2["imported"] == 6, final2

# 3c 未知类型应被拒绝
r = client.post("/api/import/start", data={"kind": "bogus"})
assert r.get_json().get("ok") is False, r.get_json()
print("[OK] 未知导入类型被正确拒绝")

# 3d 进度接口对未知 task_id 返回 idle
pr = client.get("/api/import/progress?task_id=nope").get_json()
assert pr["status"] == "idle", pr
print("[OK] 未知 task_id 返回 idle")

print("\nALL IMPORT-PROGRESS CHECKS PASSED")
db.close()
