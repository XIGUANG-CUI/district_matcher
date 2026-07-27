"""匹配服务：执行双引擎匹配并写入结果表。"""
from engine import engine1, engine2, decider
from utils import validators
from database.models import MatchResult


def _build_engine1_detail(e1):
    if getattr(e1, "error", None):
        return f"异常：{e1.error}"
    lines = []
    lines.append(f"省级：{e1.province.name or '未命中'}（{e1.province.code or '-'}）"
                 f"{'✅' if e1.province.matched else '❌'}")
    lines.append(f"市级：{e1.city.name or '未命中'}（{e1.city.code or '-'}）"
                 f"{'✅' if e1.city.matched else '❌'}")
    lines.append(f"区县：{e1.district.name or '未命中'}（{e1.district.code or '-'}）"
                 f"{'✅' if e1.district.matched else '❌'}")
    return "\n".join(lines)


def _build_engine2_detail(e2):
    if not e2:
        return "未执行"
    lines = []
    lines.append(f"解析省级：{e2.province or '未解析'}")
    lines.append(f"解析市级：{e2.city or '未解析'}")
    lines.append(f"解析区县：{e2.district or '未解析'}")
    lines.append(f"反查区县代码：{e2.district_code or '未命中'}")
    return "\n".join(lines)


def run_match(db, id_card, address, with_detail=False):
    """对单条数据执行双引擎匹配，返回 MatchResult。

    with_detail=True 时额外构建 engine1/engine2 过程文本（供存库与页面“详情”
    展示）；单条 API/SDK 路径无需展示过程，传 False（默认）可省去构建开销（P12）。
    """
    # 边界情况处理：身份证格式异常直接标记“异常”，跳过引擎匹配（D6 接入校验）
    if not validators.is_id_card_format(id_card):
        return MatchResult(
            match_status="异常",
            engine1_detail="未执行",
            engine2_detail="未执行",
            decision_path="身份证格式异常（非18位或结构非法），跳过引擎匹配",
        )
    e1 = engine1.engine1_match(db, id_card)
    e2 = engine2.engine2_parse(db, address or "", id_card)
    result = decider.dual_engine_decide(db, e1, e2)
    if with_detail:
        result.engine1_detail = _build_engine1_detail(e1)
        result.engine2_detail = _build_engine2_detail(e2)
    return result


def process_batch(db, batch_id, on_progress=None):
    """对指定批次全部学生执行双引擎匹配，覆盖旧结果并返回统计。

    on_progress(done, total, stats) 为可选进度回调（P04 后台任务用）。
    """
    students = db.get_students_by_batch(batch_id)
    # 同一批次重新执行时覆盖旧结果，避免页面和导出混入修复前的历史记录。
    db.delete_results_by_batch(batch_id)
    total = len(students)
    stats = {"正确": 0, "已解析": 0, "已解析(仅证件)": 0, "需复核": 0,
              "部分": 0, "无匹配": 0, "异常": 0, "总计": total}
    rows = []
    for i, stu in enumerate(students):
        # P12：批量处理需存详情供页面展示，传 with_detail=True
        result = run_match(db, stu["id_card"], stu.get("address"), with_detail=True)
        rows.append({
            # P10：t_result 自包含 batch_id，不再依赖 student_id 关联
            "batch_id": stu["batch_id"],
            "student_name": stu.get("student_name"),
            "id_card": stu.get("id_card"),
            "address": stu.get("address"),
            "province_code": result.province_code,
            "province_name": result.province_name,
            "city_code": result.city_code,
            "city_name": result.city_name,
            "district_code": result.district_code,
            "district_name": result.district_name,
            "match_status": result.match_status,
            "engine1_result": result.engine1_detail,
            "engine2_result": result.engine2_detail,
            "remark": result.decision_path,
        })
        stats[result.match_status] = stats.get(result.match_status, 0) + 1
        # 每 200 条上报一次进度，避免频繁回调影响吞吐
        if on_progress and (i + 1) % 200 == 0:
            on_progress(i + 1, total, dict(stats))
    # P02：批量写入，单次事务提交（替代逐条 insert + commit）
    db.insert_results_batch(rows)
    if on_progress:
        on_progress(total, total, dict(stats))
    return stats
