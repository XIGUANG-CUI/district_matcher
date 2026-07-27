import unittest

from engine import engine1, engine2, decider
from tests._fixture import build_fixture_db


class TestDecider(unittest.TestCase):
    def setUp(self):
        self.db = build_fixture_db()

    def _run(self, id_card, address):
        e1 = engine1.engine1_match(self.db, id_card)
        e2 = engine2.engine2_parse(self.db, address, id_card)
        return decider.dual_engine_decide(self.db, e1, e2)

    def test_case1_correct(self):
        # 引擎①区县命中 + 引擎②一致
        r = self._run("23010219890714291X", "哈尔滨市道外区水泥路99-4号")
        self.assertEqual(r.match_status, "正确")
        self.assertEqual(r.district_code, "230102000000")
        self.assertEqual(r.district_name, "道外区")

    def test_case4_history_resolved(self):
        # 230105（太平区已撤销）+ 地址道外区 -> 引擎②解析层级3，引擎①省/市一致 -> 正确
        r = self._run("23010519890714291X", "哈尔滨市道外区水泥路99-4号")
        self.assertEqual(r.match_status, "正确")
        self.assertEqual(r.province_name, "黑龙江省")
        self.assertEqual(r.city_name, "哈尔滨市")
        self.assertEqual(r.district_name, "道外区")
        self.assertEqual(r.district_code, "230102000000")

    def test_province_then_county_keeps_final_district_code(self):
        r = self._run(
            "230123201004231812",
            "黑龙江省依兰县三道岗镇东河村联合屯",
        )
        self.assertEqual(r.district_name, "依兰县")
        self.assertEqual(r.district_code, "230123000000")
        self.assertEqual(r.match_status, "正确")

    def test_engine2_unresolved_name_does_not_clear_engine1(self):
        r = self._run("230123201004231812", "黑龙江省不存在县某村")
        self.assertEqual(r.district_name, "依兰县")
        self.assertEqual(r.district_code, "230123000000")
        self.assertEqual(r.match_status, "已解析")

    def test_case_municipality_correct(self):
        r = self._run("110108199003074567", "北京市海淀区中关村大街1号")
        self.assertEqual(r.match_status, "正确")
        self.assertEqual(r.city_name, "北京市")
        self.assertEqual(r.district_name, "海淀区")

    def test_case5_nomatch(self):
        # 身份证无法匹配且无地址
        r = self._run("65010019890714291X", "")
        self.assertIn(r.match_status, ("无匹配",))


if __name__ == "__main__":
    unittest.main()
