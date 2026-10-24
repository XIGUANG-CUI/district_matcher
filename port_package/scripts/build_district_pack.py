# -*- coding: utf-8 -*-
"""生成 district_pack.tsv.gz（可复现：固定排序 + gzip mtime=0）。

用法（在仓库根目录）：
    python scripts/build_district_pack.py \
        --source database/district_matcher.db \
        --out data/district_pack.tsv.gz \
        --max-level 5

产出：
    data/district_pack.tsv.gz   6 列 TSV（district_code/name/admin_level/parent_code/province_code/city_code）
    data/district_pack.sha256   sha256 + 行数 + 各级行数 + 字节数

不依赖 pandas / numpy，仅标准库。
"""
import argparse
import gzip
import hashlib
import os
import sqlite3
import sys

EXPECTED_TOTAL = 664239
EXPECTED_LEVELS = {1: 31, 2: 343, 3: 3255, 4: 41274, 5: 619336}
HEADER = "district_code\tdistrict_name\tadmin_level\tparent_code\tprovince_code\tcity_code\n"


def build(source, out_path, max_level=5, verify=True):
    conn = sqlite3.connect(source)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT id, district_code, district_name, admin_level, pid, "
            "province_code, city_code FROM t_district_code "
            "WHERE admin_level <= ? ORDER BY admin_level, district_code",
            (max_level,)).fetchall()
        id_to_code = {r["id"]: r["district_code"] for r in rows}

        os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
        # mtime=0 + 固定压缩级别 => 同样输入得到同样字节（便于 sha256 验收）
        with open(out_path, "wb") as raw:
            with gzip.GzipFile(filename="", mode="wb", fileobj=raw,
                               compresslevel=9, mtime=0) as gz:
                gz.write(HEADER.encode("utf-8"))
                counts = {}
                total = 0
                for r in rows:
                    code = r["district_code"]
                    parent = id_to_code.get(r["pid"], "") if r["pid"] else ""
                    line = "%s\t%s\t%d\t%s\t%s\t%s\n" % (
                        code, r["district_name"], r["admin_level"], parent,
                        r["province_code"] or "", r["city_code"] or "")
                    gz.write(line.encode("utf-8"))
                    total += 1
                    counts[r["admin_level"]] = counts.get(r["admin_level"], 0) + 1
    finally:
        conn.close()

    size = os.path.getsize(out_path)
    h = hashlib.sha256()
    with open(out_path, "rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    digest = h.hexdigest()

    summary = {
        "total": total,
        "by_level": dict(sorted(counts.items())),
        "size": size,
        "sha256": digest,
        "max_level": max_level,
    }

    sha_path = os.path.splitext(out_path)[0] + ".sha256"
    with open(sha_path, "w", encoding="utf-8") as f:
        f.write("# district_pack.tsv.gz 校验信息（由 scripts/build_district_pack.py 生成）\n")
        f.write("sha256  %s\n" % digest)
        f.write("size    %d\n" % size)
        f.write("total   %d\n" % total)
        f.write("levels  " + " ".join("%d:%d" % kv for kv in sorted(counts.items())) + "\n")

    print("数据包：%s" % out_path)
    print("sha256：%s" % digest)
    print("大小  ：%d 字节（%.2f MB）" % (size, size / 1024.0 / 1024.0))
    print("行数  ：%d" % total)
    print("各级  ：%s" % summary["by_level"])

    if verify and max_level == 5:
        if total != EXPECTED_TOTAL or dict(counts) != EXPECTED_LEVELS:
            print("警告：行数与文档 2.4 不一致（期望 total=%d, levels=%s）"
                  % (EXPECTED_TOTAL, EXPECTED_LEVELS))
    return summary


def main():
    parser = argparse.ArgumentParser(description="生成区划字典数据包")
    parser.add_argument("--source", default=os.path.join("database", "district_matcher.db"),
                        help="源字典库（含 t_district_code）")
    parser.add_argument("--out", default=os.path.join("data", "district_pack.tsv.gz"),
                        help="输出数据包路径")
    parser.add_argument("--max-level", type=int, default=5,
                        help="最大层级：5=全量（默认）；4=仅省/市/区县（应急降级）")
    parser.add_argument("--no-verify", action="store_true", help="跳过行数校验")
    args = parser.parse_args()

    if not os.path.exists(args.source):
        print("源库不存在：%s" % args.source, file=sys.stderr)
        return 1
    build(args.source, args.out, max_level=args.max_level, verify=not args.no_verify)
    return 0


if __name__ == "__main__":
    sys.exit(main())
