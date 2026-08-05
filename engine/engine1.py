"""引擎①：身份证前 6 位层级匹配行政区划。"""
import config
from database.models import Engine1Result, LevelResult
from utils import validators


def build_codes(first6):
    """由身份证前 6 位构造三级 12 位匹配代码。"""
    province_code = first6[:2] + config.PROVINCE_PAD       # AB + 10*0
    city_code = first6[:4] + config.CITY_PAD               # ABCD + 8*0
    district_code = first6 + config.DISTRICT_PAD           # ABCDEF + 6*0
    return province_code, city_code, district_code


def engine1_match(db, id_card):
    """
    输入：身份证号
    输出：Engine1Result（省/市/区县三级匹配结果）
    """
    first6 = validators.get_id_first6(id_card)
    if first6 is None:
        return Engine1Result(error="身份证前6位非数字或长度不足")

    province_code, city_code, district_code = build_codes(first6)
    res = Engine1Result()

    prov = db.get_by_district_code(province_code, 1)
    if prov:
        res.province = LevelResult(code=prov["district_code"],
                                   name=prov["district_name"], matched=True)

    if first6[:2] in config.MUNICIPALITIES and res.province.matched:
        # 直辖市：市级即省级本身（真实数据中直辖市存在 level-2 的“市辖区”虚拟节点，跳过）
        res.city = LevelResult(code=res.province.code,
                               name=res.province.name, matched=True)
    else:
        city = db.get_by_district_code(city_code, 2)
        if city:
            res.city = LevelResult(code=city["district_code"],
                                   name=city["district_name"], matched=True)

    dist = db.get_by_district_code(district_code, 3)
    if dist and dist["district_name"] != "市辖区":
        res.district = LevelResult(code=dist["district_code"],
                                   name=dist["district_name"], matched=True)
    else:
        # 历史代码映射直查：身份证签发地代码已撤销（如 230105 太平区）时，
        # 经 t_history_mapping 旧码->新码 翻译后按现行代码命中（引擎①兜底增强，
        # 不影响决策器 v2 的“引擎②为主锚点”原则）。
        code_map = db.get_history_code_map() or {}
        new6 = code_map.get(first6)
        if new6:
            mapped = db.get_by_district_code(new6 + config.DISTRICT_PAD, 3)
            if mapped and mapped["district_name"] != "市辖区":
                res.district = LevelResult(code=mapped["district_code"],
                                           name=mapped["district_name"],
                                           matched=True, mapped=True)

    return res
