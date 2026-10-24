# -*- coding: utf-8 -*-
"""纯算法层单测：不依赖 Flask / models / 数据库（FakeRepo）。

对应 PORTING_GUIDE 12.2 的算法类用例（#1–#13）与若干边界。
运行：
    python -m pytest tests/test_district_match_unit.py -q
"""
import pytest

from services.district_match import address_parser, engine1, engine2, runner
from services.district_match import constants
from services.district_match.decider import dual_engine_decide
from services.district_match.models import Engine1Result


def _row(code, name, level, parent=""):
    return {
        "district_code": code, "district_name": name, "admin_level": level,
        "parent_code": parent or None, "province_code": None, "city_code": None,
        "id": code, "pid": parent or None,
    }


FIXTURE_ROWS = [
    _row("110000000000", "北京市", 1),
    _row("110100000000", "市辖区", 2, "110000000000"),
    _row("110101000000", "东城区", 3, "110100000000"),
    _row("110108000000", "海淀区", 3, "110100000000"),
    _row("110101001000", "东华门街道", 4, "110101000000"),
    _row("120000000000", "天津市", 1),
    _row("150000000000", "内蒙古自治区", 1),
    _row("150700000000", "呼伦贝尔市", 2, "150000000000"),
    _row("150722000000", "莫力达瓦达斡尔族自治旗", 3, "150700000000"),
    _row("150722001000", "红彦镇", 4, "150722000000"),
    _row("150722001001", "新多村", 5, "150722001000"),
    _row("230000000000", "黑龙江省", 1),
    _row("230100000000", "哈尔滨市", 2, "230000000000"),
    _row("230104000000", "道外区", 3, "230100000000"),
    _row("230109000000", "松北区", 3, "230100000000"),
    _row("230104001000", "水泥路街道", 4, "230104000000"),
    _row("230104001001", "水泥路社区", 5, "230104001000"),
    _row("231100000000", "黑河市", 2, "230000000000"),
    _row("231183000000", "嫩江市", 3, "231100000000"),
    _row("232700000000", "大兴安岭地区", 2, "230000000000"),
    _row("232701000000", "漠河市", 3, "232700000000"),
    _row("310000000000", "上海市", 1),
    _row("310100000000", "市辖区", 2, "310000000000"),
    _row("310151000000", "崇明区", 3, "310100000000"),
    _row("500000000000", "重庆市", 1),
    _row("500100000000", "市辖区", 2, "500000000000"),
    _row("500120000000", "璧山区", 3, "500100000000"),
    _row("640000000000", "宁夏回族自治区", 1),
    _row("640400000000", "固原市", 2, "640000000000"),
    _row("640425000000", "彭阳县", 3, "640400000000"),
]

HISTORY_ALIAS = {"太平区": "道外区"}
HISTORY_CODE = {"230105": "230104"}


class FakeRepo:
    """只实现引擎依赖的 6 个方法。"""

    def __init__(self, rows=None):
        self.rows = rows if rows is not None else FIXTURE_ROWS
        self.by_code = {r["district_code"]: r for r in self.rows}
        self.by_name = {}
        for r in self.rows:
            self.by_name.setdefault((r["district_name"], r["admin_level"]), []).append(r)

    def get_by_district_code(self, code, level=None):
        row = self.by_code.get(code)
        if row is None:
            return None
        if level is not None and row["admin_level"] != level:
            return None
        return row

    def get_by_id(self, rid):
        if rid is None:
            return None
        return self.by_code.get(str(rid))

    def reverse_lookup(self, name, level, pid=None):
        items = self.by_name.get((name, level)) or []
        if pid:
            for item in items:
                if item.get("parent_code") == str(pid):
                    return item
            return None
        return items[0] if items else None

    def search_by_name(self, name, level=None):
        if level is None:
            return [r for r in self.rows if r["district_name"] == name]
        return list(self.by_name.get((name, level)) or [])

    def get_history_alias_map(self):
        return dict(HISTORY_ALIAS)

    def get_history_code_map(self):
        return dict(HISTORY_CODE)


@pytest.fixture()
def repo():
    return FakeRepo()


def _match(repo, id_card, address):
    return runner.run_match(repo, id_card, address, with_detail=True)


# ---------------------------------------------------------------- 12.2 #1–#13
def test_case01_history_code_plus_address(repo):
    r = _match(repo, "230105199001010011",
               "黑龙江省哈尔滨市道外区水泥路99-4号")
    assert r.match_status == constants.STATUS_CORRECT
    assert (r.province_name, r.city_name, r.district_name) == ("黑龙江省", "哈尔滨市", "道外区")
    assert r.district_code == "230104000000"
    assert "历史映射" in r.engine1_detail


def test_case02_address_without_province(repo):
    r = _match(repo, "230105199001010011", "哈尔滨市道外区水泥路99-4号")
    assert r.match_status == constants.STATUS_CORRECT
    assert r.district_code == "230104000000"
    assert r.province_name == "黑龙江省"


def test_case03_id_only(repo):
    r = _match(repo, "230105", "")
    assert r.match_status == constants.STATUS_PARSED_ID_ONLY
    assert r.district_code == "230104000000"
    assert r.district_name == "道外区"


def test_case04_address_only(repo):
    r = _match(repo, "", "黑龙江省哈尔滨市道外区水泥路99-4号")
    assert r.match_status == constants.STATUS_PARSED
    assert r.district_code == "230104000000"


def test_case05_municipality(repo):
    r = _match(repo, "", "北京市海淀区中关村大街1号")
    assert r.match_status == constants.STATUS_PARSED
    assert r.district_code == "110108000000"
    assert r.city_name == "北京市"
    assert r.city_code == "110000000000"


def test_case06_invalid_id_only(repo):
    r = _match(repo, "abc123", "")
    assert r.match_status == constants.STATUS_ERROR


def test_case07_invalid_id_valid_address(repo):
    r = _match(repo, "abc123", "黑龙江省哈尔滨市道外区水泥路99-4号")
    assert r.match_status == constants.STATUS_PARSED
    assert r.district_code == "230104000000"


def test_case08_both_empty(repo):
    r = _match(repo, "", "")
    assert r.match_status == constants.STATUS_ERROR
    assert "未输入" in r.decision_path


def test_case09_conflict_needs_review(repo):
    r = _match(repo, "150722199001010011", "黑龙江省嫩江市中央街1号")
    assert r.match_status == constants.STATUS_REVIEW
    assert (r.province_name, r.city_name, r.district_name) == ("黑龙江省", "黑河市", "嫩江市")
    assert r.district_code == "231183000000"


def test_case10_autonomous_region_short_name(repo):
    r = _match(repo, "640425199001010011", "宁夏彭阳县白阳镇1号")
    assert r.match_status == constants.STATUS_CORRECT
    assert r.district_code == "640425000000"
    assert r.province_name == "宁夏回族自治区"


def test_case11_county_level_city(repo):
    r = _match(repo, "", "黑龙江省黑河市嫩江市中央街1号")
    assert r.city_name == "黑河市"
    assert r.district_name == "嫩江市"
    assert r.district_code == "231183000000"


def test_case12_community_not_county(repo):
    parsed = address_parser.parse_address("黑龙江省哈尔滨市道外区水泥路社区1号")
    assert parsed["district"] == "道外区"
    r = _match(repo, "", "黑龙江省哈尔滨市道外区水泥路社区1号")
    assert r.district_code == "230104000000"


def test_case13_township_village(repo):
    address = "莫力达瓦达斡尔族自治旗红彦镇新多村"
    parsed = address_parser.parse_address(address)
    assert parsed["district"] == "莫力达瓦达斡尔族自治旗"
    assert parsed["township"] == "红彦镇"
    assert parsed["village"] == "新多村"
    r = _match(repo, "", address)
    assert r.district_code == "150722000000"
    assert r.city_name == "呼伦贝尔市"


# ---------------------------------------------------------------- 其他边界
def test_township_fallback_to_county():
    """区县名缺失时，用乡镇反推区县（层级4兜底）。"""
    rows = list(FIXTURE_ROWS)
    repo = FakeRepo(rows)
    r = _match(repo, "", "黑龙江省哈尔滨市水泥路街道1号")
    # 地址中没有区县名，乡镇“水泥路街道”在哈尔滨市范围内反推 230104
    assert r.district_code == "230104000000"


def test_village_fallback_to_county(repo):
    r = _match(repo, "", "黑龙江省哈尔滨市新多村")
    # 新多村属内蒙古，黑龙江省范围内找不到 -> 不应跨省误配
    assert r.district_code in (None, "")


def test_municipality_virtual_shixiaqu(repo):
    r = _match(repo, "110108199001010011", "")
    assert r.match_status == constants.STATUS_PARSED_ID_ONLY
    assert r.district_code == "110108000000"
    assert r.city_name == "北京市"


def test_history_alias_priority():
    """同名历史区划优先按别名命中，避免误配到外地同名区。"""
    rows = list(FIXTURE_ROWS) + [
        _row("210911000000", "太平区", 3, "210900000000"),
        _row("210900000000", "阜新市", 2, "210000000000"),
        _row("210000000000", "辽宁省", 1),
    ]
    repo = FakeRepo(rows)
    r = _match(repo, "", "哈尔滨市太平区1号")
    assert r.district_code == "230104000000"


def test_id_card_checksum_not_enforced(repo):
    """只取前 6 位，不做校验位强校验。"""
    r = _match(repo, "230104000000000000", "")
    assert r.district_code == "230104000000"


def test_engine1_invalid_first6(repo):
    e1 = engine1.engine1_match(repo, "abc")
    assert e1.error
    assert isinstance(e1, Engine1Result)


def test_engine2_no_address(repo):
    assert engine2.engine2_parse(repo, "", None) is not None
    r = _match(repo, "", "完全无法识别的文本")
    assert r.match_status in (constants.STATUS_ERROR, constants.STATUS_NOMATCH,
                              constants.STATUS_PARTIAL)


def test_decider_with_empty_engines():
    # 两引擎皆空时由 runner 提前判定「异常」；decider 层对应引擎① error 分支
    res = dual_engine_decide(
        FakeRepo(), Engine1Result(error="未输入有效的身份证/区划代码或地址"), None)
    assert res.match_status == constants.STATUS_ERROR


def test_all_status_values():
    assert set(constants.ALL_STATUSES) == {
        "正确", "已解析", "已解析(仅证件)", "需复核", "部分", "无匹配", "异常"}
