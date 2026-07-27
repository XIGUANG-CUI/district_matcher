"""冒烟测试：用合成 Excel 验证导入(列识别+6位补零)+批量匹配+导出整条链路。"""
import os, sys, tempfile, sqlite3
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config
from database.db_manager import DBManager
from services import import_service, match_service, export_service

tmp = tempfile.mkdtemp()
db_path = os.path.join(tmp, "smoke.db")

# 1) 生成行政区划 Excel（6位代码 + 中文表头，测试自动识别与补零）
dist_df = pd.DataFrame([
    ["230000", "黑龙江省", "省"],
    ["230100", "哈尔滨市", "市"],
    ["230102", "道外区", "区"],
    ["110000", "北京市", "直辖市"],
    ["110108", "海淀区", "区"],
], columns=["行政区划代码", "行政区划名称", "行政级别"])
dist_xlsx = os.path.join(tmp, "行政编码数据.xlsx")
dist_df.to_excel(dist_xlsx, index=False)

# 2) 生成学生 Excel
stu_df = pd.DataFrame([
    ["张三", "23010519890714291X", "哈尔滨市道外区水泥路99-4号"],
    ["李四", "110108200003154578", "北京市海淀区中关村大街1号"],
    ["王五", "230102199001011234", "哈尔滨市道外区中央大街"],
], columns=["姓名", "身份证号", "地址"])
stu_xlsx = os.path.join(tmp, "students.xlsx")
stu_df.to_excel(stu_xlsx, index=False)

db = DBManager(db_path)

# 3) 导入区划
summ = import_service.import_district_excel(db, dist_xlsx)
print("区划导入:", summ)
assert summ["imported"] == 5

# 4) 导入学生
sinfo = import_service.import_students(db, stu_xlsx)
print("学生导入:", sinfo)
bid = sinfo["batch_id"]

# 5) 批量匹配
stats = match_service.process_batch(db, bid)
print("匹配统计:", stats)
# v2：ID 与地址同省同市解析为“正确”，故不再强求“已解析”计数。
# 仅校验全量解析、无无匹配/异常。
assert stats["总计"] == 3
assert stats["无匹配"] == 0 and stats["异常"] == 0
assert (stats["正确"] + stats["已解析"] + stats["已解析(仅证件)"]
        + stats["需复核"] + stats["部分"]) == 3

# 6) 抽查张三（230105 -> 道外区；v2 下引擎①省/市与引擎②一致 → "正确"）
rows = db.query_results(batch_id=bid, status=None, page=1, page_size=10)
zhang = [r for r in rows if r["student_name"] == "张三"][0]
print("张三结果:", zhang["district_name"], zhang["district_code"], zhang["match_status"])
assert zhang["district_code"] == "230102000000"
assert zhang["match_status"] == "正确"

# 7) 导出
ep = export_service.export_results(db, batch_id=bid, fmt="xlsx")
print("导出文件:", ep)
assert os.path.exists(ep)

print("\n✅ 端到端冒烟测试通过")
