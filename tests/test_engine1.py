import unittest

from engine import engine1
from tests._fixture import build_fixture_db


class TestEngine1(unittest.TestCase):
    def setUp(self):
        self.db = build_fixture_db()

    def test_build_codes(self):
        pc, cc, dc = engine1.build_codes("230105")
        self.assertEqual(pc, "230000000000")
        self.assertEqual(cc, "230100000000")
        self.assertEqual(dc, "230105000000")

    def test_engine1_correct(self):
        # 230102 -> 道外区 在库中，区县匹配成功
        res = engine1.engine1_match(self.db, "23010219890714291X")
        self.assertTrue(res.province.matched)
        self.assertEqual(res.province.name, "黑龙江省")
        self.assertTrue(res.city.matched)
        self.assertEqual(res.city.name, "哈尔滨市")
        self.assertTrue(res.district.matched)
        self.assertEqual(res.district.name, "道外区")

    def test_engine1_district_fail(self):
        # 230105（太平区已撤销）区县未命中，但省市命中
        res = engine1.engine1_match(self.db, "23010519890714291X")
        self.assertTrue(res.province.matched)
        self.assertTrue(res.city.matched)
        self.assertFalse(res.district.matched)

    def test_engine1_municipality(self):
        # 北京：市级即省级
        res = engine1.engine1_match(self.db, "110108199003074567")
        self.assertTrue(res.province.matched)
        self.assertEqual(res.province.name, "北京市")
        self.assertTrue(res.city.matched)
        self.assertEqual(res.city.name, "北京市")
        self.assertTrue(res.district.matched)
        self.assertEqual(res.district.name, "海淀区")

    def test_engine1_invalid(self):
        res = engine1.engine1_match(self.db, "ABCDE119890714291X")
        self.assertIsNotNone(res.error)


if __name__ == "__main__":
    unittest.main()
