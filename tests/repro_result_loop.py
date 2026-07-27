"""复现：批量处理进度条无限循环。
模拟真实场景——后台线程 process_batch 与 Flask 主线程共用同一 DBManager
（P01 单连接 + Lock），观察 progress 接口是否最终返回 done。
"""
import os, sys, time, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
from database.db_manager import DBManager
from services import import_service
import app as appmod

tmp = tempfile.mkdtemp()
db_path = os.path.join(tmp, "repro.db")
db = DBManager(db_path)

# 1) seed 区划
dist_df = pd.DataFrame([
    ["230000", "黑龙江省", "省"],
    ["230100", "哈尔滨市", "市"],
    ["230102", "道外区", "区"],
    ["110000", "北京市", "直辖市"],
    ["110108", "海淀区", "区"],
], columns=["行政区划代码", "行政区划名称", "行政级别"])
dx = os.path.join(tmp, "d.xlsx")
dist_df.to_excel(dx, index=False)
import_service.import_district_excel(db, dx)

# 2) seed 1500 条学生（体现大批次并发）
rows = []
for i in range(1500):
    rows.append([
        "学生%d" % i,
        "23010519890714291X" if i % 2 == 0 else "110108200003154578",
        "哈尔滨市道外区水泥路%d号" % i,
    ])
stu_df = pd.DataFrame(rows, columns=["姓名", "身份证号", "地址"])
sx = os.path.join(tmp, "s.xlsx")
stu_df.to_excel(sx, index=False)
sinfo = import_service.import_students(db, sx)
bid = sinfo["batch_id"]
print("seed batch =", bid, "imported =", sinfo["imported"])

# 3) 替换 app 全局 db 为临时库（后台线程与主线程将共用同一连接）
appmod.db = db
client = appmod.app.test_client()

# 4) POST 触发后台处理（内部 result_page 还会用同一 db 渲染）
r = client.post("/result", data={"action": "process", "batch_id": bid})
print("POST /result ->", r.status_code)

# 5) 轮询进度，最多 60s
t0 = time.time()
last = None
while time.time() - t0 < 60:
    p = client.get("/api/result/progress?batch_id=" + bid).get_json()
    now = time.time() - t0
    if p != last:
        print("%.1fs  %s" % (now, p))
        last = p
    if p.get("status") in ("done", "error"):
        print(">>> 终态:", p)
        break
    time.sleep(0.5)
else:
    print("!!! TIMEOUT：progress 卡在 running 永不结束 —— 复现无限循环")

db.close()
