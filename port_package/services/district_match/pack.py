"""数据包读取与校验（TSV + gzip，字段顺序锁定）。

格式与文档 4.5 完全一致（6 列）：
    district_code	district_name	admin_level	parent_code	province_code	city_code
首行允许 `#` 注释行与表头行。

不引入 pandas：gzip + 标准库 csv 逐行读。
"""
import gzip
import hashlib
import os

PACK_COLUMNS = (
    "district_code", "district_name", "admin_level",
    "parent_code", "province_code", "city_code",
)
HEADER_LINE = "\t".join(PACK_COLUMNS)

# 文档 2.4 实测基准（构建脚本产出的包必须与之吻合）
EXPECTED_LEVEL_COUNTS = {1: 31, 2: 343, 3: 3255, 4: 41274, 5: 619336}
EXPECTED_TOTAL = 664239

# 由 scripts/build_district_pack.py 产出后回填（见 data/district_pack.sha256）
EXPECTED_SHA256 = "d1e60bbb0141da282410c618aefecddcb9d8962ab647c7b5ffabbac8451c05f4"


def sha256_file(path, chunk=1024 * 1024):
    """计算文件 sha256（分块读取）。"""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            block = f.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def iter_pack_rows(path):
    """迭代数据包，产出 6 元组 (code, name, level, parent_code, province_code, city_code)。"""
    if not os.path.exists(path):
        raise FileNotFoundError(f"数据包不存在：{path}")
    with gzip.open(path, "rt", encoding="utf-8", newline="") as fh:
        for line in fh:
            if not line or line.startswith("#"):
                continue
            if line.startswith("district_code\t"):
                continue
            if not line.strip():
                continue
            parts = line.rstrip("\r\n").split("\t")
            if len(parts) != 6:
                raise ValueError(
                    f"数据包列数异常：期望 6 列，实际 {len(parts)} 列 -> {parts[:3]}")
            yield (
                parts[0], parts[1], int(parts[2]),
                parts[3] or None, parts[4] or None, parts[5] or None,
            )


def count_pack(path):
    """统计数据包总行数与各级行数（验收用）。"""
    total = 0
    by_level = {1: 0, 2: 0, 3: 0, 4: 0, 5: 0}
    for _code, _name, level, *_rest in iter_pack_rows(path):
        total += 1
        by_level[level] = by_level.get(level, 0) + 1
    return {"total": total, "by_level": by_level}


def verify_pack(path, expected=None, check_sha256=True):
    """校验数据包与文档 2.4 是否一致；返回 (ok, detail)。"""
    expected = expected or {
        "total": EXPECTED_TOTAL,
        "by_level": EXPECTED_LEVEL_COUNTS,
    }
    if not os.path.exists(path):
        return False, {"error": "数据包不存在", "path": path}
    counts = count_pack(path)
    detail = dict(counts)
    detail["sha256"] = sha256_file(path) if check_sha256 else ""
    detail["size"] = os.path.getsize(path)
    ok = counts["total"] == expected.get("total")
    for level, n in (expected.get("by_level") or {}).items():
        if counts["by_level"].get(level) != n:
            ok = False
    exp_sha = expected.get("sha256") or EXPECTED_SHA256
    if check_sha256 and exp_sha and not exp_sha.startswith("__"):
        if exp_sha != detail["sha256"]:
            ok = False
    return ok, detail
