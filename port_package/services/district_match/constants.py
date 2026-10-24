"""常量：替代原项目 config.py 中被算法层引用的部分。

原则：只搬算法需要的常量，不搬路径/端口/上传目录等运行时配置。
"""

# 直辖市：前 2 位 -> 名称
MUNICIPALITIES = {
    "11": "北京市",
    "12": "天津市",
    "31": "上海市",
    "50": "重庆市",
}

# 12 位代码补零规则
PROVINCE_PAD = "0000000000"   # 2 位 + 10 个 0
CITY_PAD = "00000000"         # 4 位 + 8 个 0
DISTRICT_PAD = "000000"       # 6 位 + 6 个 0

# 匹配状态枚举（v2，共 7 种）
STATUS_CORRECT = "正确"
STATUS_PARSED = "已解析"
STATUS_PARSED_ID_ONLY = "已解析(仅证件)"
STATUS_REVIEW = "需复核"
STATUS_PARTIAL = "部分"
STATUS_NOMATCH = "无匹配"
STATUS_ERROR = "异常"

ALL_STATUSES = (
    STATUS_CORRECT,
    STATUS_PARSED,
    STATUS_PARSED_ID_ONLY,
    STATUS_REVIEW,
    STATUS_PARTIAL,
    STATUS_NOMATCH,
    STATUS_ERROR,
)

ADMIN_LEVEL_NAME = {1: "省", 2: "市", 3: "区县", 4: "街道/镇", 5: "村/社区"}

# 录入联动：students 表 7 个区划列（字段名锁定，不得改名）
DISTRICT_FIELDS = (
    "district_province_name",
    "district_province_code",
    "district_city_name",
    "district_city_code",
    "district_county_name",
    "district_county_code",
    "district_match_status",
)

# 字典库文件名（与 student.db 同目录，由 0.2 #3 的路径规则推导）
DISTRICT_DB_FILENAME = "district.db"
DISTRICT_PACK_FILENAME = "district_pack.tsv.gz"

# 缓存常驻层级上限：L1–L4 常驻内存，L5 走 SQL
CACHE_MAX_LEVEL = 4
