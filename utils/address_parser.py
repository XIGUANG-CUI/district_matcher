"""地址文本解析工具：从完整地址中提取省/市/区县名称。"""
import re

import config

MUNICIPAL_NAMES = list(config.MUNICIPALITIES.values())  # 北京市 等

# 自治区（及常见简称）→ 全称映射：地址中常省略“回族自治区”等后缀
_PROVINCE_SHORT_MAP = {
    "内蒙古": "内蒙古自治区",
    "广西": "广西壮族自治区",
    "西藏": "西藏自治区",
    "宁夏": "宁夏回族自治区",
    "新疆": "新疆维吾尔自治区",
}


def _expand_province_short(text):
    """
    将地址开头的自治区简称扩展为全称，使后续正则能正确识别并剥离省前缀。
    例如："宁夏彭阳县..." → "宁夏回族自治区彭阳县..."
    """
    for short, full in _PROVINCE_SHORT_MAP.items():
        if text.startswith(full):
            return text
        if text.startswith(short):
            # 避免把“广西省”这类旧称展开成“广西壮族自治区省”
            if text.startswith(short + "省"):
                return text
            return full + text[len(short):]
    return text

PROVINCE_RE = re.compile(r"([\u4e00-\u9fa5]{1,10}?(?:省|自治区))")
MUNICIPAL_RE = re.compile(r"(北京市|天津市|上海市|重庆市)")
CITY_RE = re.compile(r"([\u4e00-\u9fa5]{1,10}?(?:市|自治州|地区|盟))")
# 修复：避免把“社区”中的“区”误当作行政区划后缀
DISTRICT_RE = re.compile(r"([\u4e00-\u9fa5]{1,12}?(?:自治县|(?<!社)区|县|旗|新区))")
# 层级4（乡镇/街道）候选：用于辅助反推上层区县
TOWN_RE = re.compile(
    r"([\u4e00-\u9fa5]{1,12}?(?:街道办事处|街道|民族乡|乡|镇|民族苏木|苏木))"
)
# 层级5（村/社区）候选：作为最后兜底反推
VILLAGE_RE = re.compile(
    r"([\u4e00-\u9fa5]{1,12}?(?:社区居民委员会|村民委员会|社区|居委会|村|嘎查))"
)


def parse_address(address):
    """
    从地址文本提取三级行政区划名称。
    返回 {"province": str|None, "city": str|None, "district": str|None}
    仅做名称提取，不保证与数据库匹配（由引擎②反查负责）。
    """
    if not address or not address.strip():
        return {"province": None, "city": None, "district": None,
                "township": None, "village": None}

    text = address.strip()
    text = _expand_province_short(text)
    result = {"province": None, "city": None, "district": None,
              "township": None, "village": None}

    # 1) 省级
    mun = MUNICIPAL_RE.search(text)
    if mun:
        result["province"] = mun.group(1)
    else:
        prov = PROVINCE_RE.search(text)
        if prov:
            result["province"] = prov.group(1)

    # 2) 市级
    if result["province"] in MUNICIPAL_NAMES:
        # 直辖市：市级即省级本身
        result["city"] = result["province"]
    else:
        # 修复：先剔除省名前缀再匹配市，避免"黑龙江省嫩江市"整体被当作市名
        search_text = text
        if result["province"] and result["province"] in text:
            idx = text.find(result["province"])
            search_text = text[idx + len(result["province"]):]
        for m in CITY_RE.finditer(search_text):
            name = m.group(1)
            if name == result["province"]:
                continue
            # 跳过“市辖区”虚拟名
            if name == "市辖区":
                continue
            result["city"] = name
            break

    # 3) 计算剩余文本：剔除已识别的省/市前缀，供抽取区县/乡镇/村
    if result["province"] in MUNICIPAL_NAMES:
        prefix = result["province"]
        rest = text[text.find(prefix) + len(prefix):] if prefix in text else text
    elif result["city"]:
        rest = text[text.find(result["city"]) + len(result["city"]):] if result["city"] in text else text
    elif result["province"]:
        # 地址可能省后直接跟区县（如“黑龙江省依兰县…”）。
        # 若不剔除省名前缀，区县正则会误取“黑龙江省依兰县”。
        prefix = result["province"]
        rest = text[text.find(prefix) + len(prefix):] if prefix in text else text
    else:
        rest = text

    # 3.1) 区县级
    district_m = None
    for m in DISTRICT_RE.finditer(rest):
        name = m.group(1)
        if result["city"] and name == result["city"]:
            continue
        result["district"] = name
        district_m = m
        break

    # 3.2) 乡镇/村（层级4/5）候选：在剔除省/市/区县后的剩余文本中抽取，
    #      避免长区县名（如“莫力达瓦达斡尔族自治旗”）被乡镇/村正则钻取
    rest_for_town_village = rest[district_m.end():] if district_m else rest

    tm = TOWN_RE.search(rest_for_town_village)
    rest_for_village = rest_for_town_village[tm.end():] if tm else rest_for_town_village
    if tm:
        result["township"] = tm.group(1)

    vm = VILLAGE_RE.search(rest_for_village)
    if vm:
        result["village"] = vm.group(1)

    return result
