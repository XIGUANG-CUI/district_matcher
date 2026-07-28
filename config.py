"""配置文件：路径与全局常量。"""
import os
import sys

# 项目根目录
# PyInstaller 打包后（sys.frozen == True）：
#   BASE_DIR  = exe 所在目录（可写：数据库 / uploads / config.txt）
#   BUNDLE_DIR = _MEIPASS 临时解压目录（只读：模板 / 静态文件 / 区划源数据）
if getattr(sys, 'frozen', False):
    BASE_DIR = os.path.dirname(sys.executable)
    BUNDLE_DIR = sys._MEIPASS
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    BUNDLE_DIR = BASE_DIR

# 数据库文件（可写，exe 同级 database/ 目录）
DB_PATH = os.path.join(BASE_DIR, "database", "district_matcher.db")

# 数据目录（只读，打包的 Excel 源数据）
DATA_DIR = os.path.join(BUNDLE_DIR, "data")

# 上传临时目录（可写，exe 同级 uploads/ 目录）
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

# 行政区划相关常量
MUNICIPALITIES = {
    "11": "北京市",
    "12": "天津市",
    "31": "上海市",
    "50": "重庆市",
}  # 直辖市：前2位 -> 名称

# 12 位代码补零规则
PROVINCE_PAD = "0000000000"   # 2 + 10 个 0
CITY_PAD = "00000000"         # 4 + 8 个 0
DISTRICT_PAD = "000000"       # 6 + 6 个 0

# 匹配状态枚举
STATUS_CORRECT = "正确"        # 引擎①区县命中且校验一致
STATUS_PARSED = "已解析"        # 依赖引擎②反查得到
STATUS_NOMATCH = "无匹配"       # 双引擎均失败
STATUS_ERROR = "异常"           # 数据格式异常

ADMIN_LEVEL_NAME = {1: "省", 2: "市", 3: "区县", 4: "街道/镇", 5: "村/社区"}

# 上传/导出临时目录清理策略（防止无限膨胀）
MAX_EXPORT_KEEP = 10  # 导出文件（result_export_*）最多保留最新的若干个，其余自动清理
PROTECTED_UPLOADS = {"全国行政编码数据.xlsx"}  # 受保护的源数据文件，自动清理时永不删除（删除需人工确认）
