"""单条查询业务流（run_match 输入组合）单元测试。

业务约定（按真实使用调整）：
- 证件框可输入 18 位身份证 或 6 位区划代码，取前 6 位启动引擎①；
- 地址框输入地址即启动引擎②；
- 两框不必都填；不做 18 位/校验位强校验；
- 证件无效且地址为空 -> 异常。
"""
import os
import tempfile
import unittest

from database.db_manager import DBManager
from services.match_service import run_match


def build_db():
    """构建使用真实 GB/T 2260 代码的临时库（道外区=230104，海淀区=110108）。"""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.remove(path)
    db = DBManager(path)
    db.insert_districts([
        {"id": "230000000000", "pids": "", "pid": None,
         "district_code": "230000000000", "district_name": "黑龙江省",
         "admin_level": 1, "province_code": "230000000000", "city_code": None},
        {"id": "230100000000", "pids": "230000000000", "pid": "230000000000",
         "district_code": "230100000000", "district_name": "哈尔滨市",
         "admin_level": 2, "province_code": "230000000000",
         "city_code": "230100000000"},
        {"id": "230104000000", "pids": "230000000000,230100000000",
         "pid": "230100000000", "district_code": "230104000000",
         "district_name": "道外区", "admin_level": 3,
         "province_code": "230000000000", "city_code": "230100000000"},
        {"id": "110000000000", "pids": "", "pid": None,
         "district_code": "110000000000", "district_name": "北京市",
         "admin_level": 1, "province_code": "110000000000", "city_code": None},
        {"id": "110100000000", "pids": "110000000000", "pid": "110000000000",
         "district_code": "110100000000", "district_name": "市辖区",
         "admin_level": 2, "province_code": "110000000000",
         "city_code": "110100000000"},
        {"id": "110108000000", "pids": "110000000000,110100000000",
         "pid": "110100000000", "district_code": "110108000000",
         "district_name": "海淀区", "admin_level": 3,
         "province_code": "110000000000", "city_code": "110100000000"},
    ])
    return db


class TestQueryFlow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = build_db()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def test_6digit_code_only(self):
        r = run_match(self.db, "230105", "", with_detail=True)
        self.assertEqual(r.match_status, "已解析(仅证件)")
        self.assertEqual(r.district_name, "道外区")
        self.assertEqual(r.district_code, "230104000000")
        self.assertIn("历史映射", r.engine1_detail)
        self.assertIn("未输入地址", r.engine2_detail)

    def test_18digit_id_only(self):
        r = run_match(self.db, "23010519890714291X", "", with_detail=True)
        self.assertEqual(r.match_status, "已解析(仅证件)")
        self.assertEqual(r.district_code, "230104000000")

    def test_address_only(self):
        r = run_match(self.db, "", "黑龙江省哈尔滨市道外区水泥路99-4号",
                      with_detail=True)
        self.assertEqual(r.match_status, "已解析")
        self.assertEqual(r.district_name, "道外区")
        self.assertEqual(r.district_code, "230104000000")
        self.assertIn("未输入有效的身份证", r.engine1_detail)

    def test_both_code_and_address(self):
        r = run_match(self.db, "230105", "黑龙江省哈尔滨市道外区水泥路99-4号",
                      with_detail=True)
        self.assertEqual(r.match_status, "正确")
        self.assertEqual(r.district_code, "230104000000")

    def test_municipality_address_only(self):
        r = run_match(self.db, "", "北京市海淀区中关村大街1号", with_detail=True)
        self.assertEqual(r.match_status, "已解析")
        self.assertEqual(r.city_name, "北京市")
        self.assertEqual(r.district_name, "海淀区")
        self.assertEqual(r.district_code, "110108000000")

    def test_no_valid_input(self):
        r = run_match(self.db, "abc123", "", with_detail=True)
        self.assertEqual(r.match_status, "异常")
        self.assertIn("未输入有效的身份证/区划代码或地址", r.decision_path)

    def test_invalid_id_with_address_uses_engine2(self):
        r = run_match(self.db, "abc123", "黑龙江省哈尔滨市道外区水泥路99-4号",
                      with_detail=True)
        self.assertEqual(r.match_status, "已解析")
        self.assertEqual(r.district_code, "230104000000")
        self.assertIn("未输入有效的身份证", r.engine1_detail)

    def test_whitespace_inputs(self):
        r = run_match(self.db, "  ", "  ", with_detail=True)
        self.assertEqual(r.match_status, "异常")


if __name__ == "__main__":
    unittest.main()