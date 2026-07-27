"""测试共享 fixture：构建含 230105→道外区 历史变更场景的内存库。"""
import os
import tempfile

from database.db_manager import DBManager

FIXTURE_ROWS = [
    # 黑龙江省
    {"id": "230000000000", "pids": "", "pid": None, "district_code": "230000000000",
     "district_name": "黑龙江省", "admin_level": 1,
     "province_code": "230000000000", "city_code": None},
    # 哈尔滨市（市级）
    {"id": "230100000000", "pids": "230000000000", "pid": "230000000000",
     "district_code": "230100000000", "district_name": "哈尔滨市",
     "admin_level": 2, "province_code": "230000000000", "city_code": "230100000000"},
    # 道外区（现行，编码 230102）
    {"id": "230102000000", "pids": "230000000000,230100000000", "pid": "230100000000",
     "district_code": "230102000000", "district_name": "道外区",
     "admin_level": 3, "province_code": "230000000000", "city_code": "230100000000"},
    # 依兰县（用于“省+县、缺市”地址解析回归测试）
    {"id": "230123000000", "pids": "230000000000,230100000000", "pid": "230100000000",
     "district_code": "230123000000", "district_name": "依兰县",
     "admin_level": 3, "province_code": "230000000000", "city_code": "230100000000"},
    # 北京市（直辖市，仅有省级）
    {"id": "110000000000", "pids": "", "pid": None, "district_code": "110000000000",
     "district_name": "北京市", "admin_level": 1,
     "province_code": "110000000000", "city_code": None},
    # 海淀区
    {"id": "110108000000", "pids": "110000000000", "pid": "110000000000",
     "district_code": "110108000000", "district_name": "海淀区",
     "admin_level": 3, "province_code": "110000000000", "city_code": "110000000000"},
]


def build_fixture_db():
    """返回一个已填充 fixture 数据的 DBManager（临时文件）。"""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.remove(path)
    db = DBManager(path)
    db.insert_districts(FIXTURE_ROWS)
    return db
