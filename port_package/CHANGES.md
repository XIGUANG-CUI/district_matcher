# CHANGES.md — 区划匹配模块接入变更说明

- 基准仓库：`StudentMessageSharePRO-v2`
- 基准 commit：`89f330c0ee7845702eb6f0d720b5f3e950d5154b`（2026-10-23）
- 补丁文件：`patches/district_match_89f330c.patch`（`git apply --check` 已通过；不含 `docs/PORTING_GUIDE.md`，不含二进制数据包）
- 补丁规模：主补丁 **28 个文件，+4097 / -12**（新增 20 个文件 + 新增 `data/district_pack.tsv.sha256` 1 个 + 修改 7 个文件）
- 增量补丁：`patches/district_match_89f330c_fix1.patch`（**5 个文件**：`run_with_tray.spec` + 四份说明），基线为「89f330c + 主补丁」
- 数据包：`data/district_pack.tsv.gz`（5,411,508 字节）+ `data/district_pack.tsv.sha256`，需复制到仓库 `data/` 目录（打包必需）
- 偏差记录：`IMPLEMENTATION_NOTES.md`（体积/代码/懒加载/新增 `source` 列/打包数据包等 12 项，逐条给出原因）

---

## 1. 新增文件（20）

| 文件 | 说明 |
|---|---|
| `models/district.py` | Repository + 进程级缓存 + `fill_district_fields(_many)` + 4.9 三接口 + 批量回填 + 字典导入 |
| `services/district_match/__init__.py` | 纯算法包入口（禁止 import flask/models/config） |
| `services/district_match/constants.py` | 状态常量、7 列字段名、缓存层级、字典库/数据包文件名 |
| `services/district_match/models.py` | `LevelResult / Engine1Result / Engine2Result / MatchResult` |
| `services/district_match/validators.py` | 身份证格式与前 6 位提取（不做校验位强校验） |
| `services/district_match/address_parser.py` | 地址分词：省/市/区县/乡镇/村；自治区简称扩展；`(?<!社)区` 避免“社区”误判 |
| `services/district_match/engine1.py` | 引擎①：身份证前 6 位 → 12 位代码；历史码映射 |
| `services/district_match/engine2.py` | 引擎②：地址解析 + 名称反查 + 乡镇/村兜底反推；历史别名优先 |
| `services/district_match/decider.py` | 双引擎决策（引擎②为主，引擎①校验/兜底），7 种状态 |
| `services/district_match/runner.py` | `run_match / to_district_fields / to_result_row / process_batch` |
| `services/district_match/importer.py` | 数据包导入（事务化清空重灌）+ 批量输入行读取（xlsx/csv） |
| `services/district_match/exporter.py` | 结果导出（xlsx/csv，10 列中文表头，公式注入防护） |
| `services/district_match/pack.py` | 数据包读取/计数/sha256 校验（含期望值回填） |
| `scripts/build_district_pack.py` | 数据包生成脚本（可复现：固定排序 + `mtime=0`） |
| `scripts/bench_district_match.py` | 性能基准脚本（临时 USER_DATA_DIR；导入/预热/单条/3000 行批量，产出 §6 数字） |
| `blueprints/district_match.py` | Blueprint：15 条路由（见 §5） |
| `templates/admin/district_match.html` | 页面（继承 `base.html`，无内联脚本） |
| `static/js/district_match.js` | 页面交互（fetch + `showToast/showConfirm/safeJSON`） |
| `tests/test_district_match_unit.py` | 纯算法单测（FakeRepo，22 条，覆盖 12.2 #1–#13） |
| `tests/test_district_match_repo.py` | 仓库级测试（小数据包 fixture，10 条） |

## 2. 修改文件（主补丁 7 个 + fix1 1 个）

逐处 diff 见 `INTEGRATION_POINTS.md`（与 patch 内容一致）：

| 文件 | 改动 |
|---|---|
| `models/db.py` | 新增 `DISTRICT_DDL` / `DISTRICT_MATCH_DDL` / 16 条历史种子；`init_db()` 幂等新增 students 7 列、3 张 dm 表、种子写入 |
| `models/students.py` | 3 个写入口调用 `fill_district_fields`；INSERT 增加 7 列；`update_student` 仅在 `address`/`id_card` 变化时重算 |
| `config.py` | `ALL_STUDENT_FIELDS` / `FIELD_MAPPING` 增加 7 个中文字段名 |
| `app.py` | 注册 `district_match` Blueprint；`field_help['auto_fields']` 增加 7 项 |
| `templates/base.html` | “学生数据管理”下拉新增“区划匹配”入口 |
| `tests/conftest.py` | `_cleanup_db` 增加 `dm_match_result` / `dm_match_batch` |
| `tests/test_routes.py` | 路由总数上限 155 → 170（新增 15 条路由） |
| `run_with_tray.spec` | **（fix1 增量补丁）**数据文件收集段后新增区划数据包：缺文件 `SystemExit`，存在则收进 `_MEIPASS/data/`；未用 `os.walk(data/)` 避免带入运行时目录 |

---

## 3. DDL

### 3.1 `district.db`（独立库，不进备份，可由数据包重建）

```sql
CREATE TABLE IF NOT EXISTS dm_district_code (
    district_code TEXT NOT NULL,      -- 12 位，TEXT，保留前导零
    district_name TEXT NOT NULL,
    admin_level   INTEGER NOT NULL,   -- 1省 2市 3区县 4乡镇 5村
    parent_code   TEXT,
    province_code TEXT,
    city_code     TEXT,
    PRIMARY KEY (district_code)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS idx_dm_district_name   ON dm_district_code (district_name, admin_level);
CREATE INDEX IF NOT EXISTS idx_dm_district_parent ON dm_district_code (parent_code);
CREATE INDEX IF NOT EXISTS idx_dm_district_level  ON dm_district_code (admin_level);
```

### 3.2 `student.db`（3 张业务表）

```sql
CREATE TABLE IF NOT EXISTS dm_match_batch (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_no TEXT NOT NULL UNIQUE,
    source TEXT DEFAULT 'batch',            -- upload / students
    total INTEGER DEFAULT 0,
    status TEXT DEFAULT 'running',           -- running / done / failed
    stats_json TEXT DEFAULT '',
    created_by TEXT DEFAULT '',
    is_deleted INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS dm_match_result (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id INTEGER,                        -- 按需匹配/重新匹配为 NULL
    student_id INTEGER,
    student_name TEXT,
    id_card TEXT,
    address TEXT,
    province_code TEXT, province_name TEXT,
    city_code TEXT,     city_name TEXT,
    district_code TEXT, district_name TEXT,
    match_status TEXT,
    engine1_detail TEXT,
    engine2_detail TEXT,
    decision_path TEXT,
    source TEXT DEFAULT 'batch',             -- batch / upload / students / on_demand
    is_deleted INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_dm_result_batch   ON dm_match_result (batch_id);
CREATE INDEX IF NOT EXISTS idx_dm_result_student ON dm_match_result (student_id);
CREATE INDEX IF NOT EXISTS idx_dm_result_id_card ON dm_match_result (id_card);

CREATE TABLE IF NOT EXISTS dm_history_mapping (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    old_code TEXT, new_code TEXT, old_name TEXT, new_name TEXT,
    change_type TEXT, change_date TEXT, remark TEXT,
    is_deleted INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### 3.3 `students` 新增 7 列（只增列，不改其他字段）

```sql
ALTER TABLE students ADD COLUMN district_province_name TEXT DEFAULT '';
ALTER TABLE students ADD COLUMN district_province_code TEXT DEFAULT '';
ALTER TABLE students ADD COLUMN district_city_name     TEXT DEFAULT '';
ALTER TABLE students ADD COLUMN district_city_code     TEXT DEFAULT '';
ALTER TABLE students ADD COLUMN district_county_name   TEXT DEFAULT '';
ALTER TABLE students ADD COLUMN district_county_code   TEXT DEFAULT '';
ALTER TABLE students ADD COLUMN district_match_status  TEXT DEFAULT '';
```

> 代码列全部 TEXT，12 位、保留前导零；空串表示“未计算/未匹配”。

---

## 4. 幂等与事务

| 位置 | 幂等策略 |
|---|---|
| `init_db()` 增列 | 逐列 `try ALTER TABLE ... except sqlite3.OperationalError: pass`，重复启动不报错 |
| 3 张 dm 表 | 全部 `CREATE TABLE IF NOT EXISTS` / `CREATE INDEX IF NOT EXISTS` |
| 历史种子 16 条 | 仅当 `dm_history_mapping` 为空时写入；写入后立即 `commit`，避免后续 `migrate_*` 新开连接撞写锁 |
| `import_dictionary()` | 一次性独立连接（0.2 #1b 唯一例外）→ `BEGIN IMMEDIATE` → `DELETE FROM dm_district_code` → 分块 `executemany` → `COMMIT`；异常 `ROLLBACK` + 二次清空；完成后 `invalidate_cache()` |
| `backfill_students(only_empty=True)` | 只选 `district_county_code` 为空的学生，已有值不覆盖 |
| `ensure_student_district(refresh=False)` | 已有 students 7 列或历史结果时直接返回，不重复落库 |
| 缓存 | 进程级模块变量 + `threading.Lock`；字典导入后显式失效 |

**写边界（严格按 4.8.1 / 4.9）**

| 函数 | 写入目标 |
|---|---|
| `create_student` / `create_student_with_conn` / `update_student` | `students`（含 7 列），事务由调用方控制 |
| `fill_district_fields(_many)` | **纯内存**：只补 7 个键，不执行 SQL 写、不 commit、不 UPDATE |
| `get_student_district` | 只读（students 优先，其次 `dm_match_result`） |
| `ensure_student_district` / 路由 `rematch` | 只写 `dm_match_result`（`source='on_demand'`），**不回写 students** |
| `match_students` | 只写 `dm_match_result`；学生口径复用 `models.db._non_graduated_where()` |
| `backfill_students`（唯一批量写 students 入口） | 只 `UPDATE` 7 列，**不动 `updated_at`**、不写 `update_logs` |

---

## 5. 路由清单（Blueprint `district_match`，15 条）

| # | Method | Path | 鉴权 | 返回 |
|---|---|---|---|---|
| 1 | GET | `/admin/district-match` | `@auth.admin_required` | 页面 HTML（未登录 302 到登录） |
| 2 | POST | `/api/district-match/query` | admin JSON | `{success, data:{district_*, match_status, engine1_detail, engine2_detail, decision_path}}` |
| 3 | POST | `/api/district-match/preview` | admin JSON | `{success, data:{headers, rows(≤10), suggest:{id_card,address}, total_estimate}}` |
| 4 | POST | `/api/district-match/batch` | admin JSON | `{success, data:{progress_id, total}}`；重复提交 409 |
| 5 | GET | `/api/district-match/progress/<progress_id>` | 登录 JSON | `{success, data:{status, phase, done, total, message, result}}`；失效 404 |
| 6 | GET | `/api/district-match/batches?page&size` | 登录 JSON | `{success, data:{items, total, page}}` |
| 7 | GET | `/api/district-match/batches/<id>/results?page&size&status` | 登录 JSON | `{success, data:{batch, items, total, stats, page}}`；不存在 404 |
| 8 | POST | `/api/district-match/batches/<id>/delete` | admin JSON | `{success, data:{batch_id}}`（软删除批次与结果） |
| 9 | GET | `/api/district-match/batches/<id>/export?format=xlsx|csv` | 登录 JSON | 文件下载（`send_file`） |
| 10 | GET | `/api/district-match/dictionary?verify=1` | 登录 JSON | `{success, data:{db_path, db_exists, db_size, total, by_level, updated_at, cache, pack}}` |
| 11 | POST | `/api/district-match/dictionary/import` | admin JSON | `{success, data:{progress_id, pack}}`；并发 409 |
| 12 | GET | `/api/district-match/history` | 登录 JSON | `{success, data:{items, readonly:true}}` |
| 13 | POST | `/api/district-match/backfill` | admin JSON | `{success, data:{progress_id, limit, only_empty}}`；并发 409 |
| 14 | POST | `/api/district-match/students/<id>/rematch` | admin JSON | `{success, data:{district_*, match_status, batch_id, created_at, source}}` |
| 15 | GET | `/api/district-match/students/<id>` | 登录 JSON | `{success, data:{district:{...} | null}}` |

说明：
- 所有 POST 受全局 `before_request` CSRF 校验（`X-CSRF-Token` / `csrf_token`），未带令牌返回 403 JSON；
- `preview` 是为预览需求新增的路由，已在本文清单中显式列出，无隐式接口；
- 401/403 由 `_require_admin_json()` / `_require_login_json()` 统一返回 JSON。

---

## 6. 性能实测（Windows 10 / Python 3.10.10 / 本机 SSD）

| 项 | 实测 | 目标 |
|---|---|---|
| 数据包导入（664,239 行，含 3 个索引） | 6.21 s | 有进度、可接受 |
| `district.db` 体积 | 123,015,168 字节（117.3 MB） | 文档估 104.8 MB（见 IMPLEMENTATION_NOTES #2） |
| 首次缓存预热（L1–L4 + 历史） | 0.18 s / 44,903 行 + 16 条 | ≤ 3 s |
| 单条 `fill_district_fields`（全量字典，缓存命中） | 1.207 ms | ≤ 50 ms |
| **3000 行 `fill_district_fields_many`（全量字典）** | **0.044 s，0.015 ms/行** | **< 20 ms/行** |
| 3000 行（小数据包 fixture） | 0.036 s，0.012 ms/行 | < 20 ms/行 |

> 3000 行测试命令：`python -m pytest tests/test_district_match_repo.py::test_fill_many_perf -q -s`
> 全量字典基准脚本：`scripts/bench_district_match.py`（见 §8）

## 7. 数据包

| 项 | 值 |
|---|---|
| 文件 | `data/district_pack.tsv.gz` |
| 大小 | 5,411,508 字节（5.16 MiB；文档估 5.43 MB，见 IMPLEMENTATION_NOTES #1） |
| sha256 | `d1e60bbb0141da282410c618aefecddcb9d8962ab647c7b5ffabbac8451c05f4` |
| 总行数 | 664,239 |
| 各级行数 | L1=31 / L2=343 / L3=3255 / L4=41,274 / L5=619,336 |
| 列 | `district_code district_name admin_level parent_code province_code city_code`（6 列，与 4.5 一致） |
| 生成脚本 | `scripts/build_district_pack.py`（`--max-level 5` 默认；`4` 仅应急降级） |

复现：

```powershell
python scripts/build_district_pack.py --source database/district_matcher.db --out data/district_pack.tsv.gz
# 固定排序 + gzip mtime=0，同一源库重复生成 sha256 不变
```

## 8. 回滚

```sql
-- 1) student.db：删业务表（先生成备份）
DROP TABLE IF EXISTS dm_match_result;
DROP TABLE IF EXISTS dm_match_batch;
DROP TABLE IF EXISTS dm_history_mapping;

-- 2) students 7 列（SQLite ≥ 3.35 才支持 DROP COLUMN；否则用备份还原）
ALTER TABLE students DROP COLUMN district_province_name;
ALTER TABLE students DROP COLUMN district_province_code;
ALTER TABLE students DROP COLUMN district_city_name;
ALTER TABLE students DROP COLUMN district_city_code;
ALTER TABLE students DROP COLUMN district_county_name;
ALTER TABLE students DROP COLUMN district_county_code;
ALTER TABLE students DROP COLUMN district_match_status;
```

```powershell
# 3) 删字典库与数据包（可再生资产）
Remove-Item data/district.db, data/district.db-wal, data/district.db-shm -ErrorAction SilentlyContinue
Remove-Item data/district_pack.tsv.gz, data/district_pack.tsv.sha256 -ErrorAction SilentlyContinue
# 4) 逆向应用补丁（或直接 git checkout 到 89f330c）
git apply -R patches/district_match_89f330c_fix1.patch   # 先回滚 spec 与四份说明
git apply -R patches/district_match_89f330c.patch
```

> 建议回滚前先做一次 `student.db` 备份；`district.db` 不进系统备份，删除后可由数据包重建。

## 9. 未包含项（按文档约定）

- 未改 `utils.create_import_template()`（导入模板仍为硬编码 29 列）；
- 未写 `update_logs`；
- 未新增 pip 依赖、未用 pandas/numpy、未新增 CDN/端口/角色；
- 未在 `student.db` 建依赖 `dm.*` 的视图；
- 历史映射页只读（本期不做增删改）。
