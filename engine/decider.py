"""双引擎决策器 v2：引擎②（地址）为主，引擎①（身份证）校验/兜底。

设计原则：
- 行政层级3（区县/县级市）是主判定锚点。引擎②解析出层级3即采用其全量结果。
- 引擎①仅做一致性校验（一致→正确，冲突→需复核）与兜底补充
  （地址为空或引擎②未解析层级3时，由引擎①补充/兜底区县）。
- 归属永远先问引擎②，从架构上杜绝跨引擎省市区县拼接。
"""
from database.models import MatchResult


def dual_engine_decide(db, engine1_result, engine2_result):
    """
    决策逻辑（v2，引擎②为主）：
    A  e2 层级3命中           -> 采用引擎②全量；引擎①校验（一致=正确，冲突=需复核）
    B  e2 仅省/市 + e1区县    -> 地址省市 + 证件区县（已解析）
    C  e2 抽出区县名无代码     -> 需复核
    D  e2 无输出              -> 纯引擎①兜底（已解析(仅证件)/部分/无匹配/异常）
    """
    res = MatchResult()
    e1 = engine1_result
    e2 = engine2_result
    e1_err = getattr(e1, "error", None)
    e2_l3 = bool(e2 and e2.district_code)

    # 兜底名称（展示用）
    res.province_name = (e2.province if e2 else None) or e1.province.name
    res.city_name = (e2.city if e2 else None) or e1.city.name
    res.district_name = (e2.district if e2 else None) or e1.district.name

    def _fill_from_e1():
        res.province_name = e1.province.name
        res.city_name = e1.city.name
        res.district_name = e1.district.name
        res.province_code = e1.province.code
        res.city_code = e1.city.code
        res.district_code = e1.district.code

    if e2_l3:
        # 主引擎②：采用其全量（省/市/区县）
        res.province_name = e2.province
        res.city_name = e2.city
        res.district_name = e2.district
        res.province_code = e2.province_code
        res.city_code = e2.city_code
        res.district_code = e2.district_code
        if e1_err:
            res.match_status = "已解析"
            res.decision_path = "引擎②解析层级3成功（身份证异常，以地址为准）"
        else:
            # 以名称一致性校验（比代码前缀更鲁棒，天然兼容直辖市市代码表示差异）
            prov_ok = (e1.province.matched and e2.province
                       and e1.province.name == e2.province)
            city_ok = (e1.city.matched and e2.city
                       and e1.city.name == e2.city)
            prov_conflict = (e1.province.matched and e2.province
                             and e1.province.name != e2.province)
            city_conflict = (e1.city.matched and e2.city
                             and e1.city.name != e2.city)
            if prov_conflict or city_conflict:
                res.match_status = "需复核"
                res.decision_path = (
                    f"引擎②解析层级3，但引擎①证件归属"
                    f"(省={e1.province.name or '-'}/市={e1.city.name or '-'})"
                    f"与地址(省={e2.province or '-'}/市={e2.city or '-'})不一致，"
                    f"以地址为准，标记需复核"
                )
            elif prov_ok and city_ok:
                res.match_status = "正确"
                res.decision_path = "引擎②解析层级3，引擎①省/市一致（双校验通过）"
            else:
                res.match_status = "已解析"
                res.decision_path = "引擎②解析层级3成功（引擎①未完整校验，以地址为准）"

    elif e2 and (e2.province or e2.city) and not e2.district_code:
        # 引擎②仅命中省/市，未解析层级3 -> 引擎①补充层级3
        res.province_name = e2.province or e1.province.name
        res.city_name = e2.city or e1.city.name
        res.province_code = e2.province_code or (e1.province.code if e1.province.matched else None)
        res.city_code = e2.city_code or (e1.city.code if e1.city.matched else None)
        if e1.district.matched:
            res.district_name = e1.district.name
            res.district_code = e1.district.code
            res.match_status = "已解析"
            res.decision_path = "引擎②解析省/市，引擎①补充层级3（地址省市+证件区县）"
        else:
            res.match_status = "需复核"
            res.decision_path = "引擎②仅解析省/市，引擎①亦无层级3，标记需复核"

    elif e2 and e2.district and not e2.district_code:
        # 引擎②抽出区县名但未反查到代码（极端情况）
        res.match_status = "需复核"
        res.decision_path = "引擎②抽出区县名但未反查到代码，标记需复核"

    else:
        # 引擎②无有效输出（地址空/无法解析）-> 纯引擎①兜底
        if e1_err:
            res.match_status = "异常"
            res.engine1_detail = f"引擎①异常：{e1_err}"
            _fill_from_e1()
        elif e1.district.matched:
            res.match_status = "已解析(仅证件)"
            _fill_from_e1()
            res.decision_path = "地址为空/无法解析，纯引擎①（身份证）兜底"
        elif e1.province.matched or e1.city.matched:
            res.match_status = "部分"
            _fill_from_e1()
            res.decision_path = "引擎①仅解析省/市，无层级3（部分）"
        else:
            res.match_status = "无匹配"
            _fill_from_e1()
            res.decision_path = "引擎①与引擎②均未能确定层级3（无匹配）"

    return res
