"""对外 SDK：供其他 Python 程序直接调用的 DistrictMatcher。

注意：本项目内部模块（database / services / utils / config）使用项目根目录
绝对导入，因此引用本 SDK 前需保证 district_matcher 目录在 sys.path 中。
本文件会自动将项目根目录加入 sys.path，因此只要本文件物理位置在
district_matcher/sdk/ 下，即可被任意外部程序直接 import。

用法：
    import sys
    sys.path.insert(0, "/path/to/district_matcher")   # 包含 sdk/ 的目录
    from sdk.district_matcher import DistrictMatcher
    matcher = DistrictMatcher(db_path="district_matcher.db")
    result = matcher.match("23010519890714291X", "哈尔滨市道外区水泥路99-4号")
"""
import os
import sys

# 自动将项目根目录（district_matcher/）加入 sys.path，保证内部模块可解析
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database.db_manager import DBManager
from services.match_service import run_match


class DistrictMatcher:
    def __init__(self, db_path=None):
        self.db = DBManager(db_path)

    def match(self, id_card, address=""):
        """单条匹配，返回标准 dict 结果。"""
        result = run_match(self.db, id_card, address or "")
        return {
            "province_code": result.province_code,
            "province_name": result.province_name,
            "city_code": result.city_code,
            "city_name": result.city_name,
            "district_code": result.district_code,
            "district_name": result.district_name,
            "match_status": result.match_status,
        }

    def match_batch(self, students):
        """批量匹配。students: list[dict]{name,id_card,address}"""
        out = []
        for s in students:
            rec = self.match(s.get("id_card"), s.get("address", ""))
            rec["name"] = s.get("name")
            out.append(rec)
        return out
