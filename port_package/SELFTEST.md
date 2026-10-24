# SELFTEST.md — 测试命令、实际输出与 12.2 全量用例表

测试环境：Windows 10 / Python 3.10.10 / pytest 9.1.1 / PyInstaller 6.16.0
测试方式：把补丁应用到一个 `89f330c` 的干净工作树，复制数据包后执行（结果可复现）；§1.7 的 fix1 在「89f330c + 主补丁」工作树上验证。

---

## 1. 命令与输出

### 1.1 补丁可应用性（28 文件，+4097 / -12）

```powershell
git worktree add --detach C:\tmp\dm_v2_verify 89f330c0ee7845702eb6f0d720b5f3e950d5154b
git -C C:\tmp\dm_v2_verify apply --check patches/district_match_89f330c.patch
git -C C:\tmp\dm_v2_verify apply patches/district_match_89f330c.patch
```

```
PATCH_APPLY_CHECK_OK
PATCH_APPLIED
```

> 补丁为 LF 行尾、无 BOM，应用时无 whitespace 警告；`data/district_pack.tsv.gz` 为二进制，不含在 patch 内，需另行复制。

### 1.2 纯算法单测（FakeRepo，不依赖 Flask/models）

```powershell
python -m pytest tests/test_district_match_unit.py -q
```

```
........................                                       [100%]
22 passed in 0.54s
```

### 1.3 仓库级测试（真实 SQLite + 目标 conftest）

```powershell
python -m pytest tests/test_district_match_repo.py -q -s
```

```
..........                                                     [100%]
3000 行耗时 0.036s，单行 0.012 ms
10 passed in 0.59s
```

### 1.4 全量回归

```powershell
python -m pytest -q
```

```
tests/test_auth.py .....................                         [ 16%]
tests/test_district_match_repo.py ..........                     [ 24%]
tests/test_district_match_unit.py ......................         [ 42%]
tests/test_models.py ..................                          [ 56%]
tests/test_routes.py ...........                                 [ 65%]
tests/test_utils.py ...........................................  [100%]
125 passed in 1.30s
```

拆分：既有 **93** 条（`test_auth` 21 + `test_models` 18 + `test_routes` 11 + `test_utils` 43）+ 新增 **32** 条（`test_district_match_unit` 22 + `test_district_match_repo` 10）= **125**，无跳过（`pytest -q` 无 `s` 标记）。

### 1.5 全量字典性能基准

```powershell
python scripts/bench_district_match.py --pack data/district_pack.tsv.gz
```

```
import_seconds=6.21 total=664239 by_level={1: 31, 2: 343, 3: 3255, 4: 41274, 5: 619336}
warm_seconds=0.18 rows=44903 history_rows=16
many_3000_seconds=0.044 per_row_ms=0.015 hits=3000
single_row_ms=1.207
district_db_bytes=123015168
```

### 1.6 数据包校验

```powershell
python scripts/build_district_pack.py --source database/district_matcher.db --out data/district_pack.tsv.gz
Get-FileHash data/district_pack.tsv.gz -Algorithm SHA256
```

```
数据包：data/district_pack.tsv.gz
sha256：d1e60bbb0141da282410c618aefecddcb9d8962ab647c7b5ffabbac8451c05f4
大小  ：5411508 字节（5.16 MB）
行数  ：664239
各级  ：{1: 31, 2: 343, 3: 3255, 4: 41274, 5: 619336}
```

### 1.7 PyInstaller 打包验证（fix1 增量补丁）

前置：数据包已放在仓库 `data/district_pack.tsv.gz`；`run_with_tray.spec` 只有 onedir（`COLLECT`）一个目标，无 onefile 目标，故只需验证一个产物。

```powershell
python -m PyInstaller run_with_tray.spec --noconfirm
```

```
11763 INFO: Building EXE from EXE-00.toc completed successfully.
12643 INFO: Building COLLECT COLLECT-00.toc completed successfully.
12656 INFO: Build complete! The results are available in: <仓库>\dist
```

产物检查：

```powershell
Get-ChildItem dist -Recurse -Filter district_pack.tsv.gz | Select-Object FullName,Length
Get-FileHash "dist\学生数据管理系统1.0.0.7\_internal\data\district_pack.tsv.gz" -Algorithm SHA256
```

```
FullName : ...\dist\学生数据管理系统1.0.0.7\_internal\data\district_pack.tsv.gz
Length   : 5411508
SHA256   : d1e60bbb0141da282410c618aefecddcb9d8962ab647c7b5ffabbac8451c05f4
```

自动发现 + 字典导入（把 `config.BASE_DIR` 指到 `_internal`，走 `pack_path()` 第一候选，调用与「字典导入」按钮相同的 `import_dictionary()`）：

```
PACK_PATH=...\dist\学生数据管理系统1.0.0.7\_internal\data\district_pack.tsv.gz
IMPORT_OK total=664239 by_level={1: 31, 2: 343, 3: 3255, 4: 41274, 5: 619336}
DICT_DB=...\user_data\data\district.db BYTES=123015168
```

> onedir 产物的 `_MEIPASS` 即 `_internal/`，该文件即运行期首选路径 `BASE_DIR/data/district_pack.tsv.gz`；`_PACK_CANDIDATES` 第二候选 `user_data/data/` 保留，用于免重打包替换字典。本项为命令行验证，未做 GUI 自动化点击。

缺包快速失败（把 spec 复制到无 `data/` 的空目录执行）：

```powershell
python -m PyInstaller run_with_tray.spec --noconfirm
```

```
[spec] 缺少区划数据包：C:\Users\...\dm_specfail_5ff512\data\district_pack.tsv.gz
EXIT=1
```

---

## 2. 12.2 全部 31 条：输入 → 期望输出

> #1–#13 为纯算法用例（`tests/test_district_match_unit.py`，FakeRepo 固定字典）；
> #14–#24 为集成/接口用例；#25–#31 为录入联动用例（4.8.1）。
> 代码列均为 12 位 TEXT，下表省略前导零以外的格式说明。

| # | 输入（身份证 / 地址） | 期望输出（省 / 市 / 区县 / 状态） | 覆盖方式 |
|---|---|---|---|
| 1 | `230105...` + `黑龙江省哈尔滨市道外区水泥路99-4号` | 黑龙江省 / 哈尔滨市 / 道外区 `230104000000` / **正确**（引擎①历史码 230105→230104） | 自动 `test_case01_history_code_plus_address` |
| 2 | `230105...` + `哈尔滨市道外区...`（缺省名） | 黑龙江省 / 哈尔滨市 / 道外区 `230104000000` / **正确** | 自动 `test_case02_address_without_province` |
| 3 | `230105...` + 空地址 | 黑龙江省 / 哈尔滨市 / 道外区 `230104000000` / **已解析(仅证件)** | 自动 `test_case03_id_only` |
| 4 | 空 + `黑龙江省哈尔滨市道外区...` | 黑龙江省 / 哈尔滨市 / 道外区 `230104000000` / **已解析** | 自动 `test_case04_address_only` |
| 5 | 空 + `北京市海淀区中关村大街1号` | 北京市 / 北京市 `110000000000` / 海淀区 `110108000000` / **已解析** | 自动 `test_case05_municipality` |
| 6 | `abc123` + 空地址 | 全空 / **异常**（身份证格式无效且地址为空） | 自动 `test_case06_invalid_id_only` |
| 7 | `abc123` + 道外区地址 | 黑龙江省 / 哈尔滨市 / 道外区 `230104000000` / **已解析** | 自动 `test_case07_invalid_id_valid_address` |
| 8 | 空 + 空 | 全空 / **异常**（决策路径含“未输入”） | 自动 `test_case08_both_empty` |
| 9 | `150722...`（内蒙古）+ `黑龙江省嫩江市中央街1号` | 黑龙江省 / 黑河市 / 嫩江市 `231183000000` / **需复核**（不得混入内蒙古/呼伦贝尔） | 自动 `test_case09_conflict_needs_review` |
| 10 | `640425...` + `宁夏彭阳县白阳镇1号` | 宁夏回族自治区 / 固原市 / 彭阳县 `640425000000` / **正确** | 自动 `test_case10_autonomous_region_short_name` |
| 11 | 空 + `黑龙江省黑河市嫩江市中央街1号` | 黑龙江省 / 黑河市 / 嫩江市 `231183000000` / **已解析** | 自动 `test_case11_county_level_city` |
| 12 | 地址含 `...道外区水泥路社区1号` | 区县=道外区（“社区”不得被识别为区县） | 自动 `test_case12_community_not_county` |
| 13 | `莫力达瓦达斡尔族自治旗红彦镇新多村` | 内蒙古自治区 / 呼伦贝尔市 / 莫力达瓦达斡尔族自治旗 `150722000000`；乡镇=红彦镇、村=新多村 | 自动 `test_case13_township_village` |
| 14 | 导入数据包 | total=664,239；L1=31 / L2=343 / L3=3255 / L4=41,274 / L5=619,336；sha256 匹配 | 自动（小包 `test_pack_counts`）+ 手工（全量见 §1.5/§1.6） |
| 15 | 批量匹配 3 条 | 批次 `total=3`，`stats["总计"]=3`，无“异常”以外意外状态 | 手工：页面批量匹配后查看批次详情 |
| 16 | 删除批次（软删除） | `dm_match_batch.is_deleted=1`、其 `dm_match_result.is_deleted=1`；列表不再显示；`students` 不变 | 手工：批次记录 → 删除，再查 `students` 行数与 7 列 |
| 17 | 导出 xlsx / csv | 10 列中文表头（学生姓名/身份证号/地址/省/市/区县/状态/引擎①过程/引擎②过程/说明），文件可打开 | 手工：批次结果 → 导出 |
| 18 | 未登录访问 `/admin/district-match` | 302 跳转登录 | 手工（`@auth.admin_required`） |
| 19 | 非 admin 调 `POST /api/district-match/query` | 403 JSON `{success:false, message:...}` | 手工（`_require_admin_json`） |
| 20 | 无 CSRF 令牌的 POST（如 `/api/district-match/batch`） | 403 JSON「安全校验失败」 | 手工（全局 `app._csrf_protect`） |
| 21 | 字典导入进行中再次触发导入 | 409 JSON「字典导入正在进行中」 | 手工（`_import_lock`） |
| 22 | 字典未导入时单条查询 | 409 JSON「区划字典尚未导入…」，不抛 500 | 自动（`test_fill_never_raises_when_dict_missing` 覆盖留空语义）+ 手工接口 |
| 23 | 上传 11 MB 文件 | 413（`MAX_CONTENT_LENGTH=10MB`）或明确提示 | 手工 |
| 24 | 现有 `pytest -q` | 125 passed，无回归 | 自动（§1.4） |
| 25 | 新增学生，地址 `黑龙江省哈尔滨市道外区水泥路99-4号` | 省=黑龙江省 `230000000000` / 市=哈尔滨市 `230100000000` / 县=道外区 **`230104000000`** / 状态=**正确** | 自动 `test_ensure_student_district_only_writes_result`（先 create_student）+ 手工表单 |
| 26 | 新增学生，无地址，证件 `230105...` | 县=道外区 **`230104000000`**，状态=**已解析(仅证件)** | 自动 `test_case03_id_only`（算法层）+ 手工表单 |
| 27 | 编辑学生，仅改电话 | 7 列保持原值（不重算、不覆盖） | 手工（`update_student` 仅 `address/id_card` 变化才追加 7 列） |
| 28 | 编辑学生，地址改为 `北京市海淀区...` | 7 列更新为北京市 / 海淀区 `110108000000`，状态=已解析 | 手工（同上逻辑）+ 算法层 `test_case05_municipality` |
| 29 | 字典未导入时新增学生 | 学生保存成功；7 列为空串；无异常、无 500 | 自动 `test_fill_never_raises_when_dict_missing` + 手工 |
| 30 | 批量导入 100 行含地址 | 每行 7 列均写入；3000 行实测 0.044 s（0.015 ms/行） | 自动 `test_fill_many_perf`（3000 行）+ 手工导入 |
| 31 | 存量回填 `only_empty=True` | 仅补空值行；已有值行不变；`updated_at` 不变 | 自动 `test_backfill_only_empty_keeps_values`（值不变）+ 手工核对 `updated_at` |

> 说明：文档正文（本目录所附 PORTING_GUIDE 副本）12.2 #25/#26 仍写作 `230105000000`，按 2026-10-24 决策以现行码 **`230104000000`** 为准（见 IMPLEMENTATION_NOTES #3）。

---

## 3. 手工验证路径（浏览器）

1. admin 登录 → 学生数据管理 → **区划匹配**；
2. 单条查询：填 #1 的输入 → 期望“正确 / 道外区 `230104000000`”，展开可看引擎①②过程；
3. 字典状态：未导入时显示缺失；点“导入数据包”→ 进度到 664,239 → 状态显示各级行数；
4. 批量匹配：上传含“身份证号/住址”表头的 xlsx → 预览识别列 → 启动 → 进度 → 批次结果 → 导出；
5. 学生列表：编辑弹窗只读展示 7 项，点“重新匹配”只写 `dm_match_result`（`source='on_demand'`），`students` 不变；
6. 批量补全区划：只更新 7 列，`updated_at` 不变。
