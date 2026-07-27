"""引擎②：地址文本解析 + 层级3（区县/县级市）反查。

设计原则（v2）：
- 地址是主信息源，行政层级3（区县/县级市）是主判定锚点。
- 当层级3为县级市（XX市）且地址省略了层级2的市时，必须确保反查成功，
  并据此推导父级市/省（用户明确要求）。
- 区划字典含层级4（乡镇/街道）与层级5（村/社区）数据，
  在层级3无法由名称直接命中时，可用乡镇/村反推上层区县。
"""
import re

from database.models import Engine2Result
from utils import address_parser
from utils import validators

# 用于从地址中扫描层级3（区县/县级市）候选：XX市/县/区/旗/自治县/新区
# 使用 lazy 量词，避免把"黑龙江省黑河市嫩江市"整体当作一个候选
_COUNTY_CANDIDATE_RE = re.compile(
    r"([\u4e00-\u9fa5]{1,10}?(?:自治县|市|县|区|旗|新区))"
)

# 常见"撤县设市/设区"历史名称映射：旧名 -> 当前标准名
# 用于兼容地址中仍使用旧县/市名的情况（字典仅存新名，旧名需翻译后反查）
_HISTORICAL_NAME_MAP = {
    "嫩江县": "嫩江市",
    "东宁县": "东宁市",
    "漠河县": "漠河市",
    "双城市": "双城区",
    "普兰店市": "普兰店区",
    "崇明县": "崇明区",
    "吴江市": "吴江区",
    "溧水县": "溧水区",
    "高淳县": "高淳区",
    "铜山县": "铜山区",
    "绍兴县": "柯桥区",
    "璧山县": "璧山区",
    "增城市": "增城区",
    "金坛市": "金坛区",
    "江都市": "江都区",
}


def _lookup_county(db, name, city_pid=None):
    """按名称查层级3，支持历史别名映射；返回 (row, 实际命中的标准名)。"""
    if not name:
        return None, name
    candidates = [name]
    alias = _HISTORICAL_NAME_MAP.get(name)
    if alias and alias not in candidates:
        candidates.append(alias)
    for try_name in candidates:
        row = None
        if city_pid:
            row = db.reverse_lookup(try_name, 3, pid=city_pid)
        if not row:
            row = db.reverse_lookup(try_name, 3)
        if row:
            return row, try_name
    return None, name


def _find_county_candidate(db, address, exclude, city_pid=None):
    """
    从地址中扫描可能的层级3名称并反查。
    exclude: 需要排除的名称集合（已识别的省/市）
    city_pid: 若已知所属市，优先在该市范围内反查
    返回 (命中行, 命中名) 或 (None, None)，与 _lookup_county 返回契约一致
    """
    seen = set()
    for m in _COUNTY_CANDIDATE_RE.finditer(address):
        name = m.group(1)
        if name in exclude or name in seen:
            continue
        seen.add(name)
        if name in ("市辖区",):
            continue
        # 优先在已知市范围内反查
        row = None
        if city_pid:
            row = db.reverse_lookup(name, 3, pid=city_pid)
        if not row:
            row = db.reverse_lookup(name, 3)
        if row:
            return row, name
    return None, None


def _apply_county(db, res, county_row, city_pid_holder):
    """
    用命中的层级3行填充 district，并向上推导 city/province。
    city_pid_holder: 单元素列表 [city_pid]，便于跨步骤共享。
    """
    res.district = county_row["district_name"]
    res.district_code = county_row["district_code"]
    parent = db.get_by_id(county_row["pid"]) if county_row.get("pid") else None
    if parent and parent["admin_level"] == 2:
        # 县级市/县的上层是地级市
        res.city = parent["district_name"]
        res.city_code = parent["district_code"]
        city_pid_holder[0] = parent["id"]
        if not res.province:
            pp = db.get_by_id(parent["pid"])
            if pp and pp["admin_level"] == 1:
                res.province = pp["district_name"]
                res.province_code = pp["district_code"]
    elif parent and parent["admin_level"] == 1:
        # 省直管县/直辖市：city 置为省/市名（与 parser/引擎①直辖市约定一致）
        res.city = parent["district_name"]
        res.city_code = parent["district_code"]
        res.province = parent["district_name"]
        res.province_code = parent["district_code"]
        if city_pid_holder is not None:
            city_pid_holder[0] = parent["id"]


def _lookup_by_ancestor(db, name, level, scope_level, scope_id):
    """
    在指定范围（scope_level=1 按省，2 按市）内按名称反查层级 level。
    乡镇/村的 pid 是其直接父级（区县/乡镇），并非省/市，
    故需沿父链向上核对归属，避免同名跨区误匹配。
    """
    if not name:
        return None
    cands = db.search_by_name(name, level) or []
    if not cands:
        return None
    if scope_level is None or scope_id is None:
        return cands[0]
    for row in cands:
        cur = row
        depth = 0
        while cur and depth < 6:
            if cur["admin_level"] == scope_level:
                if cur["id"] == scope_id:
                    return row
                break
            cur = db.get_by_id(cur["pid"]) if cur.get("pid") else None
            depth += 1
    return None


def _try_town_village_fallback(db, res, city_pid_holder):
    """
    层级3名称未命中时的兜底：用乡镇(层级4)/村(层级5)反推上层区县。
    优先在已识别的市(层级2)范围内反查，否则按省(层级1)；乡镇优先于村。
    """
    if res.district_code:
        return
    # 确定范围：优先已识别的市，否则省
    scope_level, scope_id = None, None
    if city_pid_holder and city_pid_holder[0]:
        scope_level, scope_id = 2, city_pid_holder[0]
    elif res.province:
        pr = db.reverse_lookup(res.province, 1)
        if pr:
            scope_level, scope_id = 1, pr["id"]
    # 层级4 乡镇/街道 -> 父级即层级3
    if res.township:
        t = _lookup_by_ancestor(db, res.township, 4, scope_level, scope_id)
        if t and t.get("pid"):
            county = db.get_by_id(t["pid"])
            if county and county["admin_level"] == 3:
                _apply_county(db, res, county, city_pid_holder)
                return
    # 层级5 村/社区 -> 父级层级4 -> 再父级层级3
    if not res.district_code and res.village:
        v = _lookup_by_ancestor(db, res.village, 5, scope_level, scope_id)
        if v and v.get("pid"):
            tw = db.get_by_id(v["pid"])
            if tw and tw.get("pid"):
                county = db.get_by_id(tw["pid"])
                if county and county["admin_level"] == 3:
                    _apply_county(db, res, county, city_pid_holder)


def engine2_parse(db, address, id_card=None):
    """
    输入：地址文本（及可选身份证号用于辅助限定）
    输出：Engine2Result（解析出的名称 + 反查得到的 12 位代码）
    """
    parsed = address_parser.parse_address(address)
    res = Engine2Result(
        province=parsed["province"],
        city=parsed["city"],
        district=parsed["district"],
        township=parsed.get("township"),
        village=parsed.get("village"),
    )
    city_pid = [None]  # 可变容器，跨步骤共享

    # 步骤1：解析市级（层级2）。若“市”实为县级市（省略了地级市），
    # 则作为层级3处理并推导父级市/省（用户强调项）。
    if res.city:
        city_row = db.reverse_lookup(res.city, 2)
        if city_row:
            res.city_code = city_row["district_code"]
            city_pid[0] = city_row["id"]
            if not res.province and city_row.get("pid"):
                prov_row = db.get_by_id(city_row["pid"])
                if prov_row:
                    res.province = prov_row["district_name"]
                    res.province_code = prov_row["district_code"]
        else:
            # 县级市（XX市）省略层级2：直接作为层级3，并推导父级
            county_row, county_name = _lookup_county(db, res.city)
            if county_row:
                _apply_county(db, res, county_row, city_pid)

    # 步骤2：由区县名反查层级3（优先在已知市范围内）
    if res.district and not res.district_code:
        county_row, county_name = _lookup_county(db, res.district, city_pid[0])
        if county_row:
            res.district = county_name
            res.district_code = county_row["district_code"]
            _apply_county(db, res, county_row, city_pid)

    # 步骤3：兜底扫描地址中的 XX市/县/区/旗 候选
    if not res.district_code:
        exclude = {x for x in (res.province, res.city) if x}
        county_row, county_name = _find_county_candidate(
            db, address, exclude, city_pid[0])
        if county_row:
            res.district = county_name
            res.district_code = county_row["district_code"]
            _apply_county(db, res, county_row, city_pid)

    # 步骤4：乡镇/村辅助反推层级3（字典含层级4/5数据）
    _try_town_village_fallback(db, res, city_pid)

    # 步骤5：补全省级代码
    if res.province and not res.province_code:
        row = db.reverse_lookup(res.province, 1)
        if row:
            res.province_code = row["district_code"]

    return res
