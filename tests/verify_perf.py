"""性能优化验证（P01/P02/P03/P04/P05）：

- 正确性：批量匹配结果可查询、分页计数正确
- 吞吐：对 N 条学生计时，报告条/秒（对比改造前的“每条多次建连+逐条提交+重复查库”）
- P03 缓存失效：插入新区划后能被立即查到
- P04 后台处理：经 Flask test_client 触发后台任务并轮询进度接口

运行：python tests/verify_perf.py
"""
import os
import sys
import time
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import config

# 关键：在 import app 之前把数据库指向临时文件，避免污染真实库
fd, _tmp = tempfile.mkstemp(suffix=".db")
os.close(fd)
os.remove(_tmp)
config.DB_PATH = _tmp

from database.db_manager import DBManager
from services import match_service
from tests._fixture import FIXTURE_ROWS


def build_students(n):
    codes = ["23010219890714291X", "23010519890714291X",
             "110108199003074567", "230123201004231812"]
    addrs = ["哈尔滨市道外区水泥路99-4号", "北京市海淀区中关村大街1号",
             "黑龙江省依兰县三道岗镇东河村联合屯", ""]
    rows = []
    for i in range(n):
        rows.append({
            "batch_id": "PERF",
            "student_name": f"学生{i}",
            "id_card": codes[i % 4],
            "address": addrs[i % 4],
        })
    return rows


def main():
    db = DBManager(config.DB_PATH)
    db.insert_districts(FIXTURE_ROWS)

    N = 4000
    db.insert_students(build_students(N))

    # ---------- 计时 process_batch（P01/P02/P03 生效） ----------
    t0 = time.time()
    stats = match_service.process_batch(db, "PERF")
    dt = time.time() - t0
    print(f"[吞吐] 处理 {N} 条耗时 {dt:.3f}s，约 {N / dt:.0f} 条/秒")

    matched = sum(v for k, v in stats.items() if k != "总计")
    assert matched == N, f"结果总数异常: {stats}"
    # 230105(太平区撤销)+道外区地址 -> 已解析；其余应正确/无匹配，不应出现“异常”
    assert stats.get("异常", 0) == 0, f"出现非预期异常: {stats}"
    print(f"[正确性] 状态统计: {stats}")

    # ---------- 结果可查询 + 分页计数（D2/P02） ----------
    assert db.count_results(batch_id="PERF") == N
    page1 = db.query_results(batch_id="PERF", page=1, page_size=50)
    assert len(page1) == 50, "分页每页条数异常"
    print(f"[分页] count_results={db.count_results(batch_id='PERF')}，"
          f"首页返回 {len(page1)} 条 OK")

    # ---------- P03 缓存失效验证 ----------
    db.insert_districts([{
        "id": "999000000000", "pids": "", "pid": None,
        "district_code": "999000000000", "district_name": "测试新区",
        "admin_level": 1, "province_code": "999000000000", "city_code": None,
    }])
    hit = db.get_by_district_code("999000000000", 1)
    assert hit is not None and hit["district_name"] == "测试新区", "缓存未失效"
    print("[P03] 插入新区划后缓存失效并可查到 OK")

    # ---------- P04 后台处理 + 进度接口（Flask test_client） ----------
    import app as app_module
    client = app_module.app.test_client()
    resp = client.post("/result", data={"action": "process",
                                        "batch_id": "PERF"})
    assert resp.status_code == 200, "触发后台处理失败"
    status = "running"
    for _ in range(300):
        p = client.get("/api/result/progress?batch_id=PERF").get_json()
        status = p.get("status")
        if status in ("done", "error"):
            break
        time.sleep(0.05)
    assert status == "done", f"后台任务未成功完成: {p}"
    assert db.count_results(batch_id="PERF") == N, "后台写入结果数异常"
    print(f"[P04] 后台任务完成，进度接口返回: {p['stats']} OK")

    print("\nALL PERF CHECKS PASSED")


if __name__ == "__main__":
    main()
