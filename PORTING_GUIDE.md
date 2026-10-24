# 区划匹配功能接入 StudentMessageSharePRO —— 移植与集成手册

> **目标系统**：StudentMessageSharePRO（Flask 3.0 / SQLite WAL / Windows 单机 / 校园内网 / PyInstaller 单文件）。
> **依据**：《集成交接说明.md》（下称“交接说明”）与当前 `district_matcher` 仓库源码。
> **读者**：负责实施接入的开发人员 / AI Agent。
> **硬约束**：交接说明中的“必须 / 禁止 / 不得”优先级高于本手册；本手册与交接说明冲突时，以交接说明为准，或先与系统方确认。

---

## 0. 结论先行

### 0.1 关键决策

| 决策项 | 结论 | 理由 |
|---|---|---|
| 集成方式 | **进程内 Blueprint 模块** | 目标系统禁止新增端口/常驻进程；同进程可共享 `get_db()`、权限、CSRF、模板 |
| 是否新增服务/端口 | **否** | 复用 5001 主服务，不引入边车 |
| 数据访问 | **`models.db.get_db()` + Repository 适配** | 目标铁律禁止自建 `sqlite3.connect()`、长连接、连接池；原 `DBManager` 必须重写 |
| 区划字典存储 | **独立 `district.db` + 运行时 `ATTACH`（已确认）** | 精简字典全 5 级 105 MB；独立库不膨胀 `student.db`、不进备份、可重建 |
| 字典分发 | **5.43 MB 的 `.tsv.gz` 数据包，首次导入生成 105 MB 字典库** | 避免 365 MB 整库或 51 MB Excel 进 EXE；无 pandas 也能解析 |
| pandas | **完全移除** | 目标明确禁止（打包体积）；导入/导出改用 `openpyxl` + 标准库 `csv/gzip` |
| 学生表 | **新增 7 列（省/市/县 名称+代码 + 匹配状态）** | 业务方要求「录入即生成」，供后续档案转递单直接取用；列由系统独占写入 |
| 结果存储 | **独立 `dm_match_batch` / `dm_match_result`，软删除** | 符合“删除一律软删除”；不影响学生主表 |
| 前端 | **继承 `base.html`，仅 admin 可见** | 复用 Bootstrap 5.3、`showToast/showConfirm/safeJSON`；不新增 CDN |
| 新增 pip 依赖 | **无** | `openpyxl`、Flask 目标系统已有；不引入 pandas/numpy |
| 测试 | **pytest + 目标 `conftest.py` fixture** | 现有 62 条用例中，算法类可原样迁移；DB/接口类改用 fixture |
| 结果回写学生字段 | **本期做：录入时同步回写 6+1 列（已确认）** | 写入口集中在 `create_student` / `create_student_with_conn` / `update_student` 三处，详见 4.8.1 |
| 备份 | **仅备份 `student.db`（已确认）** | 备份服务只处理单库文件；`district.db` 不进备份，可由内置数据包重建 |

### 0.2 三个灰区 —— 系统方已确认（2026-10-24）

| # | 问题 | 结论 |
|---|---|---|
| 1 | `ATTACH DATABASE` 是否接受 | **接受**。系统备份只备份 `student.db` 单文件（SQLite 原生 `backup()`，失败回退 `copy2` + wal/shm），备份目录 `_USER_DATA_DIR/databackup/`，**不复制 `data/` 目录**，因此 `district.db` 天然不进备份、无需做排除逻辑；它属于“可由内置数据包重建”的可再生资产。 |
| 1b | 字典 105 MB 一次性导入用什么连接 | **允许对 `district.db` 使用一次性独立连接**（系统批准的**唯一例外**）：仅限 admin 触发的后台导入任务，完成即关闭；不得进入请求路径、不得建连接池、不得跨库写 `main`。 |
| 2 | `students` 是否已有地址字段 | **有**：`students.address`（中文名「住址」），已在 `config.FIELD_MAPPING`（`'住址': 'address'`）、`ALL_STUDENT_FIELDS` 与导入模板中，`update_student.allowed_fields` 也含 `address`。**受 `fields_visible` 限制**：非 admin 默认 9 个基础字段不含「住址」，需管理员在用户管理显式勾选；住址不参与脱敏（默认不可见即等价保护）。 |
| 3 | 是否回写学生主表 | **回写（已确认，2026-10-24 变更）**：录入学生时同步生成「省/市/县 名称+代码」共 6 列，另加 1 列匹配状态；写入口集中、失败留空不阻断、不进导入模板。详见 **4.8.1**。 |

**ATTACH 的附加约束（必须写进实现）**

1. 只允许通过 `models.db.get_db()` 返回的同一连接 ATTACH，禁止自建连接 / 连接池；每请求只 ATTACH 一次（`g` 标记，连接关闭自动 DETACH）。
2. 运行期对 `dm` **只读**；不得跨库写 `main`；不得在 `student.db` 中创建依赖 `dm.*` 的视图或触发器（换机恢复会失败）。
3. 路径固定为 `os.path.join(os.path.dirname(config.DATABASE_PATH), 'district.db')`；**不要使用 `config._USER_DATA_DIR` 私有变量**。
4. `district.db` 缺失或损坏时，功能必须降级提示「区划字典未导入」，不得抛 500。
5. 备份口径：**仅 `student.db` 进入备份**；`district.db` 不备份、由内置数据包重建。恢复流程 = 还原 `student.db` → 若缺字典则重新导入。

---

## 1. 目标系统约束摘要

### 1.1 不可协商基线（摘自交接说明）

| 项 | 值 |
|---|---|
| 技术栈 | Python 3.10+ / Flask 3.0 / 原生 JS / Bootstrap 5.3 / SQLite（WAL）/ openpyxl |
| 运行环境 | Windows 单机 / 校园内网，**无外网** |
| 启动 | `python run_with_tray.py` 或 `python app.py` |
| 端口 | 默认 5001，由 `config.txt` / `STUDENT_PORT` 决定，**不得硬编码** |
| 打包 | PyInstaller 单文件 `run_with_tray.spec`；新增静态/数据文件必须同步进 spec |
| 路径 | `BASE_DIR`（代码/只读）与 `_USER_DATA_DIR`（用户数据）分离 |
| 数据库 | `_USER_DATA_DIR/data/student.db` |
| 运行模型 | 单进程多线程；长任务 daemon 线程 |

**路径铁律**：运行时生成的任何文件（上传、导出、缓存、日志、照片）必须写入 `_USER_DATA_DIR`，严禁写入代码目录。

### 1.2 对本项目影响最大的 8 条

| # | 约束 | 对本项目的直接影响 |
|---|---|---|
| 1 | 路由必须走 Blueprint | 原 `app.py` 的全部页面/API 路由不能直接搬；需新建 `blueprints/district_match.py`，`app.py` 只加 2 行 |
| 2 | 数据访问必须走 `models/` + `get_db()` | 原 `DBManager`（自建长连接、写锁、缓存）必须拆成 Repository + 模块级缓存 |
| 3 | 建表/加字段只能写在 `models/db.py:init_db()` | 字典表、批次表、结果表、历史表 DDL 全部集中到 `init_db()` |
| 4 | 写操作必须权限装饰器保护；AJAX 用 `_require_admin_json()` | 原 API 无鉴权，必须逐个加 |
| 5 | 删除一律软删除 | 批次、结果、历史映射用 `is_deleted`；临时文件可物理删 |
| 6 | 学生查询必须排除毕业/已删除，复用共享函数 | 若按学生筛选，必须用 `_non_graduated_where()` / `_exclude_graduated_clause()` |
| 7 | 禁止 pandas / ORM / 新 CDN / 前端框架 | 原 `import_service`、`export_service`、`db_manager` 中 pandas 代码全部重写 |
| 8 | 上传白名单 xlsx/xls/csv，`MAX_CONTENT_LENGTH=10MB` | **51 MB 区划 Excel 不能上传**；字典改为“内置数据包 + 固定路径导入” |

### 1.3 其他必须遵守的细节

- **CSRF**：全局校验所有 POST/PUT/DELETE/PATCH；`fetch` 由 `base.html` 拦截器自动带 `X-CSRF-Token`；独立表单需 hidden input。
- **CSP**：只放行 `self` 与 `cdn.jsdelivr.net`；新 JS 必须放 `static/js/` 外链，不能写内联 `<script>` 或 `onclick=`。
- **前端**：不得 `alert()` / `confirm()`，用 `showToast()` / `showConfirm()`；`fetch` 统一 `safeJSON()`；数据渲染不得 `innerHTML` 直插。
- **事务**：批量写入用 `BEGIN IMMEDIATE` + UPSERT。
- **测试**：pytest，`tests/conftest.py` 提供 `app/client/db_conn` fixture。
- **改动最小**：不改现有函数签名、不重命名现有字段/路由/模板变量、不重构无关代码。

---

## 2. 原系统资产盘点

### 2.1 业务能力

输入「身份证号/6 位区划代码」+「地址文本」，输出省/市/区县三级 **12 位行政区划代码与名称**，并给出 7 种匹配状态与决策依据。

核心算法资产：

- 地址正则抽取（省/市/区县/乡镇/村、自治区简称、直辖市、长地名）。
- 身份证前 6 位层级构造 + 历史旧码映射。
- 名称反查 + 父级补全（含省直管县、县级市省略地级市）。
- 双引擎仲裁（地址为主、层级 3 为锚点）。

### 2.2 模块依赖

```
（原）app.py
 ├── config.py
 ├── database.db_manager.DBManager ── config.py, pandas, history_data
 ├── services.import_service ──────── config.py, pandas
 ├── services.match_service ───────── engine.*, utils.validators, database.models
 ├── services.export_service ──────── config.py, pandas
 └── utils.cleanup ────────────────── config.py

engine.engine1 ─── config.py, database.models, utils.validators
engine.engine2 ─── database.models, utils.address_parser, utils.validators
engine.decider ─── database.models
utils.address_parser ─── config.py
sdk.district_matcher ─── database.db_manager, services.match_service
```

### 2.3 文件级处置表

| 原文件 | 职责 | 目标处置 | 说明 |
|---|---|---|---|
| `utils/address_parser.py` | 地址正则抽取 | **原样复用** | 仅把 `import config` 改为本地常量 |
| `utils/validators.py` | 身份证工具 | **原样复用** | 无外部依赖 |
| `engine/engine1.py` | 身份证层级匹配 | **原样复用** | 依赖 Repository 的同名方法 |
| `engine/engine2.py` | 地址反查/补父级 | **原样复用** | 依赖 Repository 的同名方法 |
| `engine/decider.py` | 双引擎仲裁 | **原样复用** | 无 DB 依赖 |
| `database/models.py` | 结果 dataclass | **原样复用** | 可作为服务层 DTO |
| `database/history_data.py` | 16 条历史种子 | **复用数据** | 迁为 `dm_history_mapping` 种子 |
| `services/match_service.py` | 单条/批量匹配编排 | **小改复用** | `db` 参数换成 Repository |
| `database/db_manager.py` | 连接、缓存、CRUD | **重写** | 拆成 `models/district.py` + 模块缓存；禁止长连接 |
| `services/import_service.py` | 区划/学生导入 | **重写** | 去 pandas；字典读数据包，学生用 openpyxl/csv |
| `services/export_service.py` | 结果导出 | **重写** | 去 pandas；openpyxl/csv 写 `_USER_DATA_DIR` |
| `utils/cleanup.py` | uploads 清理 | **丢弃/重写** | 原逻辑基于 `config.UPLOAD_DIR`；目标按需清理导出文件 |
| `sdk/district_matcher.py` | 进程内 SDK | **丢弃** | 目标用 Blueprint；如需内部调用，直接调服务层 |
| `app.py` | Flask UI/API/托盘 | **丢弃** | 路由迁入 Blueprint；托盘/端口由目标负责 |
| `templates/*.html` | 5 个页面 | **重写** | 必须继承 `base.html`、走目标 CSRF/JS 规范 |
| `static/css/style.css` | 原样式 | **丢弃** | 原模板未引用，样式来自 Bootstrap CDN |
| `district_matcher.spec` / `version.txt` | 原打包 | **丢弃** | 改用目标 `run_with_tray.spec` |
| `database/district_matcher.db` | 完整字典（365 MB） | **不直接分发** | 离线生成 5.43 MB 数据包 |
| `data/全国行政编码数据.xlsx` | 字典源（51 MB） | **不打包** | 仅用于离线生成数据包 |
| `tests/*` | 62 条 unittest（算法/DB/接口）+ 11 个验证脚本 | **迁移** | 算法类直接转 pytest；含 DB/接口的改用 fixture |

### 2.4 数据规模（实测）

| 项 | 数值 |
|---|---|
| 原始 SQLite | 382,263,296 字节（约 365 MB） |
| `t_district_code` 总行数 | 664,239 |
| level 1 / 2 / 3 / 4 / 5 | 31 / 343 / 3,255 / 41,274 / 619,336 |
| 原 Excel | 51,380,202 字节（31 个省级 sheet） |
| 常驻缓存（level 1–4） | 44,903 行，约 50 MB，首次加载约 1.8 s |
| 历史映射种子 | 16 条 |

---

## 3. 目标系统内的落位设计

### 3.1 目录结构

```
models/
  district.py                 # Repository：字典查询、历史映射、缓存、批次/结果 CRUD
  db.py                       # 仅追加 DDL（dm_* 表 + ATTACH + 建表）
services/
  district_match/
    __init__.py
    constants.py              # 补零常量、直辖市表、状态枚举
    models.py                 # LevelResult / Engine1Result / Engine2Result / MatchResult
    address_parser.py         # 原样迁移
    validators.py             # 原样迁移
    engine1.py                # 原样迁移
    engine2.py                # 原样迁移
    decider.py                # 原样迁移
    runner.py                 # run_match / 批量匹配编排（原 match_service）
    importer.py               # 数据包导入（gzip+csv，无 pandas）
    exporter.py               # xlsx/csv 导出（openpyxl，无 pandas）
    pack.py                   # 数据包解析与校验
blueprints/
  district_match.py           # 页面 + API + 后台任务
templates/
  admin/
    district_match.html       # 继承 base.html
static/
  js/
    district_match.js         # 外链脚本，无内联、无 innerHTML
tests/
  test_district_match_unit.py     # 纯算法（62 条迁移）
  test_district_match_routes.py   # 鉴权/CSRF/进度/导出
  test_district_match_repo.py     # 字典导入与查询
data/（打包进 EXE，只读）
  district_pack.tsv.gz        # 5.43 MB，全 5 级
```

### 3.2 分层职责

| 层 | 文件 | 职责 | 约束 |
|---|---|---|---|
| 模型/数据 | `models/district.py` | 所有 SQL、缓存、行转换 | 只用 `get_db()`；返回 dict/list；不自建连接 |
| 模型/迁移 | `models/db.py` | `dm_*` 表 DDL、ATTACH、索引、历史种子 | 幂等；只追加，不改旧逻辑 |
| 服务 | `services/district_match/*` | 算法、导入、导出、批量编排 | 不直接碰 Flask 请求对象；纯函数优先 |
| 路由 | `blueprints/district_match.py` | 页面、JSON API、后台任务 | 权限、CSRF、参数校验、错误转换 |
| 前端 | `templates/admin/district_match.html` + `static/js/district_match.js` | 交互、轮询、渲染 | 继承 base、showToast/showConfirm/safeJSON、textContent |
| 测试 | `tests/test_district_match_*.py` | 单元 + 集成 | 复用 conftest fixture |

### 3.3 与目标“集成十条铁律”的逐条落实

| 铁律 | 落实方式 |
|---|---|
| 1. 路由走 Blueprint | 新建 `blueprints/district_match.py`；`app.py` 仅 `from blueprints import district_match` + `app.register_blueprint(district_match.bp)` |
| 2. 数据访问走 models | `models/district.py` 中全部使用 `get_db()`；无 `sqlite3.connect()`、无连接池、无长连接 |
| 3. DDL 只在 `init_db()` | 字典表、批次表、结果表、历史表、索引、历史种子全部追加到 `models/db.py:init_db()` |
| 4. 写操作鉴权 | 页面 `@auth.admin_required`；所有 POST 先 `_require_admin_json()` |
| 5. 软删除 | `dm_match_batch.is_deleted`、`dm_match_result.is_deleted`、`dm_history_mapping.is_deleted` |
| 6. 学生过滤 | 若按学生筛选，复用 `_non_graduated_where()`；MVP 上传清单不受影响 |
| 7. 前端规范 | `showToast` / `showConfirm` / `safeJSON`；无 `alert/confirm/innerHTML` |
| 8. 无新 CDN/框架/ORM | 只用现有 Bootstrap 5.3 与 Icons；无新依赖；JS 外链 |
| 9. 批量事务 | 字典导入、结果写入均 `BEGIN IMMEDIATE` + 分批提交 |
| 10. 最小改动 | 不改 `auth.py`、不改现有路由/字段；`app.py` 2 行、`models/db.py` 追加、`base.html` 加 1 个导航项 |

### 3.4 `app.py` 的 2 行改动

```python
# app.py
from blueprints import district_match          # 新增
app.register_blueprint(district_match.bp)     # 新增
```

不要在 `app.py` 里写任何业务逻辑、线程、路径。

### 3.5 命名空间

- 所有新表统一 `dm_` 前缀，避免与现有表冲突。
- 所有路由统一 `/api/district-match/...`，页面 `/admin/district-match`。
- 所有 JSON 字段使用 snake_case，与目标系统一致。
- `services/district_match/` 包内一律用相对导入，避免与目标顶层 `models` 包同名冲突。

### 3.6 导入路径改造（最小 diff）

复制算法文件后，只改导入，不改逻辑：

| 原写法 | 目标写法 |
|---|---|
| `import config` | `from . import constants as config` |
| `from database.models import ...` | `from .models import ...` |
| `from utils import validators` | `from . import validators` |
| `from utils import address_parser` | `from . import address_parser` |
| `from engine import engine1, engine2, decider` | `from . import engine1, engine2, decider` |
| `from services.match_service import ...` | 不使用；改由 `runner.py` 内部相对导入 |

`constants.py` 必须提供与原 `config.py` 同名的常量：`MUNICIPALITIES`、`PROVINCE_PAD`、`CITY_PAD`、`DISTRICT_PAD`、状态枚举。这样引擎与地址解析器可以做到“只改 import，不改代码”。

> `models/district.py` 属于目标系统的模型层，使用绝对导入 `from models.db import get_db`，不要放进 `services` 包。

---

## 4. 数据设计

### 4.1 存储方案：独立字典库（推荐）

| 方案 | student.db 增量 | 备份影响 | 实现复杂度 | 结论 |
|---|---|---|---|---|
| **A. 独立 `district.db` + ATTACH** | 0 | `student.db` 不变；若按目录备份，需排除 `district.db` | 中（每次请求 ATTACH 一次） | **推荐** |
| B. 字典表并入 `student.db` | +约 105 MB | 每次备份都多 105 MB，无法排除 | 低（无需 ATTACH） | 备选 |
| C. 直接分发 365 MB 原库 | +365 MB | 严重 | 低 | 不采用 |

实测（精简 schema，全 5 级）：**105 MB**；仅 1–4 级：**6.4 MB**。

> 方案 A 的前提是系统方接受 `ATTACH`。`ATTACH` 只是把另一个 SQLite 文件挂到当前 `get_db()` 连接上，不新建连接、不建连接池、不保持长连接。若系统方认为这仍违反“单库”约定，改用方案 B。

### 4.2 精简字典表（`district.db`）

```sql
CREATE TABLE IF NOT EXISTS dm_district_code (
    district_code TEXT NOT NULL,          -- 12 位
    district_name TEXT NOT NULL,
    admin_level   INTEGER NOT NULL,       -- 1省 2市 3区县 4乡镇街道 5村社区
    parent_code   TEXT,                   -- 父级 12 位代码（原 UUID pid 的等价物）
    province_code TEXT,
    city_code     TEXT,
    PRIMARY KEY (district_code, admin_level)
) WITHOUT ROWID;

CREATE INDEX IF NOT EXISTS idx_dm_district_name
    ON dm_district_code(district_name, admin_level);
CREATE INDEX IF NOT EXISTS idx_dm_district_parent
    ON dm_district_code(parent_code);
```

设计要点：

- 去掉原表的 UUID `id` 和全路径 `pids`，用 12 位 `district_code` 作为 `id`、`parent_code` 作为 `pid`。
- 原算法只依赖 `id` / `pid` / `district_code` / `district_name` / `admin_level`，因此**引擎代码可原样复用**（见 6.6）。
- `WITHOUT ROWID` + 复合主键，体积更小。

### 4.3 业务表（`student.db`）

```sql
CREATE TABLE IF NOT EXISTS dm_match_batch (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_no    TEXT UNIQUE,                 -- B + 8 位十六进制
    source      TEXT,                        -- upload / students / manual
    total       INTEGER DEFAULT 0,
    status      TEXT DEFAULT 'running',      -- running / done / failed
    stats_json  TEXT,                        -- 各状态计数
    created_by  TEXT,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    is_deleted  INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_dm_batch_deleted
    ON dm_match_batch(is_deleted, created_at);

CREATE TABLE IF NOT EXISTS dm_match_result (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id       INTEGER,
    student_id     INTEGER,                  -- 可空；来自学生表时填写
    student_name   TEXT,
    id_card        TEXT,
    address        TEXT,
    province_code  TEXT, province_name TEXT,
    city_code      TEXT, city_name     TEXT,
    district_code  TEXT, district_name TEXT,
    match_status   TEXT,
    engine1_detail TEXT,
    engine2_detail TEXT,
    decision_path  TEXT,
    created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    is_deleted     INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_dm_result_batch
    ON dm_match_result(batch_id, is_deleted);
CREATE INDEX IF NOT EXISTS idx_dm_result_status
    ON dm_match_result(match_status);
```

说明：

- 不建外键（与目标现状一致），关联校验放服务层。
- 批次删除 = 批次与结果同时 `is_deleted=1`，不物理删除。
- 结果表体积很小（每批几千行），可纳入现有备份，无压力。

### 4.4 历史映射表（`student.db`）

```sql
CREATE TABLE IF NOT EXISTS dm_history_mapping (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    old_code    TEXT,
    new_code    TEXT,
    old_name    TEXT,
    new_name    TEXT,
    change_type TEXT,
    change_date TEXT,
    remark      TEXT,
    is_deleted  INTEGER DEFAULT 0
);
CREATE UNIQUE INDEX IF NOT EXISTS uk_dm_history_old_code
    ON dm_history_mapping(old_code) WHERE old_code IS NOT NULL AND is_deleted = 0;
```

放在 `student.db` 的理由：历史映射是人工维护的业务配置，体量小，应随主库备份；字典库可随时重建，不应把人工配置放进去。

### 4.5 数据包格式

`district_pack.tsv.gz`（gzip 压缩的 UTF-8 TSV，首行表头）：

```
district_code	district_name	admin_level	parent_code	province_code	city_code
230000000000	黑龙江省	1		230000000000	
230100000000	哈尔滨市	2	230000000000	230000000000	230100000000
230104000000	道外区	3	230100000000	230000000000	230100000000
```

实测体积：

| 数据包 | 原始 TSV | gzip | 导入后 DB | 能力 |
|---|---|---|---|---|
| 全 5 级 | 49.3 MB | **5.43 MB** | **104.8 MB** | 省/市/区县 + 乡镇/村兜底 |
| 仅 1–4 级 | 2.98 MB | **397 KB** | **6.4 MB** | 省/市/区县；无乡镇/村兜底 |

**推荐全 5 级包**：只多 5 MB，保留乡镇/村反推能力。

### 4.6 ATTACH 生命周期

```python
# models/district.py
import os, sqlite3
from flask import g
from models.db import get_db

DISTRICT_DB = os.path.join(_USER_DATA_DIR, "data", "district.db")   # 用目标实际常量

def ensure_attached(conn=None):
    conn = conn or get_db()
    if getattr(g, "_dm_attached", False):
        return conn
    try:
        conn.execute("ATTACH DATABASE ? AS dm", (DISTRICT_DB,))
    except sqlite3.OperationalError as e:
        if "already in use" not in str(e):
            raise
    g._dm_attached = True
    return conn
```

要点：

- 每次请求/每个 app context 只 ATTACH 一次；连接关闭即自动 DETACH。
- 后台线程必须先 `with app.app_context():`，再 `get_db()` / `ensure_attached()`。
- 不要对 `dm` 库重复设置 `journal_mode/synchronous`；目标连接已有 PRAGMA。
- 首次创建 `district.db` 时，可在 `init_db()` 中 `ATTACH` 并建表；之后运行期只读。
- `init_db()` 通常在无 Flask 请求上下文时执行，**不要依赖 `g`**；在迁移连接上直接 `ATTACH` 即可。
- 字典库导入完成后可考虑 `PRAGMA dm.journal_mode=DELETE`（避免 WAL 旁文件），但需系统方确认。

### 4.7 迁移与回滚

**新增（全部幂等）**：

1. `models/db.py:init_db()` 追加业务表 DDL 与索引。
2. `init_db()` 中 `ATTACH` 字典库并建字典表/索引（方案 A）。
3. 历史映射 16 条种子：`INSERT OR IGNORE`（按 `old_code`）。
4. `_USER_DATA_DIR/data/` 目录创建。
5. 数据包首次导入由管理员触发（或首次访问自动触发，二选一）。

**回滚**：

```sql
DROP TABLE IF EXISTS dm_match_result;
DROP TABLE IF EXISTS dm_match_batch;
DROP TABLE IF EXISTS dm_history_mapping;
-- 方案 A：DROP TABLE dm.dm_district_code; 并删除 district.db 文件
```

再移除 `app.py` 2 行、`base.html` 导航项、模板/JS/服务文件、spec 中数据包条目。**不改动任何现有学生数据，回滚无数据风险。**

### 4.8 与 `students` 的关联

| 项 | 建议 |
|---|---|
| 关联键 | `id_card`（交接说明推荐）；`student_id` 仅作辅助 |
| 读取字段 | `students.id_card`、`students.name`、`students.address`（已确认存在，受 `fields_visible` 限制） |
| 写入字段 | **本期写 `students` 的 7 个区划列**（见 4.8.1）；批次结果仍写 `dm_match_result` |
| 学生筛选 | 若按现有学生批量，必须复用 `_non_graduated_where()`，排除毕业/删除 |
| 地址质量 | 地址为自由文本、填写率未知（当前开发库为空）。批量输入必须同时支持「上传清单」与「取学生表地址」，并在界面显示有/无地址人数，由操作者选择 |

### 4.8.1 录入时回写区划字段（本期新增，已确认）⭐

**新增 7 列（`students`，全部 `TEXT DEFAULT ''`）**

| 列名 | 中文名 | 示例 / 说明 |
|---|---|---|
| `district_province_name` | 省 | 黑龙江省 |
| `district_province_code` | 省区划代码 | `230000000000` |
| `district_city_name` | 市 | 哈尔滨市 |
| `district_city_code` | 市区划代码 | `230100000000` |
| `district_county_name` | 县 | 道外区 |
| `district_county_code` | 县区划代码 | `230105000000`（以**现行码**为准，历史码只作匹配输入） |
| `district_match_status` | 匹配状态 | `正确` / `已解析` / `已解析(仅证件)` / `需复核` / `部分` / `无匹配` / `异常` / 空=未计算 |

> 代码列必须 `TEXT` 且保持 12 位（含前导零），**严禁存 INTEGER**。

**写入点（只此三处，禁止在路由层另写）**

| 函数 | 覆盖入口 | 触发规则 |
|---|---|---|
| `models.create_student` | 独立表单新增、弹窗新增 | 新建时必算 |
| `models.create_student_with_conn` | Excel 批量导入新增、批量更新自动新增 | 新建时必算（后台线程，注意缓存预热） |
| `models.update_student` | 编辑保存、弹窗保存 | **仅当 `address` 或 `id_card` 变化**才重算，否则保留原值 |

统一调用 `models/district.py: fill_district_fields(data, conn=None)`：取 `data['address']` 与 `data['id_card']` 走引擎既有决策路径（有地址按地址，无地址退证件），把 7 个字段写回 `data` 后落库；**不得接入点各写一套**。

**失败语义（已确认：留空不阻断）**

- 字典未导入 / 两者皆无结果 / 引擎异常 → 7 列留空或写状态，学生**照常保存**，接口返回成功；
- 异常不得上抛，最多 `logger.warning`；
- 单条耗时目标 < 50 ms（缓存命中后）；批量导入时须先 `warm_cache()`，否则逐条查 105 MB 库会明显拖慢。

**导入模板（已确认：不进模板）**

- `utils.create_import_template()` 的 29 列表头是**硬编码**的，不改 → 模板天然不含这 7 列；
- 7 列仍要加进 `config.FIELD_MAPPING`（导出引擎白名单由它派生）与 `config.ALL_STUDENT_FIELDS`（字段权限/查询用），并在 `app.py` 的 `field_help['auto_fields']` 中标注“自动生成”；
- 用户自建 Excel 若带了这些列，导入侧**忽略其值**，一律以系统计算为准。

**审计**：自动生成字段**不写 `update_logs`**（否则每次录入刷 7 条噪音）；人工「重新匹配」与批量回填同样不写。

**存量回填（已确认：脚本 + 页面按钮）**

- `scripts/backfill_district.py`：`--dry-run` / `--limit` / `--only-empty`（默认只补空），可中断续跑；
- admin 页面「批量补全区划」按钮：后台线程 + 进度轮询，学生口径复用 `_non_graduated_where()`，单次 ≤5,000 条；
- 回填**只 UPDATE 这 7 列，不改 `updated_at`**（避免污染“最后更新时间”）。

**对既有功能的影响**

| 位置 | 影响 |
|---|---|
| 学生列表 | **不加列**（已 18 列）；编辑弹窗只读展示 7 项 + 「重新匹配」按钮 |
| 学生 Excel 导出 | 默认 `ALL_STUDENT_FIELDS` 会多 7 列；若不想污染列表导出，导出路由需显式传 fields 白名单 |
| 导入模板 | 不变（硬编码 29 列） |
| 字段权限 | 新增 7 个可勾选项；非 admin 默认不可见 |
| 备份 | `student.db` 仅多 7 个 TEXT 列，体积影响可忽略 |

### 4.9 供后续功能复用的接口契约（档案转递单 / 派遣）⭐

> 业务方 2026-10-24 说明：未来要用「区划代码 + 省/市/县」匹配地方人才（接收单位）地址，为学生批量生成「档案转递单」，并对已派遣档案标记日期。因此本期产物不能只停留在页面：**4.8.1 已把这 6 个值落到 `students`**，后续模块可直接 `SELECT` 取用；同时仍提供下列接口供未落库/按需计算的场景使用。

本期就要落地（成本很低）：

| 接口 | 建议签名 | 行为 |
|---|---|---|
| 单生查询 | `get_student_district(student_id=None, id_card=None)` | 返回该生**最新一条有效**结果（`is_deleted=0` 且状态属已解析类）的 `{province_code, province_name, city_code, city_name, county_code, county_name, match_status, batch_id, created_at}`；无结果返回 `None` |
| 按需计算 | `ensure_student_district(student_id, refresh=False)` | 有缓存直接返回；无则即时匹配并落一条结果（`source='on_demand'`） |
| 批量 | `match_students(student_ids, batch_id)` | 复用现有 worker；学生口径必须与 `_non_graduated_where()` 一致 |

设计约束：

- 关联键用 `id_card`，`student_id` 仅辅助（学号可重分配）；
- 结果保留**批次维度**；单生查询取 `created_at DESC, id DESC` 第一条；
- 结果只软删除，查询自动跳过；
- **本期不做档案转递单/派遣**，那属于独立模块（建议 `blueprints/archive.py` + 新表 `archive_dispatch`），只通过上述接口消费区划结果。

后续模块的依赖点（先记录，避免本期返工）：

| 未来需求 | 依赖 | 提示 |
|---|---|---|
| 地方人才/接收单位匹配 | 需另一份「人才服务机构」字典（名称/地址/12 位区划代码/联系方式），以 `district_code` 关联 | 数据来源与授权由业务方提供，不在本期内置 |
| 批量生成档案转递单 | 4.8.1 已把省/市/县名称与代码写入 `students`，模板引擎（`EXPORT_ALLOWED_FIELDS`）可直接作为占位符 | 仅「接收单位名称/地址」需另加数据源（见上一行），区划部分无需再改造 |
| 派遣日期标记 | 新表 `archive_dispatch(dispatch_date, status, ...)` + `update_logs` 审计 | 不要塞进 `students.remark` |

---

## 5. 数据导入与导出（无 pandas）

### 5.1 离线生成数据包（一次性，不进入目标运行时）

在**当前仓库**执行（不属于目标系统代码，不违反“禁止自建连接”）：

```python
# tools/build_district_pack.py —— 仅离线构建时运行
import sqlite3, gzip, csv

SRC = "database/district_matcher.db"
OUT = "district_pack.tsv.gz"

conn = sqlite3.connect(SRC)
id_to_code = {r[0]: r[1] for r in conn.execute(
    "SELECT id, district_code FROM t_district_code"
)}
sql = """
SELECT district_code, district_name, admin_level, pid, province_code, city_code
FROM t_district_code
ORDER BY admin_level, district_code
"""
with gzip.open(OUT, "wt", encoding="utf-8", newline="") as f:
    w = csv.writer(f, delimiter="\t", lineterminator="\n")
    w.writerow(["district_code", "district_name", "admin_level",
                "parent_code", "province_code", "city_code"])
    for code, name, level, pid, pcode, ccode in conn.execute(sql):
        w.writerow([code, name, level, id_to_code.get(pid, ""),
                    pcode or "", ccode or ""])
```

产物：`district_pack.tsv.gz`（实测 5.43 MB）。把它加入目标 `run_with_tray.spec` 的 `datas`。

### 5.2 运行时导入流程

```
管理员点击“导入字典”
  → POST /api/district-match/dictionary/import
  → 校验无正在运行的导入
  → 生成 progress_id，daemon 线程启动
  → with app.app_context(): conn = get_db(); ensure_attached(conn)
  → BEGIN IMMEDIATE; DELETE FROM dm.dm_district_code; COMMIT
  → gzip.open(pack) + csv.reader 逐行
  → 每 5,000 行：BEGIN IMMEDIATE + executemany(INSERT OR REPLACE) + COMMIT
  → 更新 progress(done/total)
  → 校验各级行数
  → ANALYZE dm.dm_district_code（可选）
  → progress.status = done；清空字典缓存
```

关键点：

- **不能上传 51 MB Excel**：目标全局 `MAX_CONTENT_LENGTH=10MB`，且 `ALLOWED_EXTENSIONS` 不含 `.gz`。
- 数据包从**打包只读目录**读取：`BASE_DIR/data/district_pack.tsv.gz`；如允许外部更新，可优先读 `_USER_DATA_DIR/data/district_pack.tsv.gz`。
- 解析只用 `gzip` + `csv`，不引入 pandas。
- 分块提交，避免长时间持有写锁，影响其他老师录学生。
- 全量导入行数应为 `664,239`（允许版本差异）；各级行数见 2.4。
- 导入期间禁止再次导入（全局锁 + 状态检查）。
- **失败处理**：因分块提交无法整体回滚，失败时执行 `DELETE FROM dm.dm_district_code` 清空半成品，并置字典状态为“未初始化”，避免残缺字典参与匹配。如需保留旧字典，导入前先复制一份 `district.db` 备份。

### 5.3 事务模板

```python
BATCH = 5000
rows = []
conn.execute("BEGIN IMMEDIATE")
try:
    for row in reader:
        rows.append(row)
        if len(rows) >= BATCH:
            conn.executemany(
                "INSERT OR REPLACE INTO dm.dm_district_code "
                "(district_code, district_name, admin_level, parent_code, "
                " province_code, city_code) VALUES (?,?,?,?,?,?)", rows)
            conn.commit()
            rows.clear()
            conn.execute("BEGIN IMMEDIATE")
    if rows:
        conn.executemany(..., rows)
    conn.commit()
except Exception:
    conn.rollback()
    raise
```

> 目标连接若使用默认 isolation_level，显式 `BEGIN IMMEDIATE` 前需确保没有未提交事务。按目标 `get_db()` 实际行为调整。

### 5.4 学生清单导入（批量匹配输入）

- 支持 `.xlsx/.xls/.csv`，≤10 MB，扩展名白名单内。
- 用 `openpyxl.load_workbook(read_only=True, data_only=True)` 或 `csv`，逐行读，不转 DataFrame。
- 列映射：`姓名/name`、`身份证/id_card`、`地址/address`；用户可指定，否则自动识别。
- 至少要有 `id_card` 或 `address` 之一；两者都空的行跳过并计数。
- 上传文件保存到 `_USER_DATA_DIR/uploads/district_match/<uuid>.<ext>`，**不使用客户端文件名**（防路径穿越），任务结束可物理删除。
- 解析结果分批写入 `dm_match_result`（先写空结果占位）或只在内存中处理；推荐“边匹配边写”，避免大批量内存峰值。

### 5.5 导出

- 列顺序固定：

```
姓名 | 身份证号 | 地址 | 省 | 省级代码 | 市 | 市级代码 | 区县 | 区县代码 | 状态
```

- xlsx 用 openpyxl；csv 用标准库 `csv`，编码 `utf-8-sig`。
- 输出到 `_USER_DATA_DIR/exports/district_match/`，文件名服务端生成：`district_match_<batch_no>_<YYYYmmdd_HHMMSS>.<ext>`。
- 用 `send_file` 返回；不要写代码目录。
- **CSV 公式注入防护**：单元格以 `= + - @` 开头时前置 `'`。
- 不自动删除匹配结果；用户主动“删除批次”= 软删除。

### 5.6 字典校验

导入完成后校验：

```sql
SELECT admin_level, COUNT(*) FROM dm.dm_district_code GROUP BY admin_level;
```

期望：`1=31, 2=343, 3=3255, 4=41274, 5=619336`（允许数据版本差异）。任一为 0 视为失败，回滚或提示重新导入。

---

## 6. 匹配算法（保持语义不变）

> 本章是移植的“不可变契约”。引擎代码可以原样复制，只替换 Repository。

### 6.1 主流程

```
match(id_card, address):
    first6 = get_id_first6(id_card)          # 长度>=6 且前6位全数字，否则 None
    if first6 is None and address == "":
        return 状态“异常”
    e1 = engine1(first6) if first6 else None
    e2 = engine2(address) if address else None
    return decide(e1 or EMPTY_E1, e2)
```

### 6.2 引擎①（身份证）

```
build_codes(first6):
    province = first6[:2] + "0000000000"
    city     = first6[:4] + "00000000"
    district = first6      + "000000"

engine1:
    查省级（level 1）
    若 first6[:2] 属于 {11,12,31,50} 且省命中：市 = 省
    否则查市级（level 2）
    查区县（level 3）；命中且名称 != "市辖区" → matched
    否则：history_code_map[first6] → new6 → 查 new6+"000000"（level 3）
          命中且名称 != "市辖区" → matched, mapped=True
```

### 6.3 引擎②（地址）

五步：

1. **市级**：`reverse_lookup(city, 2)`；未命中则视为县级市，按 level 3 查并向上补父级。
2. **区县**：`reverse_lookup(district, 3, city_pid)`；**先别名后原名、先市范围后全局**。
3. **兜底扫描**：正则 `[\u4e00-\u9fa5]{1,10}?(?:自治县|市|县|区|旗|新区)`，排除已识别省市与“市辖区”。
4. **乡镇/村反推**：level 4 → 父级 level 3；level 5 → 父级 level 4 → level 3；沿父链核对归属；仅在无区县代码时执行。
5. **补省级代码**：名称存在但无代码时 `reverse_lookup(province, 1)`。

`_apply_county` 父级规则：

- 父级 level 2 且名为“市辖区” → 取祖父 level 1，省=市=省级。
- 父级 level 2 其他 → city=父级；省缺失则取祖父 level 1。
- 父级 level 1 → 省=市=父级（省直管县）。

### 6.4 决策表（v2）

| 情形 | 条件 | 输出 | 状态 |
|---|---|---|---|
| A | 引擎②有区县代码且引擎①异常 | 引擎② | 已解析 |
| A1 | 引擎②有区县代码，省/市名称一致 | 引擎② | 正确 |
| A2 | 引擎②有区县代码，省或市冲突 | 引擎② | 需复核 |
| A3 | 引擎②有区县代码，引擎①未完整校验 | 引擎② | 已解析 |
| B | 非 A，引擎②有省/市无区县，引擎①有区县 | 引擎②省市 + 引擎①区县 | 已解析 |
| B' | 同上但引擎①无区县 | 引擎②省市 | 需复核 |
| C | 非 A/B，引擎②仅有区县名无代码 | 引擎②名称 | 需复核 |
| D1 | 引擎②无输出，引擎①异常 | 引擎① | 异常 |
| D2 | 引擎②无输出，引擎①有区县 | 引擎① | 已解析(仅证件) |
| D3 | 引擎②无输出，引擎①仅省/市 | 引擎①省市 | 部分 |
| D4 | 两者均无 | 空 | 无匹配 |

分支顺序必须是：A → B → C → D。冲突用**名称全等**判断，不用代码前缀。

### 6.5 边界规则（必须保留）

| # | 场景 | 规则 |
|---|---|---|
| 1 | 历史代码 | `230105` 太平区 → `230104` 道外区（mapped=True） |
| 2 | 历史地名 | “太平区”→先翻“道外区”，先限定哈尔滨市，避免阜新市太平区 |
| 3 | 直辖市 | 省=市；跳过 level 2“市辖区”虚拟节点 |
| 4 | 省直管县 | 省=市=省级父级 |
| 5 | 县级市省略地级市 | “黑龙江省嫩江市”→按 level 3 查，补黑河市 |
| 6 | 自治区简称 | 内蒙古/广西/西藏/宁夏/新疆 先展开全称 |
| 7 | 长区县名 | 乡镇/村抽取在剔除区县名后进行，防“莫力达瓦达斡尔族自治旗”被截断 |
| 8 | 社区误判 | 区县正则 `(?<!社)区` |
| 9 | 乡镇/村反推 | 沿父链核对归属，优先乡镇再村 |
| 10 | 地址缺省 | 只有省/市/区县均可，能补父级必须补 |
| 11 | 证件异常 | 前 6 位非数字：有地址只用引擎②，无地址则异常 |
| 12 | 不做强校验 | 不校验 18 位/校验位，只取前 6 位 |

### 6.6 精简 schema 适配（关键）

Repository 返回的行必须保留原字段名，引擎无需改：

```python
{
    "id": row["district_code"],       # 原 UUID → 12 位代码
    "pid": row["parent_code"],        # 原父 UUID → 父级 12 位代码
    "pids": "",
    "district_code": row["district_code"],
    "district_name": row["district_name"],
    "admin_level": row["admin_level"],
    "province_code": row["province_code"],
    "city_code": row["city_code"],
}
```

已验证的兼容点：

- `engine2` 用 `db.get_by_id(row["pid"])` 取父级 → Repository 按 12 位代码查，OK。
- `city_pid` / `scope_id` 用 `row["id"]` 比较 → 统一为 12 位代码，OK。
- `reverse_lookup(name, level, pid)` 的 `pid` 过滤 → 用 `parent_code`，OK。
- `pids` 在引擎中未使用（仅导入时生成），可置空。

### 6.7 性能基线

| 场景 | 参考值 |
|---|---|
| 首次加载缓存（L1–4） | 约 1.8 s，约 50 MB |
| 单条匹配（缓存后） | 微秒级 |
| 批量（原验证脚本） | 约 5.7 万条/秒 |
| 字典建库（本机，含索引） | 约 2.6 s（不含解压/解析） |

目标单机 5–10 人使用无压力。

---

## 7. 服务层实现

### 7.1 Repository 接口（`models/district.py`）

```python
class DistrictRepository:
    def __init__(self, conn=None): self.conn = conn or get_db()

    def get_by_district_code(self, code, level=None): ...
    def get_by_id(self, code): ...                  # 按 12 位代码查任意层级
    def reverse_lookup(self, name, level, pid=None): ...
    def search_by_name(self, name, level=None): ...
    def get_history_alias_map(self): ...
    def get_history_code_map(self): ...
    def district_stats(self): ...
    def list_history(self): ...
    def add_history(self, data): ...
    def soft_delete_history(self, hid): ...
    def reset_history(self): ...
```

所有方法：

- 先 `ensure_attached()`。
- SQL 参数化。
- 返回 `dict`（`dict(sqlite3.Row)` 或手工 dict）。
- 不抛业务异常给模板；由路由层转换。

### 7.2 缓存

```python
_LOCK = threading.Lock()
_CACHE = None   # {"by_code": {}, "by_id": {}, "by_name_level": {}}

def load_cache(repo):
    global _CACHE
    if _CACHE is not None:
        return _CACHE
    with _LOCK:
        if _CACHE is not None:
            return _CACHE
        rows = repo.query_levels_le(4)   # 44,903 行
        by_code, by_id, by_name = {}, {}, {}
        for r in rows:
            d = adapt_row(r)
            by_code[(d["district_code"], d["admin_level"])] = d
            by_id[d["id"]] = d
            by_name.setdefault((d["district_name"], d["admin_level"]), []).append(d)
        _CACHE = {"by_code": by_code, "by_id": by_id, "by_name_level": by_name}
        return _CACHE
```

- 单进程多线程安全；字典导入、历史映射增删改/重置后必须 `invalidate_cache()`。
- 历史别名/代码映射也做进程级缓存（`_HISTORY_CACHE`），避免每条匹配都查库；增删改后同步失效。
- level 5 与“全层级名称搜索”直接 SQL，不常驻。
- 首访 1.8 s；如需隐藏延迟，可在管理员打开页面时启动 daemon 线程预热（可选）。

### 7.3 单条匹配

```python
def run_match(repo, id_card, address, with_detail=False):
    ...
    e1 = engine1_match(repo, id_card) if first6 else Engine1Result()
    e2 = engine2_parse(repo, address) if address.strip() else None
    return dual_engine_decide(repo, e1, e2)
```

与原版一致，不改语义。

### 7.4 批量匹配 worker

```python
def _worker(app_obj, progress_id, batch_id, source_path, mapping):
    with app_obj.app_context():
        conn = get_db()
        repo = DistrictRepository(conn)
        try:
            rows = read_input(source_path, mapping)   # openpyxl/csv
            total = len(rows)
            update_progress(progress_id, status="running", phase="matching",
                            done=0, total=total)
            buffer = []
            for i, item in enumerate(rows, 1):
                r = run_match(repo, item["id_card"], item["address"],
                              with_detail=True)
                buffer.append(to_row(batch_id, item, r))
                if len(buffer) >= 500:
                    save_results(conn, buffer)
                    buffer.clear()
                if i % 50 == 0:
                    update_progress(progress_id, done=i)
            if buffer:
                save_results(conn, buffer)
            update_progress(progress_id, status="done", done=total,
                            stats=count_stats(conn, batch_id))
        except Exception as e:
            update_progress(progress_id, status="error", message="匹配失败")
            mark_batch_failed(conn, batch_id)
```

要点：

- 后台线程必须 `with app_obj.app_context()`，不能直接用 `current_app`。
- `app_obj = current_app._get_current_object()` 在请求内获取。
- 每个 app context 自己 `get_db()`，结束自动关闭。
- 结果分块写入，`BEGIN IMMEDIATE` + `executemany`。
- 异常只记录安全消息，不把堆栈返回前端。

### 7.5 进度注册表

```python
_PROGRESS = {}
_PROGRESS_LOCK = threading.Lock()
_IMPORT_LOCK = threading.Lock()

def new_progress(kind):
    pid = uuid.uuid4().hex
    with _PROGRESS_LOCK:
        _PROGRESS[pid] = {"kind": kind, "status": "running", "phase": "",
                          "done": 0, "total": 0, "message": "", "ts": time.time()}
    return pid
```

- 单进程内存态；重启丢失，前端轮询到 404 时提示“任务已失效”。
- 完成后保留 1 小时，定时或惰性清理。
- 字典导入用 `_IMPORT_LOCK` 防并发；批量匹配建议限制同时 1 个（单机体验更好）。

### 7.6 并发与 SQLite

- 单进程、多线程：每个请求独立 `get_db()` 连接；WAL 允许读写并行。
- 批量写结果分块提交，避免长时间阻塞学生录入。
- 不要跨线程共享 `conn`；不要在后台线程创建裸连接。
- 若系统方未来改多进程，需要把进度表外置（Redis/DB），本方案默认单进程。

---

## 8. 路由、权限与 CSRF

### 8.1 路由表

| 方法 | 路径 | 用途 | 鉴权 |
|---|---|---|---|
| GET | `/admin/district-match` | 页面 | `@auth.admin_required` |
| POST | `/api/district-match/query` | 单条匹配 | `_require_admin_json()` |
| POST | `/api/district-match/batch` | 启动批量（上传/学生筛选） | `_require_admin_json()` |
| GET | `/api/district-match/progress/<progress_id>` | 进度轮询 | `_require_login_json()` |
| GET | `/api/district-match/batches` | 批次列表（分页） | `_require_login_json()` |
| GET | `/api/district-match/batches/<int:batch_id>/results` | 结果分页 | `_require_login_json()` |
| POST | `/api/district-match/batches/<int:batch_id>/delete` | 软删除批次 | `_require_admin_json()` |
| GET | `/api/district-match/batches/<int:batch_id>/export` | 导出 xlsx/csv | `_require_login_json()` |
| GET | `/api/district-match/dictionary` | 字典状态 | `_require_login_json()` |
| POST | `/api/district-match/dictionary/import` | 导入数据包 | `_require_admin_json()` |
| GET | `/api/district-match/history` | 历史映射列表 | `_require_login_json()` |
| POST | `/api/district-match/history` | 新增/更新映射 | `_require_admin_json()` |
| POST | `/api/district-match/history/delete` | 软删除映射 | `_require_admin_json()` |
| POST | `/api/district-match/history/reset` | 恢复 16 条种子 | `_require_admin_json()` |

> 若系统方要求所有功能仅 admin 可用，把 `_require_login_json()` 统一换成 `_require_admin_json()` 即可。

### 8.2 响应约定（必须跟目标一致）

```json
{"success": true, "data": {...}}
{"success": false, "message": "参数不完整"}
```

不要沿用原项目的 `{"code": 0, "data": ...}`。

### 8.3 路由骨架

```python
from flask import Blueprint, render_template, request, jsonify
import auth
from auth import _require_admin_json

bp = Blueprint("district_match", __name__)

@bp.route("/admin/district-match")
@auth.admin_required
def page():
    return render_template("admin/district_match.html")

@bp.route("/api/district-match/query", methods=["POST"])
def query():
    err = _require_admin_json()
    if err:
        return err
    data = request.get_json(silent=True) or {}
    id_card = (data.get("id_card") or "").strip()
    address = (data.get("address") or "").strip()
    if not id_card and not address:
        return jsonify({"success": False, "message": "请输入身份证号或地址"}), 400
    result = run_match(DistrictRepository(), id_card, address, with_detail=True)
    return jsonify({"success": True, "data": result_to_dict(result)})
```

### 8.4 CSRF

- 全局 `before_request` 已覆盖本模块所有 POST，无需额外配置。
- 页面继承 `base.html` 后，`fetch` 拦截器自动附加 `X-CSRF-Token`。
- 非 `fetch` 表单：`<input type="hidden" name="csrf_token" value="{{ csrf_token }}">`。
- 导出用 GET，不受 CSRF 限制。

### 8.5 错误处理

- 参数错误：400 + `{success:false,message}`。
- 未授权：由目标装饰器返回 401/403 JSON。
- 找不到批次：404。
- 任务冲突：409。
- 服务器异常：交给目标全局 `errorhandler`，不返回堆栈。
- 后台任务异常：进度接口返回 `{status:"error", message:"..."}`，日志记录详情。

---

## 9. 前端

### 9.1 页面结构

继承 `templates/base.html`，导航仅在 admin 显示：

```jinja
{% if session.role == 'admin' %}
<a class="nav-link" href="{{ url_for('district_match.page') }}">区划匹配</a>
{% endif %}
```

> 目标系统的角色/session 字段名可能与示例不同，按 `base.html` 现有导航项的写法复制，不要自创判断方式。

页面四个区块：

1. **单条查询**：身份证、地址两个输入框（可只填一个）→ 结果卡片 + 过程详情。
2. **批量匹配**：上传 xlsx/xls/csv（≤10MB）+ 列映射 + 进度条 + 结果表。
3. **批次记录**：批次列表、查看结果、导出、软删除。
4. **字典状态**（admin）：总行数、各级行数、导入按钮、导入进度。

### 9.2 CSP 与静态资源

- 不新增 CDN；继续用现有 Bootstrap 5.3 / Icons。
- JS 放 `static/js/district_match.js`，页面用 `<script src="{{ url_for('static', filename='js/district_match.js') }}" defer></script>`。
- **禁止内联 `<script>`、`onclick=`、`javascript:`**（CSP 只放行 self 与 jsdelivr）。
- 目标未开模板自动重载，**改模板/JS 后需重启服务**再验证。
- 配置通过 `data-*` 属性传给 JS，例如：

```html
<div id="dm-app"
     data-query-url="{{ url_for('district_match.query') }}"
     data-progress-url="{{ url_for('district_match.progress', progress_id='__PID__') }}">
</div>
```

### 9.3 JS 规范

```javascript
const resp = await fetch(url, {
  method: "POST",
  headers: {"Content-Type": "application/json"},
  body: JSON.stringify(payload)
});
const data = await safeJSON(resp);
if (!data.success) {
  showToast(data.message || "操作失败", "danger");
  return;
}
```

- 通知：`showToast(msg, type)`。
- 确认：`if (await showConfirm("确定删除该批次？")) { ... }`。
- 渲染：`td.textContent = value`；**禁止 `innerHTML` 拼接数据**。
- 进度轮询：500 ms；完成后停止并刷新结果。
- 上传：`FormData`，不要手动设 `Content-Type`。

### 9.4 批量匹配交互

1. 选择文件 → 上传 → 服务端解析前 10 行返回列名与自动映射（不落库）。
2. 用户确认映射 → 启动任务 → 返回 `progress_id` + `batch_id`。
3. 前端 500 ms 轮询进度；显示百分比与已处理数。
4. 完成后加载结果列表，显示状态统计。
5. 失败时显示安全错误消息，不弹堆栈。

### 9.5 结果渲染

- 状态徽章：正确=success、已解析=warning、已解析(仅证件)=info、需复核=danger、部分=secondary、无匹配=secondary、异常=danger。
- 详情用 `<details>` 折叠，内容用 `textContent` 写入。
- 分页 50 条/页；筛选状态。

---

## 10. 安全、审计与脱敏

| 项 | 要求 |
|---|---|
| 上传 | 扩展名白名单；服务端 UUID 文件名；校验大小；任务结束删临时文件 |
| 路径穿越 | 不使用客户端文件名；固定目录 + 服务端生成名 |
| XSS | 前端只用 `textContent`；Jinja 默认转义；不返回未转义 HTML |
| SQL | 全部参数化；LIKE 转义 `%` `_`；最小搜索长度 |
| 敏感字段 | 结果含身份证；admin 可见；若开放给非 admin，必须 `auth.mask_sensitive_data()` |
| 导出 | CSV 公式注入防护（`= + - @` 前置 `'`）；文件写 `_USER_DATA_DIR` |
| 日志 | 不记录完整身份证；不把堆栈返回前端 |
| 审计（可选） | 字典导入、批量匹配、批次删除写 `update_logs`；用目标现成模型，不新增表 |

---

## 11. 打包与路径

### 11.1 `run_with_tray.spec`

新增 `datas`：

```python
datas += [
    ("data/district_pack.tsv.gz", "data"),
    ("templates/admin/district_match.html", "templates/admin"),
    ("static/js/district_match.js", "static/js"),
]
```

- 新 Python 模块正常导入即可，无需 hiddenimports；若使用动态导入再加。
- 若目标 spec 已用 `collect_data_files("templates")` / `("static")`，只需追加数据包，避免重复条目。
- 不要把 `district.db` 打进 EXE（首次运行生成）。
- 不要把 365 MB 原库或 51 MB Excel 打进 EXE。

### 11.2 `_USER_DATA_DIR` 目录

```
_USER_DATA_DIR/
├── data/
│   ├── student.db
│   └── district.db                 # 首次导入生成，约 105 MB
├── uploads/
│   └── district_match/             # 临时清单，可清理
└── exports/
    └── district_match/             # 导出文件
```

启动时创建目录；不得写代码目录。

### 11.3 首次启动流程

1. `init_db()` 建业务表、ATTACH 建字典表（空）。
2. 管理员进入“区划匹配”页面。
3. 字典状态显示“未初始化”。
4. 点击“导入字典” → 后台读取内置 `district_pack.tsv.gz` → 生成 `district.db`（105 MB）。
5. 进度完成 → 显示各级行数 → 可开始匹配。

也可在首次访问时自动触发导入，但建议显式按钮，避免启动阻塞。

### 11.4 单文件 EXE 开销

- 数据包 5.43 MB 会被 PyInstaller 解压到临时目录，每次启动有少量开销；可接受。
- 如系统方希望零开销，可把数据包放到 `_USER_DATA_DIR/data/` 作为外部数据包，启动时优先读取；但这会增加部署步骤（需确认）。
- 105 MB `district.db` 只在用户数据目录生成一次，不进 EXE。

---

## 12. 测试与验收

### 12.1 pytest 结构

```python
# tests/test_district_match_unit.py
import pytest
from services.district_match.engine1 import engine1_match
from services.district_match.decider import dual_engine_decide

class FakeRepo:
    def __init__(self, rows): ...
    def get_by_district_code(self, code, level=None): ...
    def get_by_id(self, code): ...
    def reverse_lookup(self, name, level, pid=None): ...
    def get_history_code_map(self): ...
    def get_history_alias_map(self): ...

def test_history_code_mapping():
    repo = FakeRepo(FIXTURE_ROWS)
    r = engine1_match(repo, "230105")
    assert r.district.code == "230104000000"
```

- 复用原 62 条用例中的算法部分，改为 pytest 函数式；DB/历史/接口用例按目标 fixture 重写。
- 含 DB 的用例用 `db_conn` 或临时数据包 fixture；fixture 内调用 `invalidate_cache()`，避免跨用例污染。
- 路由用例用 `client` fixture，覆盖 401/403、CSRF、进度、导出、软删除。

### 12.2 必须通过的验收用例

| # | 输入 | 期望 |
|---|---|---|
| 1 | `230105...` + `黑龙江省哈尔滨市道外区水泥路99-4号` | 正确 / `230104000000` 道外区 |
| 2 | `230105...` + `哈尔滨市道外区...` | 正确 / `230104000000` |
| 3 | `230105` + 空 | 已解析(仅证件) / `230104000000` |
| 4 | 空 + `黑龙江省哈尔滨市道外区...` | 已解析 / `230104000000` |
| 5 | 空 + `北京市海淀区中关村大街1号` | 已解析 / `110108000000`，city=北京市 |
| 6 | `abc123` + 空 | 异常 |
| 7 | `abc123` + 道外区地址 | 已解析 / `230104000000` |
| 8 | 两个空白 | 异常 |
| 9 | `150722...` + `黑龙江省嫩江市...` | 黑龙江省/黑河市/嫩江市，状态=需复核（不得混用内蒙古/呼伦贝尔） |
| 10 | `640425...` + `宁夏彭阳县...` | 正确 / 彭阳县 |
| 11 | `黑龙江省黑河市嫩江市...` | city=黑河市，district=嫩江市 |
| 12 | 地址含 `XX社区` | 不得把“社区”识别为区县 |
| 13 | `莫力达瓦达斡尔族自治旗红彦镇新多村` | 区县/乡镇/村正确 |
| 14 | 导入数据包 | 各级行数符合 2.4 |
| 15 | 批量 3 条 | `总计=3`，无异常 |
| 16 | 批次软删除 | `is_deleted=1`，列表不显示，学生表不变 |
| 17 | 导出 xlsx/csv | 10 列中文表头，文件在 `_USER_DATA_DIR/exports` |
| 18 | 未登录访问页面 | 302 到登录 |
| 19 | 非 admin 调写接口 | 403 JSON |
| 20 | 无 CSRF 的 POST | 被全局拦截 |
| 21 | 导入中再次导入 | 409/失败提示 |
| 22 | 字典未初始化时匹配 | 明确提示“字典未导入”，不抛 500 |
| 23 | 上传 11 MB 文件 | 413/明确提示 |
| 24 | 现有 `pytest -q` | 全绿，无回归 |

**录入联动专项（4.8.1）**

| # | 输入 | 期望 |
|---|---|---|
| 25 | 新增学生（地址`黑龙江省哈尔滨市道外区水泥路99-4号`） | 7 列 = 黑龙江省/`230000000000`/哈尔滨市/`230100000000`/道外区/`230105000000`/`正确` |
| 26 | 新增学生（无地址、证件`230105...`） | 县=`230105000000`，状态=`已解析(仅证件)` |
| 27 | 编辑学生，仅改电话 | 7 列保持原值（不重算） |
| 28 | 编辑学生，改地址为`北京市海淀区...` | 7 列更新为北京市/海淀区，状态=`已解析` |
| 29 | 字典未导入时新增学生 | 学生保存成功，7 列为空，不报错 |
| 30 | 批量导入 100 行含地址 | 每行 7 列均写入；耗时可接受（先 `warm_cache()`） |
| 31 | 存量回填 `--only-empty` | 仅补空值行，已有值不动，`updated_at` 不变 |

### 12.3 自测命令

```powershell
python -m pytest -q
python -m pytest tests/test_district_match_unit.py -q
python -m pytest tests/test_district_match_routes.py -q
python app.py
# 浏览器 http://127.0.0.1:<config 端口>/admin/district-match
```

### 12.4 性能与资源验收

| 项 | 目标 |
|---|---|
| 首次缓存加载 | ≤ 3 s |
| 单条匹配 | ≤ 50 ms（含请求） |
| 1 万条批量 | 单机可接受，进度可见，UI 不阻塞 |
| 字典导入 | 有进度，期间其他写操作不被长时间阻塞 |
| 内存增量 | 50–90 MB |
| 磁盘增量 | 数据包 5.4 MB + 字典库 105 MB + 结果/导出 |

---

## 13. 实施步骤（Checklist）

### Phase 0：确认灰区 ✅ 已完成（2026-10-24，结论见 0.2 / 15.2）

- [x] `ATTACH` **允许**（五条附加约束见 0.2）；字典导入允许一次性独立连接。
- [x] `students.address` **已存在**（「住址」，受 `fields_visible` 限制）。
- [x] **回写 `students` 6+1 列**（2026-10-24 二次确认：录入即生成），见 4.8.1。
- [x] 不新增角色（仅 admin）。
- [x] 历史映射页只读。

### Phase 1：数据包与表

- [ ] 在源仓库运行 5.1 脚本，生成 `district_pack.tsv.gz`（5.43 MB）。
- [ ] `models/db.py:init_db()` 追加 4.3 / 4.4 DDL、索引、16 条历史种子、ATTACH。
- [ ] `models/db.py:init_db()` 幂等新增 `students` 的 7 列（4.8.1），并同步 `config.FIELD_MAPPING` / `ALL_STUDENT_FIELDS` / `app.py` 的 `field_help['auto_fields']`。
- [ ] 验证 `init_db()` 幂等（重复启动不报错，旧库能平滑升级）。

### Phase 2：算法与服务

- [ ] 迁移 `constants/models/address_parser/validators/engine1/engine2/decider`。
- [ ] 实现 `models/district.py`（Repository + 缓存 + `fill_district_fields(data, conn=None)` + `warm_cache()`）。
- [ ] 实现 `runner.py`（`run_match` + 批量 worker）。
- [ ] 用 FakeRepo 跑通原用例中的纯算法部分（地址解析、引擎①/②、决策、边界）。

### Phase 2.5：录入联动与存量回填 ⭐

- [ ] `create_student` / `create_student_with_conn` / `update_student` 三处接入 `fill_district_fields()`；编辑时按 `address`/`id_card` 是否变化决定是否重算。
- [ ] 批量导入前 `warm_cache()`；字典缺失时跳过计算，学生照常入库（失败留空）。
- [ ] `scripts/backfill_district.py`（`--dry-run` / `--limit` / `--only-empty`）。
- [ ] admin 页面「批量补全区划」按钮（后台线程 + 进度 + 只更 7 列、不动 `updated_at`）。
- [ ] 编辑弹窗只读展示 7 项 + 「重新匹配」按钮（仅 admin）。

### Phase 3：导入导出

- [ ] `importer.py`：gzip+csv 分块导入、进度、校验、缓存失效。
- [ ] `exporter.py`：openpyxl/csv、10 列、公式注入防护、写 `_USER_DATA_DIR`。
- [ ] 学生清单预览/导入（openpyxl/csv，≤10MB）。

### Phase 4：路由与前端

- [ ] `blueprints/district_match.py`：14 个路由 + 权限 + CSRF + 进度。
- [ ] `app.py` 加 2 行；`base.html` 加 1 个导航项。
- [ ] `templates/admin/district_match.html`：四个区块。
- [ ] `static/js/district_match.js`：safeJSON、toast/confirm、轮询、textContent。
- [ ] CSP 自检：无内联脚本/事件。

### Phase 5：打包与数据

- [ ] `run_with_tray.spec` 加数据包、模板、JS。
- [ ] 首次启动建目录、建表、导入字典。
- [ ] 验证 `_USER_DATA_DIR` 下生成 `district.db`、导出/上传目录。

### Phase 6：验收

- [ ] 12.2 全部用例 + 4.8.1 的录入联动用例（新增/导入/编辑/回填四条链路）。
- [ ] `pytest -q` 全绿。
- [ ] 现有学生列表/导入/导出/考勤/保险无回归（重点核对导入模板列数仍为 29、导出多 7 列是否符合预期）。
- [ ] 字典未导入场景：仍可正常新增学生（7 列留空）。
- [ ] 备份体积可接受。
- [ ] 回滚演练：DROP 表 + 删文件 + `ALTER TABLE DROP COLUMN` 不适用时用备份还原，学生数据无损。

---

## 14. 交付物（对应交接说明第 11 章）

1. **变更文件清单**：新增/修改/删除逐文件说明。
2. **数据库变更**：4.3 / 4.4 DDL、幂等说明、升级步骤、回滚 SQL。
3. **影响面评估**：
   - `students` 表：**新增 7 列**（4.8.1），旧数据默认空串，需回填脚本/按钮补全。
   - 学生列表/查询/统计/考勤/保险：无功能影响；字段权限新增 7 个可勾选项（非 admin 默认不可见）。
   - 学生导出：默认字段集多 7 列，需确认是否显式排除。
   - 导入模板：不变（硬编码 29 列）。
   - 备份：`student.db` 仅多 7 个 TEXT 列（可忽略）；`district.db` 不进备份、可重建。
   - 磁盘：+105 MB 字典 + 5.4 MB 数据包；内存 +50–90 MB。
   - 启动：不预加载，首访多 1.8 s（批量导入前需 `warm_cache()`）。
   - 录入性能：单条 +一次区划匹配（缓存命中 <50 ms）；字典缺失则跳过。
4. **依赖变更**：无；`requirements.txt` 不变。
5. **自测证据**：`pytest -q` 输出、12.2 结果、字典行数截图/日志。
6. **启动验证步骤**：从零启动 → 导入字典 → 单条 → 批量 → 导出 → 软删除。
7. **已知限制与风险**：见 15.3。
8. **提交记录**：按目标中文提交规范，示例：

```
feat(district-match): 接入身份证/地址区划代码匹配模块

- 新增 blueprints/district_match.py、services/district_match/*、models/district.py
- models/db.py 幂等新增 dm_match_batch/dm_match_result/dm_history_mapping 表
- 首次导入 district_pack.tsv.gz 构建 district.db（无 pandas）
- 新增 /admin/district-match 页面与 /api/district-match/* 接口
```

---

## 15. 系统方答复与交接表填写

### 15.1 按交接说明 10.1–10.4 填写的建议值

| 项 | 建议填写 |
|---|---|
| 功能名称 | 身份证/地址区划代码匹配 |
| 解决的问题 | 历史身份证区划码与不规范地址 → 现行省/市/区县 12 位代码 |
| 使用角色 | admin（MVP 不新增角色） |
| 入口 | 导航“区划匹配” → `/admin/district-match` |
| 输入 | 单条（身份证/地址）；批量 xlsx/xls/csv（身份证+地址，≤10MB）；可选「按现有学生筛选」（读取 `students.address`，已确认可用） |
| 输出 | 页面表格 + xlsx/csv 导出（10 列）；**录入/导入/编辑学生时自动写入 `students` 的 7 个区划列** |
| 成功与失败判定 | 7 种状态：正确/已解析/已解析(仅证件)/需复核/部分/无匹配/异常 |
| 交付形态 | Python 模块：Blueprint + services + models + 模板/JS + 数据包 |
| 是否常驻进程 | 否，复用主进程 |
| 是否额外端口 | 否 |
| 新增 pip 依赖 | 无（openpyxl/Flask 已有，不引入 pandas） |
| 是否依赖外网 | 否（数据包内置，纯离线） |
| 读取的表/字段 | `students.id_card/name/address`（地址已确认，受 `fields_visible` 限制）、`dm_district_code`、`dm_history_mapping` |
| 写入的表/字段 | `dm_match_batch`、`dm_match_result`、`dm_history_mapping`（新表）+ **`students` 7 列**（`district_*`，见 4.8.1） |
| 与 students 关联键 | `id_card`（辅助 `student_id`） |
| 数据规模 | 字典 664,239 行；单批建议 ≤50,000 行 |
| 并发要求 | 单机 5–10 人；字典导入串行；批量建议同时 1 个 |
| 是否新增角色 | 否（如需，另行提交权限矩阵） |
| 字段可见性 | 结果含身份证，默认仅 admin；非 admin 需脱敏 |
| 敏感字段 | 身份证号 → `auth.mask_sensitive_data()` |

### 15.2 系统方答复（2026-10-24）

**已确认**

| # | 问题 | 答复 |
|---|---|---|
| 1 | `ATTACH` 是否允许 | **允许**，按 0.2 的五条附加约束执行。备份**只备 `student.db`**（`databackup/student_A.db|student_B.db`），不复制 `data/` 目录，`district.db` 无需排除、也不进备份；缺失时可由内置数据包重建 |
| 1b | 字典 105 MB 导入连接 | **允许一次性独立连接**（唯一例外，仅限 admin 触发的后台导入任务，完成即关闭） |
| 2 | `students` 地址字段 | 有：`address`（「住址」），已在 `FIELD_MAPPING` / `ALL_STUDENT_FIELDS` / 导入模板 / `update_student.allowed_fields`；**受 `fields_visible` 限制**（非 admin 默认不可见）；不参与脱敏 |
| 3 | 结果是否回写 | **回写（2026-10-24 二次确认）**：录入学生时同步写入 6 列区划 + 1 列状态，详见 4.8.1；写入口集中在 3 个 model 函数，失败留空不阻断，不进导入模板 |

**其余问题（系统方建议值，按此实施；如业务方另有要求再调整）**

| # | 问题 | 建议 |
|---|---|---|
| 4 | 单文件 EXE +5.4 MB 数据包 | **接受**。数据包须写入 `run_with_tray.spec` 的 `datas`；单文件模式下 `_MEIPASS` 为只读临时目录，首次启动需复制到 `_USER_DATA_DIR` 后再导入 |
| 5 | 乡镇/村兜底 | **先做 1–4 级（约 6.4 MB）**：档案转递单只需到区县。若实际样本显示乡镇级能显著提升命中率，再切 5 级全量包（105 MB），schema 不变 |
| 6 | 历史映射页 | **只读列表 + 说明**（16 条种子 `INSERT OR IGNORE`）；增删改留到有明确需求时再做 |
| 7 | 导出是否复用 `export_template` 引擎 | **MVP 不复用**：结果表用 `openpyxl` 独立导出；转递单场景再评估（见 4.9） |
| 8 | 是否写 `update_logs` | **不写**。自动生成字段每次录入会刷 7 条噪音；人工「重新匹配」与批量回填同样不写。`update_logs` 只记录人工修改的业务字段 |
| 9 | 批量输入 | **两者都要**：上传 xlsx/xls/csv（≤10 MB，单批 ≤50,000 行）为主，「按现有学生筛选」为辅 |
| 10 | `init_db()` 连接与常量 | `init_db()` 在 `app.py` 导入阶段执行，**无 Flask app context，`g` 不可用**；此时 `get_db()` 返回裸 `sqlite3.Connection`（`row_factory=sqlite3.Row`，PRAGMA 已配）。字典库建表请在迁移连接上直接 `ATTACH`，勿依赖 `g`。路径用 `os.path.dirname(config.DATABASE_PATH)` 推导，**不要用 `config._USER_DATA_DIR`** |

### 15.3 风险清单

| 风险 | 影响 | 缓解 |
|---|---|---|
| `district.db` 缺失/损坏 | 功能不可用 | 明确降级提示「区划字典未导入」，不抛 500；字典可重建、不进备份 |
| 学生地址填写率低 | 按学生筛选命中率低 | 以「上传清单」为主路径；界面显示有/无地址人数，避免误判 |
| 字典导入时间长/锁库 | 影响录入 | 5,000 行分块提交；非工作时段导入 |
| 首访 1.8 s | 体验 | 管理员打开页面时后台预热 |
| 内存 +50–90 MB | 低配机 | 只缓存 L1–4；必要时改为 LRU/只缓存 L1–3 |
| 单文件包解压 5.4 MB | 启动略慢 | 可改外部数据包（需部署步骤） |
| 备份变大 | 方案 B 明显 | 推荐方案 A；字典库排除备份 |
| 录入链路强依赖引擎 | 字典缺失/异常会拖慢或写空 | 失败留空不阻断；字典导入前置检查；批量前 `warm_cache()`；单条目标 <50 ms |
| 批量导入变慢 | 3000 行逐条匹配 | 缓存预热 + 分块提交；必要时只对新增行计算，编辑行按“地址/证件是否变化”跳过 |
| 存量学生 7 列为空 | 旧档案转递单缺数据 | `backfill_district.py` + 页面按钮一次性补齐（`--only-empty`） |
| 学生导出多 7 列 | 用户困惑 | 列表导出显式传 fields 白名单，或接受新增列 |
| CSP 拦截内联脚本 | 页面失效 | JS 全外链、无 onclick |
| 目标 fixture 与连接行为差异 | 测试失败 | 先读 `models/db.py` 与 `conftest.py` 再落地 |

---

## 16. 附录

### A. 字段字典

**字典行（Repository 返回）**

| 字段 | 说明 |
|---|---|
| `id` | 等于 `district_code`（兼容原引擎） |
| `pid` | 等于 `parent_code`（兼容原引擎） |
| `pids` | 空字符串（引擎不使用） |
| `district_code` | 12 位代码 |
| `district_name` | 名称 |
| `admin_level` | 1–5 |
| `province_code` | 省级 12 位 |
| `city_code` | 市级 12 位（省级为空） |

**匹配结果**

| 字段 | 说明 |
|---|---|
| `province_code` / `province_name` | 省级 |
| `city_code` / `city_name` | 市级 |
| `district_code` / `district_name` | 区县级 |
| `match_status` | 7 种状态 |
| `engine1_detail` / `engine2_detail` | 过程文本 |
| `decision_path` | 决策路径 |

### B. 状态枚举

| 值 | 含义 |
|---|---|
| `正确` | 引擎②命中区县，且引擎①省/市一致 |
| `已解析` | 引擎②命中区县，未完整校验；或地址省市 + 证件区县 |
| `已解析(仅证件)` | 地址无效，仅引擎①得到区县 |
| `需复核` | 省市冲突、仅区县名无代码、省市信息不足 |
| `部分` | 仅省/市，无区县 |
| `无匹配` | 两引擎均无区县 |
| `异常` | 无有效输入或证件前 6 位异常 |

### C. 历史映射内置 16 条

```
230105→230104 太平区→道外区        231121→231183 嫩江县→嫩江市
231024→231086 东宁县→东宁市        232723→232701 漠河县→漠河市
230182→230113 双城市→双城区        210282→210214 普兰店市→普兰店区
310230→310151 崇明县→崇明区        320584→320509 吴江市→吴江区
320124→320117 溧水县→溧水区        320125→320118 高淳县→高淳区
320323→320312 铜山县→铜山区        330621→330603 绍兴县→柯桥区
500227→500120 璧山县→璧山区        440183→440118 增城市→增城区
320482→320413 金坛市→金坛区        321088→321012 江都市→江都区
```

### D. 原项目中有价值但不要搬的代码

| 代码 | 原因 |
|---|---|
| `DBManager.__init__` / `_connect` / `close` | 自建长连接，违反目标铁律 |
| `DBManager.insert_students` / student CRUD | 目标已有学生表与模型 |
| `DBManager.get_results_dataframe` | pandas 依赖 |
| `import_service` 的 pandas 读取 | 违反禁止 pandas |
| `export_service` 的 DataFrame 导出 | 同上 |
| `app.py` 全部 | 路由/托盘/端口/模板由目标负责 |
| `sdk/district_matcher.py` | 目标进程内直接调服务 |
| `utils/cleanup.py` | 原目录约定不适用 |

### E. 数据包生成脚本（完整版）

见 5.1。补充校验：

```python
# 导入后
assert counts[1] == 31
assert counts[2] == 343
assert counts[3] == 3255
assert counts[4] > 40000
assert counts[5] > 600000
```

### F. Repository 骨架

```python
# models/district.py
from models.db import get_db

class DistrictRepository:
    def __init__(self, conn=None):
        self.conn = ensure_attached(conn or get_db())

    def get_by_district_code(self, code, level=None):
        sql = ("SELECT district_code, district_name, admin_level, parent_code, "
               "province_code, city_code FROM dm.dm_district_code "
               "WHERE district_code=?")
        args = [code]
        if level is not None:
            sql += " AND admin_level=?"
            args.append(level)
        row = self.conn.execute(sql, args).fetchone()
        return adapt_row(row) if row else None

    def get_by_id(self, code):
        return self.get_by_district_code(code)

    def reverse_lookup(self, name, level, pid=None):
        cache = load_cache(self)
        rows = cache["by_name_level"].get((name, level), [])
        if pid:
            for r in rows:
                if r["pid"] == pid:
                    return r
        if rows:
            return rows[0]
        # level 5 或缓存未覆盖：走 SQL
        sql = ("SELECT ... FROM dm.dm_district_code "
               "WHERE district_name=? AND admin_level=?")
        args = [name, level]
        if pid:
            sql += " AND parent_code=?"
            args.append(pid)
        row = self.conn.execute(sql, args).fetchone()
        return adapt_row(row) if row else None
```

> 注意：`reverse_lookup` 的完整语义是“先别名后原名、先市范围后全局”，需在服务层或 Repository 内保持；上面仅为结构示意。

### G. 常见问题

**Q1：为什么不直接复制 365 MB 的 `district_matcher.db`？**
目标要求单文件 EXE 且禁止 pandas；365 MB 会让包体和备份不可接受。精简 schema + 5.43 MB 数据包是更优解。

**Q2：为什么不用原来的 `DBManager`？**
它自建长连接并假设单实例，直接违反目标“必须走 `get_db()`、禁止长连接/连接池”的铁律。缓存逻辑可以保留，连接与 CRUD 必须重写。

**Q3：为什么字典不放进 `student.db`？**
放进去会让每次备份多约 105 MB；且字典可由数据包重建，不需要备份。独立库 + ATTACH 更合适。

**Q4：没有地址字段怎么办？**
MVP 支持上传“身份证+地址”清单；不要擅自给 `students` 加列。若业务强依赖学生表地址，提交 Phase 2 变更评审。

**Q5：为什么导出不自动删结果？**
目标要求删除一律软删除。保留结果并支持软删除更安全；导出文件可单独清理。

**Q6：能不能继续用 pandas？**
不能。目标明确禁止（打包体积）。导入导出全部用 openpyxl + csv/gzip。

**Q7：首次匹配很慢？**
首次加载 44,903 行缓存约 1.8 s；之后微秒级。可在管理员打开页面时后台预热。

**Q8：乡镇/村数据有必要吗？**
地址只写到村/镇时，靠 level 4/5 反推区县。去掉后这类地址会降级为“需复核/无匹配”。推荐保留全量包（仅多 5 MB）。

### H. 原项目版本行为要点

| 版本 | 行为 |
|---|---|
| v1.3 | 连接复用、批量事务、区划缓存、后台批处理、进度 |
| v1.4 | 导入预览、多 sheet 合并事务、DataFrame 导出（目标不采用） |
| v1.5 | `with_detail` 惰性、配置缓存 |
| v1.6 | 导入后台任务 + 进度轮询 |
| v1.7 | 修复进度条重放 POST 死循环 |
| v1.8 | 县级市/县匹配改进、兜底解包修复 |
| v1.9 | 修复引擎①省市 + 引擎②区县混用 |
| v2.0 | 决策引擎重构：地址为主、层级 3 为锚点、7 状态 |
| v2.1 | 自治区简称展开、乡镇/村抽取顺序修复 |
| v2.2 | uploads 防膨胀 |
| v2.3 | 历史映射库化、种子、管理页/API |
| 当前 | 区划缓存按需加载（L1–4 常驻，L5 走 SQL） |

### I. 原项目与目标系统的关键差异总表

| 维度 | 原项目 | 目标系统 |
|---|---|---|
| 运行形态 | 独立 Flask + 托盘 + 打包 | 主系统内的 Blueprint |
| 数据访问 | `DBManager` 长连接 | `get_db()` 请求级连接 |
| 区划数据 | 365 MB 独立库 | 5.43 MB 包 → 105 MB 独立库 |
| 导入导出 | pandas | openpyxl + csv（无 pandas） |
| 鉴权 | 无 | 6 角色 + 装饰器 |
| CSRF | 无 | 全局校验 |
| 删除 | 物理删除 | 软删除 |
| UI | Bootstrap 自定义页 | 继承 base.html + 目标 JS 规范 |
| 测试 | unittest | pytest + conftest |
| 路径 | 项目目录 | `_USER_DATA_DIR` |
| 响应 | `{code:0}` | `{success:true}` |

---

## 17. 一句话交接

> 把本项目当作“**算法库 + 数据包**”接入目标系统：算法原样迁移，数据访问改成 `get_db()` + Repository，字典用 5.43 MB 数据包首次生成独立库（**ATTACH 已获批准**，备份只备 `student.db`），界面做成 admin Blueprint 页面，全程不用 pandas、不加端口；`students.address` 可直接读取（受字段权限限制），**录入/导入/编辑时同步回写 6 列区划代码 + 1 列状态**（4.8.1，失败留空不阻断、不进导入模板），并提供 4.9 的可复用查询接口，最后按第 13 章分阶段落地。
