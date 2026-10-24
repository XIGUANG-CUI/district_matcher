# -*- coding: utf-8 -*-
"""区划匹配性能基准（可复现）。

用法（仓库根目录）：
    python scripts/bench_district_match.py --pack data/district_pack.tsv.gz

说明：
- 自动创建临时 USER_DATA_DIR，不触碰真实 student.db；
- 全量导入数据包（664,239 行）后测 warm_cache / 单条 / 3000 行批量；
- 输出供 CHANGES.md §6 引用的实测数字。
"""
import argparse
import os
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault('USER_DATA_DIR', tempfile.mkdtemp(prefix='dm_bench_'))

import models  # noqa: E402
from models import district as dm  # noqa: E402

SAMPLES = [
    ('230104199001010011', '黑龙江省哈尔滨市道外区水泥路1号'),
    ('110108199001010022', '北京市海淀区中关村大街1号'),
    ('440305199001010033', '广东省深圳市南山区科技园南区'),
    ('320509199001010044', '江苏省苏州市吴江区松陵镇'),
    ('231183199001010055', '黑龙江省黑河市嫩江市嫩江镇'),
    ('500120199001010066', '重庆市璧山区璧城街道'),
    ('230105199001010077', '黑龙江省哈尔滨市太平区'),
    ('', '黑龙江省哈尔滨市道外区水泥路街道水泥路社区'),
]


def main():
    parser = argparse.ArgumentParser(description='区划匹配性能基准')
    parser.add_argument('--pack', default=os.path.join(ROOT, 'data', 'district_pack.tsv.gz'))
    parser.add_argument('--rows', type=int, default=3000)
    args = parser.parse_args()

    models.init_db()

    started = time.perf_counter()
    result = dm.import_dictionary(pack_file=args.pack)
    print('import_seconds=%.2f total=%s by_level=%s'
          % (time.perf_counter() - started, result.get('total'), result.get('by_level')))

    started = time.perf_counter()
    info = dm.warm_cache()
    print('warm_seconds=%.2f rows=%s history_rows=%s'
          % (time.perf_counter() - started, info.get('rows'), info.get('history_rows')))

    rows = [{'id_card': SAMPLES[i % len(SAMPLES)][0],
             'address': SAMPLES[i % len(SAMPLES)][1]} for i in range(args.rows)]
    started = time.perf_counter()
    dm.fill_district_fields_many(rows)
    elapsed = time.perf_counter() - started
    hits = sum(1 for r in rows if r.get('district_county_code'))
    print('many_%d_seconds=%.3f per_row_ms=%.3f hits=%d'
          % (args.rows, elapsed, elapsed * 1000 / args.rows, hits))

    started = time.perf_counter()
    for i in range(200):
        dm.fill_district_fields({'id_card': SAMPLES[i % len(SAMPLES)][0],
                                 'address': SAMPLES[i % len(SAMPLES)][1]}, dm.get_db())
    print('single_row_ms=%.3f' % ((time.perf_counter() - started) * 1000 / 200))
    print('district_db_bytes=%d' % os.path.getsize(dm.DISTRICT_DB_PATH))


if __name__ == '__main__':
    main()
