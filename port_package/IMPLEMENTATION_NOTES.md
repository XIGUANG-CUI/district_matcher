# IMPLEMENTATION_NOTES —— 与 PORTING_GUIDE 的偏差记录

- 基准仓库：`StudentMessageSharePRO-v2` @ `89f330c0ee7845702eb6f0d720b5f3e950d5154b`
- 主补丁：`patches/district_match_89f330c.patch`（**28 文件，+4097 / -12** = 新增 20 个文件 + 新增 `data/district_pack.tsv.sha256` 1 个 + 修改 7 个；不含二进制数据包）
- 增量补丁：`patches/district_match_89f330c_fix1.patch`（**5 文件**：`run_with_tray.spec` + 本目录四份说明），基于「89f330c + 主补丁」工作树生成
- 依据：`PORTING_GUIDE.md` 只读，本文件只记录「实现与文档不一致或文档未覆盖」的部分

---

## 1. 偏差清单

| # | 项目 | 文档 | 实际实现 | 原因 / 处理 |
|---|------|------|----------|-------------|
| 1 | 数据包体积 | 5.43 MB | `data/district_pack.tsv.gz` = **5,411,508 字节（5.41 MB / 5.16 MiB）** | 内容与行数完全对齐（664,239 行；1:31 / 2:343 / 3:3255 / 4:41274 / 5:619336）；差异仅来自 gzip 压缩率（`mtime=0`、固定压缩级别）。sha256 见 `data/district_pack.tsv.sha256`。 |
| 2 | 字典库体积 | 104.8 MB | `district.db` 实测 **123,015,168 字节（123.0 MB / 117.3 MiB）** | 5 级全量入库 + `WITHOUT ROWID` + 3 索引 + 3 张 `dm_*` 表；磁盘成本仍在「独立文件、不进备份」的可接受范围，按用户拍板保留 5 级。 |
| 3 | 道外区代码 | 本地 PORTING_GUIDE 副本 4.8.1 与 12.2 #25/#26 仍写 `230105000000` | 一律输出 **`230104000000`（道外区）**；`230105` 仅作为输入码经历史映射转为 `230104` | 按用户第二轮答复：230104 是现行码，230105 是已撤销的太平区历史码。文档已由用户侧修订，实现以答复为准。 |
| 4 | `fill_district_fields` 的懒加载 | 文档只要求「纯内存补 7 键」 | 纯内存成立（不写 SQL、不 commit、不 UPDATE students），但**首次调用**会触发一次只读加载：`_load_cache()` 读 44,903 行（≤L4）+ `_load_history()` 读 16 行 | 缓存为进程级模块变量（`_CACHE` / `_HISTORY` + `threading.Lock`），非 `g`，故只发生一次；实测 0.18 s。建议接入后在 `app.py` 启动钩子里调用 `models.district.warm_cache()`，把这一次开销移出首个录入请求。 |
| 5 | `dm_match_result` 列 | 文档 DDL 未定义 `source` | 新增 `source TEXT DEFAULT 'batch'`，取值 `batch` / `upload` / `students` / `on_demand` | 用于区分 4.9 三接口与批量/导入来源，便于排查「谁写的这条结果」。默认值保证旧写入路径不需要改动。 |
| 6 | `init_db()` 历史种子 | 文档未涉及 | 写入 `dm_history_mapping` 种子后新增 `conn.commit()` | 不加提交时，后续 `migrate_attendance_status()` 等各自新开裸连接会撞上写锁（实测 `database is locked`，22 条测试全挂）。属于必要修复，不改变表结构。 |
| 7 | 导出目录 | 4.x 要求「导出文件落在 `_USER_DATA_DIR/exports`」，且禁用 `config._USER_DATA_DIR` | 由 `os.path.dirname(current_app.config["UPLOAD_FOLDER"])` 拼出 `exports`，并 `makedirs(exist_ok=True)` | `UPLOAD_FOLDER` 本身就在 `_USER_DATA_DIR` 下，父目录即 `_USER_DATA_DIR`，既不引用私有变量，也满足落盘位置。 |
| 8 | 路由数量 | 文档未给总数 | 新增 15 条（清单见 CHANGES.md 第 3 节，含为预览需求新增的 `POST /api/district-match/preview`） | `tests/test_routes.py` 的上限断言从 155 调整为 `155 <= count <= 170`，docstring 同步为「约 163（148 原有 + 15 区划匹配）」。无清单之外的隐式接口。 |
| 9 | 额外交付脚本 | 文档只要求 `build_district_pack.py` | 另附 `scripts/bench_district_match.py` | 让「3000 行导入耗时」「单条耗时」「dict.db 体积」可复现；不参与运行期，不影响仓库行为。 |
| 10 | `importer.import_pack` 入参 | 文档伪代码给的是可迭代 DDL | 同时兼容 `str`（按 `;` 拆分执行）与可迭代语句序列 | `DISTRICT_DDL` 是三段建表/建索引拼接的多语句字符串，直接当单条执行会报 `near "C": syntax error`。 |
| 11 | 数据包分发方式 | 要求「新增文件 + git diff」 | patch 内含 `data/district_pack.tsv.sha256`，**不含** `data/district_pack.tsv.gz`（二进制） | patch 保持纯文本可读可审；数据包单独随交付目录提供，落位 `data/district_pack.tsv.gz` 后按 sha256 校验。 |
| 12 | PyInstaller 打包缺数据包（P0 修复） | 文档未覆盖打包 | `run_with_tray.spec` 在静态资源段后新增数据包收集：缺文件直接 `SystemExit`，存在则 `datas.append((pack_path, 'data'))` | 打包后 `BASE_DIR` = `_internal/`，首选路径 `_internal/data/district_pack.tsv.gz` 由该 datas 落在 `_MEIPASS/data/`。未用 `os.walk(data/)` / `collect_data_files('data')`，避免把 `data/templates/`、`exports/` 等运行时目录打进包体。`_PACK_CANDIDATES` 第二候选 `user_data/data/` 保留。 |

---

## 2. 与用户三点修正的对应实现

| 修正点 | 落点 | 说明 |
|--------|------|------|
| ① ATTACH 每请求一次，缓存不得放 `g` | `models/district.py` `ensure_attached()` | 用 `g._dm_attached_conn` 记录「本请求已 ATTACH 的连接」，同一连接重复调用直接返回；新请求 `get_db()` 新连接则重新 ATTACH（实测 1–3 ms）。`_CACHE` / `_HISTORY` / `_DICT_STATE` 全为模块级变量 + 独立 `threading.Lock`。 |
| ② `fill_district_fields` 纯内存 | `models/district.py:591` | 只 `setdefault` 7 键 + `data.update(...)`；不执行 INSERT/UPDATE/COMMIT；异常一律吞掉并留空（`logger.warning`）。事务边界在 `create_student` / `create_student_with_conn` / `update_student`。 |
| ③ 批量导入 `fill_district_fields_many` | `models/district.py:617` | 一次 `DistrictRepository`（一次 ATTACH）、一次 `_load_cache` + `_load_history`、循环 `runner.run_match`。实测 3000 行 **0.044 s（0.015 ms/行）**，远低于 20 ms/行门槛；见 CHANGES.md 性能表与 `scripts/bench_district_match.py`。 |

---

## 3. 写边界复核

| 入口 | 写 `students` | 写 `dm_match_result` | 代码位置 |
|------|--------------|----------------------|----------|
| `create_student` / `create_student_with_conn` / `update_student` | 是（7 列，随 INSERT/UPDATE） | 否 | `models/students.py` |
| `get_student_district` | 否 | 否（只读） | `models/district.py:654` |
| `ensure_student_district` | **否** | 是（`source='on_demand'`） | `models/district.py:702` |
| `match_students` | **否** | 是（`source='students'`，学生口径 `_non_graduated_where()`） | `models/district.py:737` |
| 「批量补全区划」按钮 → `backfill_students` | 是（仅 UPDATE 7 列，不动 `updated_at`） | 否 | `models/district.py:763` |
| 上传/导入匹配 | 否 | 是（`source='upload'`） | `services/district_match/runner.py` |

`update_student` 只在 `id_card` 或 `address` 实际变化时重算区划列，其余更新不会覆盖已有 7 列。

---

## 4. 接入注意事项

1. **先导字典**：首次部署需执行 `python scripts/build_district_pack.py`（已有包可跳过）与「字典导入」动作，否则 `district.db` 不存在，`fill_district_fields` 会静默留空（符合「字典缺失不报错」验收点，但录入不会有区划）。
2. **预热**：建议在 `app.py` 启动后调用 `from models.district import warm_cache; warm_cache()`，避免首个录入请求承担 0.18 s 加载。
3. **数据包落位（打包必需）**：`data/district_pack.tsv.gz` 需放入仓库 `data/`（patch 不含二进制），`.gitignore` 只忽略 `*.db` 与 `data/exports/`，`.gz` 可正常 `git add`；干净 clone 后 `pyinstaller run_with_tray.spec` 会把它收进 `_MEIPASS/data/`。可用 `certutil -hashfile data\district_pack.tsv.gz SHA256` 与 `data/district_pack.tsv.sha256` 对照。
4. **静态资源无 CDN**：页面只引用本地 `static/js/district_match.js` 与既有 `base.html`，无外链。
5. **后台任务单进程**：批量匹配 / 导入的进度表在内存（`_progress`），多进程部署（gunicorn 多 worker）下轮询可能打到别的 worker；本仓库现状为单进程，未额外引入队列依赖。

---

## 5. 未被采纳 / 未实现项

- 未在 `student.db` 建任何 `dm.*` 视图（遵循约束）。
- 未引入新 pip 依赖、未使用 pandas/numpy（xlsx 读写用 openpyxl，仓库既有依赖）。
- 未修改 `PORTING_GUIDE.md`。
- 未改动 `students` 表 7 列之外的任何列定义。

---

## 6. fix1 增量补丁说明（P0 打包修复）

| 项 | 内容 |
|---|---|
| 文件 | `patches/district_match_89f330c_fix1.patch` |
| 基线 | 「89f330c + 主补丁」的工作树 |
| 内容 | 5 个文件：`run_with_tray.spec`（改）+ `CHANGES.md` / `INTEGRATION_POINTS.md` / `SELFTEST.md` / `IMPLEMENTATION_NOTES.md`（新增，仓库根目录） |
| 未改动 | `models/`、`services/`、`blueprints/`、`config.py`、`app.py` 一律未动（除 spec） |
| 打包验证 | 见 SELFTEST.md §1.7：onedir 产物 `_internal/data/district_pack.tsv.gz` 存在（5,411,508 B，sha256 一致），自动发现路径导入 664,239 行 |

> `run_with_tray.spec` 只有 onedir（`COLLECT`）一个目标，无 onefile 目标，故只需验证一个产物。
