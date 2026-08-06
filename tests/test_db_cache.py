"""DB 缓存按需加载（方案 B）单元测试。

方案 B：内存仅常驻 level 1-4（省/市/区县/乡镇街道），
level 5（村/社区）冷路径按需走 SQL。接口行为与全量加载一致。
"""
import os
import tempfile
import unittest

from database.db_manager import DBManager
from engine import engine2

LEVEL15_ROWS = [
    # level 1 黑龙江省
    {"id": "230000000000", "pids": "", "pid": None,
     "district_code": "230000000000", "district_name": "黑龙江省",
     "admin_level": 1, "province_code": "230000000000", "city_code": None},
    # level 2 哈尔滨市
    {"id": "230100000000", "pids": "230000000000", "pid": "230000000000",
     "district_code": "230100000000", "district_name": "哈尔滨市",
     "admin_level": 2, "province_code": "230000000000", "city_code": "230100000000"},
    # level 3 道外区
    {"id": "230104000000", "pids": "230000000000,230100000000",
     "pid": "230100000000", "district_code": "230104000000",
     "district_name": "道外区", "admin_level": 3,
     "province_code": "230000000000", "city_code": "230100000000"},
    # level 4 东原街道
    {"id": "230104001000", "pids": "230000000000,230100000000,230104000000",
     "pid": "230104000000", "district_code": "230104001000",
     "district_name": "东原街道", "admin_level": 4,
     "province_code": "230000000000", "city_code": "230100000000"},
    # level 5 双兴村（村/社区，冷路径）
    {"id": "230104001001", "pids": "230000000000,230100000000,230104000000,230104001000",
     "pid": "230104001000", "district_code": "230104001001",
     "district_name": "双兴村", "admin_level": 5,
     "province_code": "230000000000", "city_code": "230100000000"},
]


def build_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.remove(path)
    db = DBManager(path)
    db.insert_districts(LEVEL15_ROWS)
    return db


class TestLazyCache(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = build_db()
        cls.db._ensure_cache()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def test_memory_only_level_1_to_4(self):
        # 内存字典只含 level 1-4（4 行），不含 level 5
        self.assertEqual(len(self.db._by_id), 4)
        self.assertEqual(len(self.db._by_code), 4)
        self.assertNotIn("230104001001", self.db._by_id)
        self.assertNotIn(("230104001001", 5), self.db._by_code)

    def test_get_by_district_code_level3_memory(self):
        row = self.db.get_by_district_code("230104000000", 3)
        self.assertEqual(row["district_name"], "道外区")

    def test_get_by_district_code_level5_sql(self):
        row = self.db.get_by_district_code("230104001001", 5)
        self.assertEqual(row["district_name"], "双兴村")
        self.assertEqual(row["admin_level"], 5)

    def test_search_by_name_level4_memory(self):
        rows = self.db.search_by_name("东原街道", 4)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["admin_level"], 4)

    def test_search_by_name_level5_sql(self):
        rows = self.db.search_by_name("双兴村", 5)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["district_name"], "双兴村")
        self.assertEqual(rows[0]["pid"], "230104001000")  # 父级 level4

    def test_search_by_name_no_level_sql(self):
        # 不分层级名称搜索走 SQL（兼容预留能力）
        rows = self.db.search_by_name("双兴村")
        self.assertTrue(any(r["admin_level"] == 5 for r in rows))

    def test_reverse_lookup_level5_sql(self):
        row = self.db.reverse_lookup("双兴村", 5)
        self.assertEqual(row["district_code"], "230104001001")

    def test_get_by_id_level4_parent(self):
        # level5 行的 pid 指向 level4，get_by_id 应能查到（内存）
        row = self.db.get_by_id("230104001000")
        self.assertEqual(row["district_name"], "东原街道")

    def test_engine2_town_village_fallback(self):
        # 地址省略区县，仅街道+村 -> 乡镇/村反推仍能命中区县（level5 走 SQL）
        e2 = engine2.engine2_parse(self.db, "黑龙江省东原街道双兴村1组")
        self.assertEqual(e2.district, "道外区")
        self.assertEqual(e2.district_code, "230104000000")

    def test_engine2_direct_district_no_level5(self):
        # 地址含区县时正常命中，不触发 level5
        e2 = engine2.engine2_parse(self.db, "黑龙江省哈尔滨市道外区东原街道双兴村1组")
        self.assertEqual(e2.district, "道外区")
        self.assertEqual(e2.district_code, "230104000000")


if __name__ == "__main__":
    unittest.main()