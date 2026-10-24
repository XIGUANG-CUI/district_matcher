"""匹配执行器：串起引擎①/引擎②/决策器，输出 MatchResult 与结果行。

本模块仍属纯算法层：不 import flask / models / config，
字典与结果表访问全部通过外部注入的 repo（鸭子类型）。
"""
from . import engine1, engine2, decider
from . import validators
from .models import Engine1Result
from .constants import ALL_STATUSES


def _build_engine1_detail(e1):
    """构建引擎①过程文本（页面“详情”展示用）。"""
    if getattr(e1, "error", None):
        return f"异常：{e1.error}"
    lines = []
    lines.append(f"省级：{e1.province.name or '未命中'}（{e1.province.code or '-'}）"
                 f"{'✅' if e1.province.matched else '❌'}")
    lines.append(f"市级：{e1.city.name or '未命中'}（{e1.city.code or '-'}）"
                 f"{'✅' if e1.city.matched else '❌'}")
    dist_note = "（历史映射）" if getattr(e1.district, "mapped", False) else ""
    lines.append(f"区县：{e1.district.name or '未命中'}（{e1.district.code or '-'}）"
                 f"{'✅' if e1.district.matched else '❌'}{dist_note}")
    return "\n".join(lines)


def _build_engine2_detail(e2):
    """构建引擎②过程文本（页面“详情”展示用）。"""
    if not e2:
        return "未执行"
    lines = []
    lines.append(f"解析省级：{e2.province or '未解析'}")
    lines.append(f"解析市级：{e2.city or '未解析'}")
    lines.append(f"解析区县：{e2.district or '未解析'}")
    lines.append(f"反查区县代码：{e2.district_code or '未命中'}")
    return "\n".join(lines)


def run_match(repo, id_card, address, with_detail=False):
    """单条双引擎匹配。

    业务约定：
    - 证件框可输入完整 18 位身份证 或 6 位区划代码，取前 6 位启动引擎①；
    - 地址框输入地址即启动引擎②；两框不必都填；
    - 不做 18 位/校验位强校验（仅以前 6 位作查询入口）；
    - 证件无效（前 6 位非数字）且地址为空 -> 标“异常”。
    """
    id_card = (id_card or "").strip()
    address = (address or "").strip()
    first6 = validators.get_id_first6(id_card)
    if not first6 and not address:
        from .models import MatchResult
        return MatchResult(
            match_status="异常",
            engine1_detail="未执行",
            engine2_detail="未执行",
            decision_path="未输入有效的身份证/区划代码或地址",
        )
    e1 = engine1.engine1_match(repo, id_card) if first6 else None
    e2 = engine2.engine2_parse(repo, address, id_card) if address else None
    result = decider.dual_engine_decide(repo, e1 or Engine1Result(), e2)
    if with_detail:
        result.engine1_detail = (_build_engine1_detail(e1) if e1
                                 else "未执行（未输入有效的身份证/区划代码）")
        result.engine2_detail = (_build_engine2_detail(e2) if e2
                                 else "未执行（未输入地址）")
    return result


def to_district_fields(result):
    """MatchResult -> students 的 7 个区划列（字段名锁定，不抛异常）。"""
    return {
        "district_province_name": result.province_name or "",
        "district_province_code": result.province_code or "",
        "district_city_name": result.city_name or "",
        "district_city_code": result.city_code or "",
        "district_county_name": result.district_name or "",
        "district_county_code": result.district_code or "",
        "district_match_status": result.match_status or "",
    }


def to_result_row(student, result):
    """MatchResult -> dm_match_result 行（对齐 6.2 DDL）。

    student: 需含 student_id / student_name / id_card / address（可为 None）
    """
    return {
        "student_id": student.get("student_id"),
        "student_name": student.get("student_name"),
        "id_card": student.get("id_card"),
        "address": student.get("address"),
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
    }


def empty_stats(total=0):
    stats = {s: 0 for s in ALL_STATUSES}
    stats["总计"] = total
    return stats


def process_batch(repo, students, batch_id, on_progress=None, progress_interval=200,
                  source="batch"):
    """批量匹配并写入 dm_match_result。

    students: 可迭代的学生 dict（student_id/student_name/id_card/address）
    同一批次重跑会先清空旧结果，避免页面/导出混入旧记录。
    """
    students = list(students)
    repo.delete_results_by_batch(batch_id)
    total = len(students)
    stats = empty_stats(total)
    rows = []
    for i, stu in enumerate(students):
        result = run_match(repo, stu.get("id_card"), stu.get("address"),
                           with_detail=True)
        row = to_result_row(stu, result)
        row["batch_id"] = batch_id
        row["source"] = source
        rows.append(row)
        stats[result.match_status] = stats.get(result.match_status, 0) + 1
        if on_progress and (i + 1) % progress_interval == 0:
            on_progress(i + 1, total, dict(stats))
    repo.insert_results_batch(rows)
    if on_progress:
        on_progress(total, total, dict(stats))
    return stats
