# INTEGRATION_POINTS.md — 接入点清单与 diff

基准：`StudentMessageSharePRO-v2 @ 89f330c0ee7845702eb6f0d720b5f3e950d5154b`（2026-10-23）
完整补丁：[patch 目录] `patches/district_match_89f330c.patch`（可直接 `git apply`，已 `--check` 通过）
增量补丁：`patches/district_match_89f330c_fix1.patch`（`run_with_tray.spec` + 四份说明，基于「89f330c + 主补丁」）

推荐接入步骤：

```powershell
# 仓库根目录
git apply --whitespace=nowarn <交付目录>/patches/district_match_89f330c.patch
git apply <交付目录>/patches/district_match_89f330c_fix1.patch
Copy-Item <交付目录>/data/district_pack.tsv.gz         data/district_pack.tsv.gz
Copy-Item <交付目录>/data/district_pack.tsv.sha256     data/district_pack.tsv.sha256
python -m pytest -q
```

> 数据包是二进制文件，未放进 patch；`data/district_pack.tsv.sha256` 为校验清单。
> `docs/PORTING_GUIDE.md` 未入库、不在 patch 内。

---

## 1. `app.py`（2 处）

```diff
@@ -55,6 +55,8 @@ app.register_blueprint(leave_cancel_bp)
 app.register_blueprint(lifecycle_bp)
 from blueprints.api import bp as api_bp
 app.register_blueprint(api_bp)
+from blueprints.district_match import bp as district_match_bp
+app.register_blueprint(district_match_bp)

 # ========== 安全防护 ==========

@@ -124,7 +126,8 @@ def _inject_field_help():
         'import_fields': list(config.ALL_STUDENT_FIELDS),
         'import_count': len(config.ALL_STUDENT_FIELDS),
         'required_fields': ['学生姓名', '学生身份证号', '学号'],
-        'auto_fields': ['年龄', '姓名拼音'],
+        'auto_fields': ['年龄', '姓名拼音', '省', '省区划代码', '市', '市区划代码',
+                        '县', '县区划代码', '匹配状态'],
         'is_admin': user_role == 'admin',
         'insurance_fields': ['学平险保单号', '学平险缴费日期', '学平险到期时间',
                              '学平险保费金额', '实习险缴费日期', '主管标记',
```

## 2. `config.py`（2 处）

```diff
@@ -151,7 +151,9 @@ ALL_STUDENT_FIELDS = [
     '入学年份',
     '姓名拼音', '学平险保单号', '学平险缴费日期', '学平险到期时间',
     '学平险保费金额', '实习险缴费日期', '主管标记', '申报日期',
-    '保单更新日期', '学费总体情况'
+    '保单更新日期', '学费总体情况',
+    # 区划匹配 7 列：系统自动生成，不进导入模板（导入侧忽略用户填值）
+    '省', '省区划代码', '市', '市区划代码', '县', '县区划代码', '匹配状态'
 ]

 FIELD_MAPPING = {
@@ -194,6 +196,14 @@ FIELD_MAPPING = {
     '申报日期': 'declaration_date',
     '保单更新日期': 'insurance_update_date',
     '学费总体情况': 'tuition_status',
+    # 区划匹配 7 列（自动生成；导出引擎白名单由本映射派生）
+    '省': 'district_province_name',
+    '省区划代码': 'district_province_code',
+    '市': 'district_city_name',
+    '市区划代码': 'district_city_code',
+    '县': 'district_county_name',
+    '县区划代码': 'district_county_code',
+    '匹配状态': 'district_match_status',
     '学校类别': 'school',
 }
```

> 导入模板 `utils.create_import_template()` 未改（硬编码 29 列），因此 7 列不会出现在模板中。

## 3. `models/db.py`（2 处）

### 3.1 模块级 DDL 与历史种子（插在 `logger = ...` 之后）

```diff
@@ -18,6 +18,110 @@ from config import DATABASE_PATH, PHOTOS_DIR
 logger = logging.getLogger(__name__)


+# ============================================================
+# 区划字典（district.db）DDL —— 由 models.district.import_dictionary 执行
+# 该库独立于 student.db，不参与备份；运行期只读，仅导入时一次性写入
+# ============================================================
+DISTRICT_DDL = """
+CREATE TABLE IF NOT EXISTS dm_district_code (
+    district_code TEXT NOT NULL,
+    district_name TEXT NOT NULL,
+    admin_level   INTEGER NOT NULL,
+    parent_code   TEXT,
+    province_code TEXT,
+    city_code     TEXT,
+    PRIMARY KEY (district_code)
+) WITHOUT ROWID;
+CREATE INDEX IF NOT EXISTS idx_dm_district_name
+    ON dm_district_code (district_name, admin_level);
+CREATE INDEX IF NOT EXISTS idx_dm_district_parent
+    ON dm_district_code (parent_code);
+CREATE INDEX IF NOT EXISTS idx_dm_district_level
+    ON dm_district_code (admin_level);
+"""
+
+
+# ============================================================
+# 区划匹配业务表（student.db）—— 批次 / 结果 / 历史映射
+# 幂等：全部 CREATE TABLE IF NOT EXISTS
+# ============================================================
+DISTRICT_MATCH_DDL = """
+CREATE TABLE IF NOT EXISTS dm_match_batch (
+    id INTEGER PRIMARY KEY AUTOINCREMENT,
+    batch_no TEXT NOT NULL UNIQUE,
+    source TEXT DEFAULT 'batch',
+    total INTEGER DEFAULT 0,
+    status TEXT DEFAULT 'running',
+    stats_json TEXT DEFAULT '',
+    created_by TEXT DEFAULT '',
+    is_deleted INTEGER DEFAULT 0,
+    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
+);
+
+CREATE TABLE IF NOT EXISTS dm_match_result (
+    id INTEGER PRIMARY KEY AUTOINCREMENT,
+    batch_id INTEGER,
+    student_id INTEGER,
+    student_name TEXT,
+    id_card TEXT,
+    address TEXT,
+    province_code TEXT,
+    province_name TEXT,
+    city_code TEXT,
+    city_name TEXT,
+    district_code TEXT,
+    district_name TEXT,
+    match_status TEXT,
+    engine1_detail TEXT,
+    engine2_detail TEXT,
+    decision_path TEXT,
+    source TEXT DEFAULT 'batch',
+    is_deleted INTEGER DEFAULT 0,
+    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
+);
+
+CREATE INDEX IF NOT EXISTS idx_dm_result_batch
+    ON dm_match_result (batch_id);
+CREATE INDEX IF NOT EXISTS idx_dm_result_student
+    ON dm_match_result (student_id);
+CREATE INDEX IF NOT EXISTS idx_dm_result_id_card
+    ON dm_match_result (id_card);
+
+CREATE TABLE IF NOT EXISTS dm_history_mapping (
+    id INTEGER PRIMARY KEY AUTOINCREMENT,
+    old_code TEXT,
+    new_code TEXT,
+    old_name TEXT,
+    new_name TEXT,
+    change_type TEXT,
+    change_date TEXT,
+    remark TEXT,
+    is_deleted INTEGER DEFAULT 0,
+    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
+);
+"""
+
+# 历史区划映射内置种子（16 条）：仅当表为空时写入一次
+HISTORY_MAPPING_SEED = [
+    ("230105", "230104", "太平区", "道外区", "撤销合并", "2004", "太平区2004年并入道外区"),
+    ("231121", "231183", "嫩江县", "嫩江市", "撤县设市", "2019", "嫩江县2019年撤县设市"),
+    ("231024", "231086", "东宁县", "东宁市", "撤县设市", "2015", "东宁县2015年撤县设市"),
+    ("232723", "232701", "漠河县", "漠河市", "撤县设市", "2018", "漠河县2018年撤县设市"),
+    ("230182", "230113", "双城市", "双城区", "撤市设区", "2014", "双城市2014年撤市设区"),
+    ("210282", "210214", "普兰店市", "普兰店区", "撤市设区", "2015", "普兰店市2015年撤市设区"),
+    ("310230", "310151", "崇明县", "崇明区", "撤县设区", "2016", "崇明县2016年撤县设区"),
+    ("320584", "320509", "吴江市", "吴江区", "撤市设区", "2012", "吴江市2012年撤市设区"),
+    ("320124", "320117", "溧水县", "溧水区", "撤县设区", "2013", "溧水县2013年撤县设区"),
+    ("320125", "320118", "高淳县", "高淳区", "撤县设区", "2013", "高淳县2013年撤县设区"),
+    ("320323", "320312", "铜山县", "铜山区", "撤县设区", "2010", "铜山县2010年撤县设区"),
+    ("330621", "330603", "绍兴县", "柯桥区", "撤县设区", "2013", "绍兴县2013年撤县设区（柯桥区）"),
+    ("500227", "500120", "璧山县", "璧山区", "撤县设区", "2014", "璧山县2014年撤县设区"),
+    ("440183", "440118", "增城市", "增城区", "撤市设区", "2014", "增城市2014年撤市设区"),
+    ("320482", "320413", "金坛市", "金坛区", "撤市设区", "2015", "金坛市2015年撤市设区"),
+    ("321088", "321012", "江都市", "江都区", "撤市设区", "2011", "江都市2011年撤市设区"),
+]
```

### 3.2 `init_db()` 内新增（在 `new_columns_v2` 迁移之后）

```diff
@@ -160,6 +264,40 @@ def init_db():
             cursor.execute(f'ALTER TABLE students ADD COLUMN {col_name} {col_def}')
         except sqlite3.OperationalError:
             pass
+
+    # ========== 区划匹配：students 7 列（只增列，不改其他字段）==========
+    # 代码列必须 TEXT（12 位、保留前导零），默认空串表示未匹配
+    district_columns = [
+        ('district_province_name', "TEXT DEFAULT ''"),
+        ('district_province_code', "TEXT DEFAULT ''"),
+        ('district_city_name', "TEXT DEFAULT ''"),
+        ('district_city_code', "TEXT DEFAULT ''"),
+        ('district_county_name', "TEXT DEFAULT ''"),
+        ('district_county_code', "TEXT DEFAULT ''"),
+        ('district_match_status', "TEXT DEFAULT ''"),
+    ]
+    for col_name, col_def in district_columns:
+        try:
+            cursor.execute(f'ALTER TABLE students ADD COLUMN {col_name} {col_def}')
+        except sqlite3.OperationalError:
+            pass
+
+    # ========== 区划匹配：批次 / 结果 / 历史映射表 ==========
+    for statement in DISTRICT_MATCH_DDL.split(';'):
+        if statement.strip():
+            cursor.execute(statement)
+
+    # 历史映射种子：仅当表为空时写入一次，之后以表内记录为准
+    try:
+        if cursor.execute('SELECT COUNT(1) FROM dm_history_mapping').fetchone()[0] == 0:
+            cursor.executemany(
+                'INSERT INTO dm_history_mapping '
+                '(old_code, new_code, old_name, new_name, change_type, change_date, remark) '
+                'VALUES (?, ?, ?, ?, ?, ?, ?)', HISTORY_MAPPING_SEED)
+    except sqlite3.OperationalError:
+        pass
+    # 立即提交种子事务，避免后续 migrate_* 新开连接时撞上写锁
+    conn.commit()
```

> `conn.commit()` 是必要的：`init_db()` 随后会调用 `migrate_attendance_status/dates/id_card`，它们各自 `get_db()`；若种子 INSERT 的事务未提交，在无 app context 场景（`init_db()` 直接返回裸连接）会触发 `database is locked`（已实测并修复）。

## 4. `models/students.py`（3 个写入口）

```diff
@@ -3,6 +3,7 @@ import re
 import logging
 from datetime import datetime, timedelta
 from models.db import get_db, _exclude_graduated_clause
+from models.district import fill_district_fields, DISTRICT_FIELDS
 from models.logs import update_sync_timestamp
 from models.school_types import get_school_type_map

@@ -38,7 +39,10 @@ def create_student(student_data):
             existing = get_student_by_id_card(id_card)
             if existing:
                 raise ValueError(f'身份证号 {id_card} 已存在')
-
+
+        # 区划联动：就地补 7 键（纯内存、不抛异常；事务仍由本函数控制）
+        fill_district_fields(student_data, conn)
+
         cursor.execute('''
             INSERT INTO students (student_no, national_no, id_card, name, gender, birth_date,
                                   ethnicity, address, major, admin_class, virtual_class,
@@ -47,9 +51,12 @@ def create_student(student_data):
                                   issue_authority, valid_start, valid_end, major_direction,
                                   household_type, junior_school, admission_type,
                                   junior_graduation_time, change_info, remark, enrollment_year,
-                                  name_pinyin, created_at)
+                                  name_pinyin, district_province_name, district_province_code,
+                                  district_city_name, district_city_code, district_county_name,
+                                  district_county_code, district_match_status, created_at)
             VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
-                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now', 'localtime'))
+                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
+                    datetime('now', 'localtime'))
         ''', (
@@ -81,7 +88,14 @@ def create_student(student_data):
             _to_null_if_empty(student_data.get('change_info')),
             _to_null_if_empty(student_data.get('remark')),
             student_data.get('enrollment_year', 0),
-            student_data.get('name_pinyin', '')
+            student_data.get('name_pinyin', ''),
+            student_data.get('district_province_name', ''),
+            student_data.get('district_province_code', ''),
+            student_data.get('district_city_name', ''),
+            student_data.get('district_city_code', ''),
+            student_data.get('district_county_name', ''),
+            student_data.get('district_county_code', ''),
+            student_data.get('district_match_status', '')
         ))
```

`create_student_with_conn` 的 3 处改动与上完全同构（调用 `fill_district_fields(student_data, conn)` + INSERT 列/占位符/取值各加 7 项），见 patch 第 1633–1671 行。

```diff
@@ -282,7 +309,28 @@ def update_student(student_id, student_data):
             existing = get_student_by_id_card(id_card)
             if existing and existing.get('id') != student_id:
                 raise ValueError(f'身份证号 {id_card} 已存在')
-
+
+        # 区划联动：仅当身份证/地址发生变化时重算 7 列（失败留空，不阻断更新）
+        try:
+            current = get_student_by_id(student_id) or {}
+            id_changed = ('id_card' in student_data and
+                          _to_null_if_empty(student_data.get('id_card'))
+                          != _to_null_if_empty(current.get('id_card')))
+            addr_changed = ('address' in student_data and
+                            _to_null_if_empty(student_data.get('address'))
+                            != _to_null_if_empty(current.get('address')))
+            if id_changed or addr_changed:
+                source = {
+                    'id_card': student_data.get('id_card', current.get('id_card')),
+                    'address': student_data.get('address', current.get('address')),
+                }
+                fill_district_fields(source, conn)
+                for key in DISTRICT_FIELDS:
+                    student_data[key] = source.get(key, '')
+                allowed_fields = allowed_fields + list(DISTRICT_FIELDS)
+        except Exception as exc:
+            logger.warning('区划联动失败，本次更新跳过区划列：%s', exc)
+
         updates = []
```

> `update_student` 的 `allowed_fields` 原本不含 7 列；仅在“身份证/地址确实变化”时才动态追加，因此“只改电话”不会重算、也不会覆盖原区划值（12.2 #27）。
> 7 列通过原有动态 `UPDATE` 写入，**不改 `update_logs`**。

## 5. `templates/base.html`（1 处）

```diff
@@ -62,6 +62,9 @@
                             <li><a class="dropdown-item" href="{{ url_for('lifecycle.admin_graduation_query') }}">
                                 <i class="bi bi-search"></i> 毕业生查询
                             </a></li>
+                            <li><a class="dropdown-item" href="{{ url_for('district_match.page') }}">
+                                <i class="bi bi-geo-alt-fill"></i> 区划匹配
+                            </a></li>
                             <li><hr class="dropdown-divider"></li>
```

## 6. `tests/conftest.py`（1 处）

```diff
@@ -59,6 +59,8 @@ def _cleanup_db(db_conn):
         'attendance_records', 'attendance_dates', 'update_logs',
         'export_history', 'export_field_mappings', 'export_templates',
         'dicts', 'students', 'users',
+        # 区划匹配结果表（幂等清空；字典历史映射种子不动）
+        'dm_match_result', 'dm_match_batch',
     ]
```

## 7. `tests/test_routes.py`（1 处）

```diff
@@ -50,10 +50,10 @@ class TestBlueprintRegistration:
     def test_route_count(self, app):
-        """路由总数约 148（144 Blueprint + 4 app.py）。"""
+        """路由总数约 163（148 原有 + 15 区划匹配）。"""
         from app import app as flask_app
         count = len(list(flask_app.url_map.iter_rules()))
-        assert 145 <= count <= 155
+        assert 155 <= count <= 170
```

> 该断言是路由总数的护栏；本期新增 15 条路由，故同步调整上限（实测 170）。

---

## 8. `run_with_tray.spec`（fix1 增量补丁，1 处）

基线：89f330c + 主补丁。位置：第 2 段「静态资源」之后、第 3 段「SDK 文件」之前。

```diff
@@ -72,6 +72,12 @@ if os.path.exists(static_dir):
         for file in files:
             src = os.path.join(root, file)
             rel = os.path.relpath(root, base_path)
             datas.append((src, rel))

+# 区划数据包（约 5.16 MiB）。缺失直接让打包失败，避免产出无字典的 EXE
+pack_path = os.path.join(base_path, 'data', 'district_pack.tsv.gz')
+if not os.path.exists(pack_path):
+    raise SystemExit(f'[spec] 缺少区划数据包：{pack_path}')
+datas.append((pack_path, 'data'))
+
 # 3. SDK 文件（读卡器 DLL + license.dat）
 if os.path.exists(sdk_dir):
     for file in os.listdir(sdk_dir):
         datas.append((os.path.join(sdk_dir, file), 'sdk'))
```

说明：

- 打包后 `BASE_DIR` = `_internal/`，该 datas 落到 `_MEIPASS/data/district_pack.tsv.gz`，正好是 `_PACK_CANDIDATES[0]`；
- 只收单个文件，不用 `os.walk(data/)` / `collect_data_files('data')`，避免把 `data/templates/`、`data/exports/` 等运行时目录打进包体；
- 缺包时 `SystemExit` 让打包立即失败，不会产出「能装但没字典」的 EXE；
- 若需免重打包换字典，把新包放到 EXE 同级 `user_data/data/district_pack.tsv.gz` 即可（`_PACK_CANDIDATES[1]`，保留）。

---

## 9. 接入后自检清单

1. `python -m pytest -q` → 125 passed（既有 93 + 新增 32）；
2. 启动应用，admin 登录 → “学生数据管理 → 区划匹配”；
3. “字典状态”页确认 `district.db` 未导入 → 点“导入数据包”，等待进度完成（约 6 s）；
4. 单条查询：`230105...` + `黑龙江省哈尔滨市道外区水泥路99-4号` → 道外区 `230104000000`；
5. 新增学生带地址 → `students` 7 列有值；字典未导入时新增 → 7 列为空但保存成功；
6. 打包：`python -m PyInstaller run_with_tray.spec --noconfirm` → 产物 `_internal/data/district_pack.tsv.gz` 存在，页面“导入数据包”可完成 664,239 行。
