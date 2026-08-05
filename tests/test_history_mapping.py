"""历史代码映射库化（t_history_mapping）单元测试。

覆盖：
- 首次建库自动写入内置种子（16 条）
- DB 层接口：get_history_alias_map / get_history_code_map / list / upsert / delete / reset
- 引擎②地址别名翻译走 DB（太平区 -> 道外区）
- 引擎①旧码 -> 新码直查（230105 -> 230104，mapped 标记）
- reset_db 后重新种子、用户删除后不回种
- Excel 导入历史映射
"""
import os
import tempfile
import unittest

import pandas as pd

from database.db_manager import DBManager
from database import history_data
from engine import engine1, engine2
from services import import_service, match_service


def build_correct_db():
    """构建使用真实 GB/T 2260 代码的临时库（道外区=230104）。"""
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
    ])
    return db


class TestHistorySeed(unittest.TestCase):
    def test_builtin_seed_consistency(self):
        # 种子数量与派生映射一致
        self.assertEqual(len(history_data.HISTORY_SEED),
                         len(history_data.HISTORY_NAME_MAP))
        self.assertEqual(len(history_data.HISTORY_SEED),
                         len(history_data.HISTORY_CODE_MAP))
        # 核心场景：230105 太平区 -> 230104 道外区
        self.assertEqual(history_data.HISTORY_CODE_MAP["230105"], "230104")
        self.assertEqual(history_data.HISTORY_NAME_MAP["太平区"], "道外区")

    def test_seed_auto_written_on_first_access(self):
        db = DBManager(tempfile.mktemp(suffix=".db"))
        self.assertEqual(db.count_history_mappings(), len(history_data.HISTORY_SEED))
        alias = db.get_history_alias_map()
        self.assertEqual(alias["太平区"], "道外区")
        self.assertEqual(alias["嫩江县"], "嫩江市")
        code = db.get_history_code_map()
        self.assertEqual(code["230105"], "230104")
        self.assertEqual(code["230182"], "230113")
        db.close()

    def test_reset_db_reseeds(self):
        db = DBManager(tempfile.mktemp(suffix=".db"))
        db.get_history_alias_map()  # 触发种子
        db.reset_db()
        # 重置后下次访问重新写入内置种子（回到出厂状态）
        self.assertEqual(db.count_history_mappings(), len(history_data.HISTORY_SEED))
        db.close()

    def test_user_delete_not_reseeded(self):
        db = DBManager(tempfile.mktemp(suffix=".db"))
        rows = db.list_history_mappings()
        for r in rows:
            db.delete_history_mapping(r["id"])
        # 用户显式删除后不回种（表为唯一权威）
        self.assertEqual(db.count_history_mappings(), 0)
        self.assertEqual(db.get_history_alias_map(), {})
        db.close()


class TestHistoryCRUD(unittest.TestCase):
    def setUp(self):
        self.db = DBManager(tempfile.mktemp(suffix=".db"))

    def tearDown(self):
        self.db.close()

    def test_upsert_by_old_code(self):
        self.db.insert_history_mapping({
            "old_code": "999999", "new_code": "888888",
            "old_name": "旧名甲", "new_name": "新名甲",
            "change_type": "测试", "change_date": "2020", "remark": ""})
        self.assertEqual(self.db.count_history_mappings(), 17)
        # 再次保存同 old_code -> 覆盖而非新增
        self.db.insert_history_mapping({
            "old_code": "999999", "new_code": "777777",
            "old_name": "旧名甲", "new_name": "新名乙",
            "change_type": "测试", "change_date": "2021", "remark": ""})
        self.assertEqual(self.db.count_history_mappings(), 17)
        code = self.db.get_history_code_map()
        self.assertEqual(code["999999"], "777777")
        alias = self.db.get_history_alias_map()
        self.assertEqual(alias["旧名甲"], "新名乙")

    def test_delete_by_id(self):
        before = self.db.count_history_mappings()
        row = self.db.list_history_mappings()[0]
        self.db.delete_history_mapping(row["id"])
        self.assertEqual(self.db.count_history_mappings(), before - 1)
        self.assertNotIn(row["old_code"], self.db.get_history_code_map())

    def test_reset_history_mappings(self):
        self.db.insert_history_mapping({
            "old_code": "999999", "new_code": "888888",
            "old_name": "旧名甲", "new_name": "新名甲"})
        self.db.reset_history_mappings()
        rows = self.db.list_history_mappings()
        self.assertEqual(len(rows), len(history_data.HISTORY_SEED))
        self.assertNotIn("999999", self.db.get_history_code_map())


class TestEngineIntegration(unittest.TestCase):
    def test_engine1_old_code_direct_lookup(self):
        db = build_correct_db()
        res = engine1.engine1_match(db, "23010519890714291X")
        self.assertTrue(res.district.matched)
        self.assertEqual(res.district.name, "道外区")
        self.assertEqual(res.district.code, "230104000000")
        self.assertTrue(res.district.mapped)  # 历史映射命中标记
        self.assertEqual(res.province.name, "黑龙江省")
        self.assertEqual(res.city.name, "哈尔滨市")
        db.close()

    def test_engine1_current_code_unaffected(self):
        db = build_correct_db()
        res = engine1.engine1_match(db, "23010419890714291X")
        self.assertTrue(res.district.matched)
        self.assertEqual(res.district.name, "道外区")
        self.assertFalse(res.district.mapped)  # 现行代码直接命中
        db.close()

    def test_engine2_alias_from_db(self):
        from tests._fixture import build_fixture_db
        db = build_fixture_db()
        # fixture 的 道外区=230102（历史遗留约定），此处仅验证别名翻译走 DB
        e2 = engine2.engine2_parse(db, "哈尔滨市太平区南马路1号", "23010519890714291X")
        self.assertEqual(e2.district, "道外区")
        self.assertEqual(e2.district_code, "230102000000")
        db.close()

    def test_engine2_alias_removed_stops_resolving(self):
        from tests._fixture import build_fixture_db
        db = build_fixture_db()
        # 删除 太平区->道外区 映射后，地址旧名不再被翻译命中
        rows = [r for r in db.list_history_mappings()
                if r["old_name"] == "太平区"]
        self.assertTrue(rows)
        db.delete_history_mapping(rows[0]["id"])
        e2 = engine2.engine2_parse(db, "哈尔滨市太平区南马路1号", "23010519890714291X")
        self.assertIsNone(e2.district_code)
        db.close()

    def test_match_full_flow_correct(self):
        db = build_correct_db()
        res = match_service.run_match(
            db, "23010519890714291X", "黑龙江省哈尔滨市道外区水泥路99-4号",
            with_detail=True)
        self.assertEqual(res.match_status, "正确")
        self.assertEqual(res.district_name, "道外区")
        self.assertEqual(res.district_code, "230104000000")
        self.assertIn("历史映射", res.engine1_detail)
        db.close()


class TestEngine2Regressions(unittest.TestCase):
    """v2.3 回归：别名同名跨区误配 + 直辖市“市辖区”虚拟节点。"""

    @staticmethod
    def _build_alias_cross_db():
        """哈尔滨(道外区230104) + 阜新(太平区210904)，别名 太平区->道外区。"""
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
            {"id": "210000000000", "pids": "", "pid": None,
             "district_code": "210000000000", "district_name": "辽宁省",
             "admin_level": 1, "province_code": "210000000000", "city_code": None},
            {"id": "210900000000", "pids": "210000000000", "pid": "210000000000",
             "district_code": "210900000000", "district_name": "阜新市",
             "admin_level": 2, "province_code": "210000000000",
             "city_code": "210900000000"},
            {"id": "210904000000", "pids": "210000000000,210900000000",
             "pid": "210900000000", "district_code": "210904000000",
             "district_name": "太平区", "admin_level": 3,
             "province_code": "210000000000", "city_code": "210900000000"},
        ])
        return db

    def test_old_name_scoped_by_city_not_global(self):
        # 哈尔滨太平区（已撤销，别名道外区）不应误命中阜新市太平区
        db = self._build_alias_cross_db()
        e2 = engine2.engine2_parse(db, "哈尔滨市太平区南马路1号")
        self.assertEqual(e2.district, "道外区")
        self.assertEqual(e2.district_code, "230104000000")
        self.assertEqual(e2.city, "哈尔滨市")
        db.close()

    def test_same_name_current_district_other_city_still_works(self):
        # 阜新市太平区是现行区划，应仍能命中阜新太平区（别名仅对旧名地址生效）
        db = self._build_alias_cross_db()
        e2 = engine2.engine2_parse(db, "阜新市太平区红树路1号")
        self.assertEqual(e2.district, "太平区")
        self.assertEqual(e2.district_code, "210904000000")
        self.assertEqual(e2.city, "阜新市")
        db.close()

    def test_municipality_virtual_node_city_is_province(self):
        # 真实库结构：海淀区 pid -> 市辖区(level2)；市级应为 北京市 而非 市辖区
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.remove(path)
        db = DBManager(path)
        db.insert_districts([
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
        e2 = engine2.engine2_parse(db, "北京市海淀区中关村大街1号")
        self.assertEqual(e2.city, "北京市")
        self.assertEqual(e2.city_code, "110000000000")
        self.assertEqual(e2.province, "北京市")
        self.assertEqual(e2.district, "海淀区")
        db.close()


class TestHistoryImport(unittest.TestCase):
    def test_import_excel(self):
        db = DBManager(tempfile.mktemp(suffix=".db"))
        tmp = tempfile.mkdtemp()
        path = os.path.join(tmp, "h.xlsx")
        pd.DataFrame([
            {"旧代码": "123456", "新代码": "654321", "旧名称": "旧名X",
             "新名称": "新名X", "变更类型": "测试", "变更日期": "2020", "备注": ""},
            {"旧代码": "111111", "新代码": "222222", "旧名称": "", "新名称": ""},
            {"旧代码": "", "新代码": "", "旧名称": "只有旧名", "新名称": ""},
        ]).to_excel(path, index=False)
        summary = import_service.import_history_mappings(db, path)
        # 第1行完整；第2行有代码对；第3行仅旧名无新名 -> 跳过
        self.assertEqual(summary["imported"], 2)
        self.assertEqual(summary["skipped"], 1)
        code = db.get_history_code_map()
        self.assertEqual(code["123456"], "654321")
        alias = db.get_history_alias_map()
        self.assertEqual(alias["旧名X"], "新名X")
        db.close()


if __name__ == "__main__":
    unittest.main()