import unittest

from engine import engine2
from tests._fixture import build_fixture_db


class TestEngine2(unittest.TestCase):
    def setUp(self):
        self.db = build_fixture_db()

    def test_parse_address(self):
        parsed = engine2.engine2_parse(self.db, "哈尔滨市道外区水泥路99-4号")
        self.assertEqual(parsed.province, "黑龙江省")
        self.assertEqual(parsed.city, "哈尔滨市")
        self.assertEqual(parsed.district, "道外区")
        self.assertEqual(parsed.district_code, "230102000000")

    def test_parse_address_without_city(self):
        parsed = engine2.engine2_parse(
            self.db, "黑龙江省依兰县三道岗镇东河村联合屯"
        )
        self.assertEqual(parsed.province, "黑龙江省")
        # 引擎②会从区县父链推导出市（依兰县父级为哈尔滨市）
        self.assertEqual(parsed.city, "哈尔滨市")
        self.assertEqual(parsed.district, "依兰县")
        self.assertEqual(parsed.district_code, "230123000000")

    def test_parse_address_municipality(self):
        parsed = engine2.engine2_parse(self.db, "北京市海淀区中关村大街1号")
        self.assertEqual(parsed.province, "北京市")
        self.assertEqual(parsed.city, "北京市")
        self.assertEqual(parsed.district, "海淀区")

    def test_reverse_lookup_scoped(self):
        # 在哈尔滨市范围内反查“道外区”
        row = self.db.reverse_lookup("道外区", 3, pid="230100000000")
        self.assertIsNotNone(row)
        self.assertEqual(row["district_code"], "230102000000")


if __name__ == "__main__":
    unittest.main()
