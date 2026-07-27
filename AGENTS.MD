# AGENTS.MD - 学生身份证区划代码转换工具

## 项目概述

开发一个基于双引擎策略的学生身份证区划代码转换工具。引擎①基于身份证前6位层级匹配行政区划，引擎②基于地址文本解析，两者交叉校验输出最优结果。系统包含数据初始化、输入处理、引擎计算、结果输出四大模块，支持后续集成到其他程序。

---

## 技术栈

| 层级 | 技术选型 | 说明 |
|------|----------|------|
| 后端语言 | Python 3.10+ | 主开发语言 |
| 数据库 | SQLite (.db) | 轻量级，零配置，便于分发和集成 |
| 前端框架 | Flask + Bootstrap 5 | 轻量Web界面，便于后期集成 |
| 数据处理 | pandas, openpyxl | Excel读写，数据批量处理 |
| 打包分发 | PyInstaller | 可打包为独立可执行文件 |

---

## 系统架构
┌──────────────────────────────────────────────────┐
│ 前端层 (Flask + Bootstrap) │
│ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────┐ │
│ │ 数据初始化 │ │ 批量导入 │ │ 单条查询 │ │ 导出 │ │
│ └──────────┘ └──────────┘ └──────────┘ └──────┘ │
├──────────────────────────────────────────────────┤
│ 业务逻辑层 │
│ ┌──────────────────────────────────────────┐ │
│ │ 引擎协调器 │ │
│ │ ┌─────────────┐ ┌──────────────────┐ │ │
│ │ │ 引擎① │ │ 引擎② │ │ │
│ │ │ 身份证匹配 │ │ 地址文本解析 │ │ │
│ │ └─────────────┘ └──────────────────┘ │ │
│ │ ┌──────────────────────────────────┐ │ │
│ │ │ 双引擎校验决策器 │ │ │
│ │ └──────────────────────────────────┘ │ │
│ └──────────────────────────────────────────┘ │
├──────────────────────────────────────────────────┤
│ 数据访问层 │
│ ┌──────────────────────────────────────────┐ │
│ │ SQLite 数据库 │ │
│ │ ┌────────┐ ┌────────┐ ┌─────────────┐ │ │
│ │ │区域代码│ │学生数据│ │ 处理结果 │ │ │
│ │ │ 表 │ │ 表 │ │ 表 │ │ │
│ │ └────────┘ └────────┘ └─────────────┘ │ │
│ └──────────────────────────────────────────┘ │
├──────────────────────────────────────────────────┤
│ 外部接口层 │
│ ┌──────────────────────────────────────────┐ │
│ │ RESTful API / Python SDK │ │
│ │ 供其他程序集成调用 │ │
│ └──────────────────────────────────────────┘ │
└──────────────────────────────────────────────────┘

---

## 数据库设计

### 数据库文件

- 文件名：`district_matcher.db`
- 位置：`database/` 子目录
- 编码：UTF-8

### 数据生命周期（一进一出）

本工具定位为**流水线**：学生数据导入 → 双引擎处理 → 导出，数据不在数据库长期驻留。
- `t_district_code`（区划字典）为参考数据，长期保留，是匹配的唯一依据。
- `t_student` / `t_result` 为**临时业务流水**：导入产生、处理写入、导出成功后由系统自动清除（`export_service` 在 `cleanup=True` 时调用 `DBManager.delete_batch`）。
- 数据库文件不会随导入次数线性膨胀；只有"清空/重置数据库"才会连带清除区划字典。

### 性能优化（v1.3）

针对"数据流转分析"识别的高影响瓶颈，批量匹配吞吐已显著提升：

- **P01 连接复用**：`DBManager` 由每次查询 `connect()`/`close()` 改为进程内单一长连接（`check_same_thread=False` + `threading.Lock` 保护写），消除每名学生约 11 次建连开销。
- **P02 批量事务**：新增 `insert_results_batch`，`process_batch` 收集结果后 `executemany` 单次提交，替代逐条 `insert_result` + 每次 `commit`（N 条学生由 N 次事务降为 1 次）。
- **P03 区划字典内存缓存**：`t_district_code` 为只读小表，首次访问全量载入内存，`get_by_district_code`/`get_by_id`/`reverse_lookup`/`search_by_name` 走内存字典；`insert_districts`/`reset_db` 时自动失效，消除每条学生重复查库开销。
- **P05 决策器复用引擎②代码**：`decider` 优先复用引擎②已反查到的 `province_code`/`city_code`/`district_code`，仅在缺失时按名称补查，消除同一条流水内对相同区划的重复反查。
- **P04 后台处理**：批量匹配改为后台线程执行，新增 `GET /api/result/progress?batch_id=` 进度接口与前端进度条轮询，避免大批次同步阻塞 HTTP 请求（不再受 Flask 请求超时限制）。

验证：`tests/verify_perf.py` 在 4000 条学生上测得约 5.7 万条/秒（改造前为每条多次建连 + 逐条提交 + 重复查库），原有 15 个单元测试全部通过。

### 界面与导入/导出优化（v1.4）

针对一致性核对 D3 与数据流转分析 P06–P08：

- **D3 导入预览→确认**：新增 `POST /import/preview` 路由与 `import_service.preview_student_file`，上传后展示**前 10 行**及自动识别的列映射（身份证号/地址/姓名），确认无误再点"导入"，消除盲目导入。导入页与结果页均新增**操作日志**面板（最近 50 条，覆盖导入/处理/导出/初始化），导入按钮提交后显示"导入中…"反馈。
- **P06 向量化导入**：`import_students` 与 `_build_district_rows` 用 `df.to_dict("records")` 替代 `df.iterrows()`，消除逐行 Series/索引转换开销；并修正空值/`NaN`/`"nan"` 身份证被正确跳过（原 `iterrows` 版本会把空身份证当作 `"nan"` 导入）。
- **P07 区划入库合并事务**：`import_district_excel` 将所有 sheet 行先累计，最后**单次 `insert_districts` 提交**，避免每个省（31 省 = 31 次事务）；缺失省级行的自动合成保留原语义（按 sheet 名补省名、下属市级 pid 指向合成省），并复用 P03 内存缓存判断"DB 已存在该省则跳过合成"。
- **P08 导出直读 DataFrame**：`export_service.export_results` 改为经 `DBManager.get_results_dataframe`（`pd.read_sql` **一步取数**）直接生成 DataFrame，再 `rename` 为中文列名写出，去掉原先 `Row→dict→中文键 dict→DataFrame` 的**三次格式转换**与整表复制；空结果也能正确输出中文表头。

验证：`tests/verify_d3_p6_p8.py` 覆盖预览函数/路由、P06 向量化与空值跳过、P07 多 sheet 合并事务与省合成、P08 直读 DataFrame 导出；原有 15 个单元测试全部通过。

### 导入进度显示（v1.6）

针对"数据初始化导入（区划字典全量，约 7 万行 / 31 个 sheet）与学生导入无进度反馈、同步阻塞请求"的问题，将 P04 的后台线程 + 进度轮询机制从"批量匹配"推广到"导入"：

- **后台导入任务**：新增 `_start_import_task(kind, ...)`，在后台线程执行 `import_service.import_district_excel` / `import_students`，进度写入模块级注册表 `_import_tasks`（key=task_id）。
- **进度回调 `on_progress`**：`import_district_excel` 与 `import_students` 新增可选 `on_progress(done, total, info)` 入参；区划导入以"数据表数"为粒度上报（reading → writing → done），学生导入以"行数"为粒度上报（building → writing → done），前端据此渲染进度条与阶段文案。
- **新路由**：`POST /api/import/start`（接收 `kind=district|student`，区划支持 `action=import_default|upload`，学生支持列名映射）启动任务并返回 `task_id`；`GET /api/import/progress?task_id=` 返回实时进度（status/phase/done/total/detail/imported/skipped/batch_id）。
- **前端**：`init.html` 默认目录导入按钮与上传表单、`import.html` 学生导入按钮均改为 AJAX 触发，展示进度面板并轮询；完成后 `sessionStorage` 暂存结果、刷新页面更新统计与批次列表；失败显示错误详情。

验证：`tests/verify_import_progress.py` 覆盖 on_progress 回调（区划 5 次 / 学生 119 次，done/total 与阶段正确）与 Flask 路由端到端（学生上传 + 区划上传进度轮询至 done、未知类型被拒、未知 task_id 返回 idle）；原有 15 单测 + 全套验证脚本无回归。

### 表结构设计

#### 1. 行政区划代码表 `t_district_code`

存储当前有效的行政区划代码映射数据。

```sql
CREATE TABLE t_district_code (
    id              TEXT PRIMARY KEY,          -- UUID，对应Excel中的ID
    pids            TEXT,                       -- 所有上级ID串联字符串
    pid             TEXT,                       -- 直接上级ID
    district_code   TEXT NOT NULL,              -- 12位行政区域代码（如230102000000）
    district_name   TEXT NOT NULL,              -- 行政区域名称
    admin_level     INTEGER NOT NULL,           -- 行政层级：1=省, 2=市, 3=区县, 4=街道/镇, 5=村/社区
    province_code   TEXT,                       -- 所属省级代码（前2位+0000），用于快速索引
    city_code       TEXT,                       -- 所属市级代码（前4位+00），用于快速索引
    created_at      TEXT DEFAULT (datetime('now','localtime'))
);

-- 索引设计
CREATE INDEX idx_district_code ON t_district_code(district_code);
CREATE INDEX idx_admin_level ON t_district_code(admin_level);
CREATE INDEX idx_pid ON t_district_code(pid);
CREATE INDEX idx_province_code ON t_district_code(province_code);
CREATE INDEX idx_city_code ON t_district_code(city_code);
CREATE INDEX idx_district_name ON t_district_code(district_name);
CREATE INDEX idx_name_level ON t_district_code(district_name, admin_level);
字段说明：

字段	说明	示例
id	原始UUID，保留Excel中的ID	0de02405-a4f9-460d-...
pids	所有上级ID	省ID,市ID
pid	直接上级ID	市ID
district_code	12位完整代码	230102000000
district_name	行政区域名称	道里区
admin_level	行政层级	3
province_code	前2位+0000（冗余字段，加速查询）	230000
city_code	前4位+00（冗余字段，加速查询）	230100
2. 学生数据表 t_student
存储待处理的学生信息。
CREATE TABLE t_student (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id        TEXT,                       -- 批次号，用于批量导入标识
    student_name    TEXT,                       -- 学生姓名
    id_card         TEXT NOT NULL,              -- 身份证号（18位）
    address         TEXT,                       -- 详细地址
    created_at      TEXT DEFAULT (datetime('now','localtime'))
);

CREATE INDEX idx_batch_id ON t_student(batch_id);
3. 处理结果表 t_result
存储引擎处理后的结果。
CREATE TABLE t_result (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id        TEXT,                       -- 所属导入批次（自包含，无需 JOIN t_student）
    student_name    TEXT,                       -- 学生姓名
    id_card         TEXT,                       -- 身份证号
    address         TEXT,                       -- 原始地址
    
    -- 省级结果
    province_code   TEXT,                       -- 省级12位代码（如230000000000）
    province_name   TEXT,                       -- 省级名称
    
    -- 市级结果
    city_code       TEXT,                       -- 市级12位代码（如230100000000）
    city_name       TEXT,                       -- 市级名称
    
    -- 区县级结果
    district_code   TEXT,                       -- 区县级12位代码（如230102000000）
    district_name   TEXT,                       -- 区县级名称
    
    -- 匹配元数据
    match_status    TEXT,                       -- 正确/已解析/已解析(仅证件)/需复核/部分/无匹配/异常
    engine1_result  TEXT,                       -- 引擎①匹配过程（with_detail 时构建）
    engine2_result  TEXT,                       -- 引擎②匹配过程（with_detail 时构建）
    remark          TEXT,                       -- 决策路径备注
    created_at      TEXT DEFAULT (datetime('now','localtime')),
    
    -- P10：结果表自包含 batch_id，不再以 student_id 关联 t_student；
    -- 查询/分页/导出/清理均按 batch_id，去除冗余 JOIN。
);

CREATE INDEX idx_result_batch ON t_result(batch_id);
CREATE INDEX idx_result_status ON t_result(match_status);
4. 历史代码映射表 t_history_mapping（预留）
用于后期扩展，建立旧代码到新代码的直接映射。
CREATE TABLE t_history_mapping (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    old_code        TEXT NOT NULL,              -- 历史6位代码
    new_code        TEXT NOT NULL,              -- 现行6位代码
    old_name        TEXT,                       -- 历史名称
    new_name        TEXT,                       -- 现行名称
    change_type     TEXT,                       -- 变更类型：撤销/合并/改名/拆分
    change_date     TEXT,                       -- 变更日期
    remark          TEXT
);

CREATE INDEX idx_old_code ON t_history_mapping(old_code);
代码格式转换规则
身份证前6位 → 12位区划代码
规则：
  身份证前6位 = ABCDEF
  
  12位省级匹配代码 = AB0000000000   （前2位 + 10个0）
  12位市级匹配代码 = ABCD00000000   （前4位 + 8个0）
  12位区县匹配代码 = ABCDEF000000   （前6位 + 6个0）
  
  最终输出：三级各12位代码
  省级输出：AB0000000000
  市级输出：ABCD00000000
  区县输出：ABCDEF000000
  示例（身份证前6位 = 230105）：

层级	提取方式	6位	12位匹配代码	说明
省级	前2位 + "0000"	230000	230000000000	用于匹配省级
市级	前4位 + "00"	230100	230100000000	用于匹配市级
区县	全部6位	230105	230105000000	用于匹配区县级
最终输出示例（230105→道外区）：
省级代码：230000000000  省级名称：黑龙江省
市级代码：230100000000  市级名称：哈尔滨市
区县代码：230102000000  区县名称：道外区（引擎②反查得到）
匹配状态：已解析
核心引擎设计
引擎①：身份证前6位层级匹配
功能：从身份证提取前6位，构造12位匹配代码，在数据库中逐层匹配。
# 伪代码逻辑
def engine1_match(id_card_first6):
    """
    输入：身份证前6位（如"230105"）
    输出：{
        "province": {"code": "230000000000", "name": "黑龙江省", "matched": True},
        "city": {"code": "230100000000", "name": "哈尔滨市", "matched": True},
        "district": {"code": None, "name": None, "matched": False}
    }
    """
    # 构造12位匹配代码
    province_code = id_card_first6[:2] + "0000" + "000000"   # 230000000000
    city_code = id_card_first6[:4] + "00" + "000000"         # 230100000000
    district_code = id_card_first6 + "000000"                 # 230105000000
    
    result = {
        "province": query_db(district_code=province_code, admin_level=1),
        "city": query_db(district_code=city_code, admin_level=2),
        "district": query_db(district_code=district_code, admin_level=3)
    }
    
    # 排除"市辖区"虚拟节点
    if result["district"]["name"] == "市辖区":
        result["district"]["matched"] = False
    
    return result
    匹配状态判断：
    省匹配	市匹配	区县匹配	状态
✅	✅	✅	引擎①完全成功
✅	✅	❌	引擎①部分成功（市区已知，区县需引擎②补充）
✅	❌	-	引擎①仅省级匹配成功
❌	-	-	引擎①失败，完全依赖引擎②
引擎②：地址文本解析
功能：从地址文本中提取省、市、区县三级行政区划名称。
# 伪代码逻辑
def engine2_parse(address):
    """
    输入：完整地址文本
    输出：{
        "province": "黑龙江省",
        "city": "哈尔滨市", 
        "district": "道外区"
    }
    """
    # 正则提取省/市/区县
    province = extract_by_suffix(address, ["省", "自治区", "直辖市"])
    city = extract_by_suffix(address, ["市", "自治州", "地区", "盟"])
    district = extract_by_suffix(address, ["区", "县", "市", "自治县"])
    
    return {"province": province, "city": city, "district": district}
    引擎②：区县名称反查
功能：根据引擎②解析出的区县名称，反查12位行政区划代码。
# 伪代码逻辑
def engine2_reverse_lookup(district_name, city_pid=None):
    """
    根据区县名称反查12位代码
    输入：区县名称, 市级PID（可选）
    输出：{"code": "230102000000", "name": "道外区"}
    """
    if city_pid:
        # 限定范围反查（在已知市级ID下查找）
        return query_db(name=district_name, admin_level=3, pid=city_pid)
    else:
        # 全局反查
        return query_db(name=district_name, admin_level=3)
地址解析结果：道外区
引擎①已知：哈尔滨市（PID=0de02405...）
反查条件：行政层级=3 AND 名称="道外区" AND PID=哈尔滨市ID

反查命中：行政区域代码 = "230102000000"
输出：230102000000（12位区县代码）
双引擎决策器（v2.0 重构）
设计原则：
- 引擎②（地址解析）为绝对主引擎；行政层级3（区县/县级市）是判定锚点，只要层级3命中即以引擎②结果为准。
- 引擎①（身份证）降级为校验器 + 兜底补充器：仅当「地址为空值」或「引擎②无法解析层级3」时提供区县；其余仅做省/市一致性校验，影响置信度，不决定归属。
- 证件与地址省/市冲突时，仍输出引擎②结果并标记「需复核」供人工核查。
- 省/市一致性校验改用名称比对（比代码前缀鲁棒，天然兼容直辖市市代码表示差异，如 1100 vs 1101）。

# 伪代码逻辑（v2）
def dual_engine_decide(e1, e2):
    e2_l3 = bool(e2 and e2.district_code)          # 引擎②层级3命中（主锚点）
    e1_district_ok = bool(e1 and e1.district and e1.district.matched)

    # 情况A：引擎②解析出层级3 → 以引擎②全量结果为准
    if e2_l3:
        prov_ok = (e1 and e1.province and e1.province.matched
                   and e2.province and e1.province.name == e2.province)
        city_ok = (e1 and e1.city and e1.city.matched
                   and e2.city and e1.city.name == e2.city)
        if prov_ok and city_ok:
            return {**e2.fields, "status": "正确"}
        if not e1_district_ok:                      # 引擎①无区县可校验
            return {**e2.fields, "status": "已解析"}
        return {**e2.fields, "status": "需复核"}      # 省/市冲突，交人工

    # 情况B：引擎②无层级3，但有省/市 + 引擎①区县命中 → 组合
    if e2 and (e2.province or e2.city) and e1_district_ok:
        return {
            "province_code": e2.province_code or e1.province.code,
            "city_code":     e2.city_code or e1.city.code,
            "district_code": e1.district.code,
            "district_name": e1.district.name,
            "status": "已解析",
        }

    # 情况C：引擎②仅解析出区县名但无代码（反查失败）→ 人工复核
    if e2 and e2.district and not e2.district_code:
        return {**e2.fields, "status": "需复核"}

    # 情况D：引擎②完全无输出 → 纯引擎①兜底
    if e1_district_ok:
        return {**e1.fields, "status": "已解析(仅证件)"}
    if e1 and ((e1.province and e1.province.matched) or (e1.city and e1.city.matched)):
        return {"status": "部分"}
    if e1 and e1.error:
        return {"status": "异常"}
    return {"status": "无匹配"}

决策规则汇总表（v2）：

优先级	引擎②层级3	引擎①区县	省/市校验	输出代码来源	状态标识
A1	✅ 命中	✅ 命中	名称一致	引擎②全量（省/市/区县）	正确
A2	✅ 命中	✅ 命中	省或市冲突	引擎②全量（省/市/区县）	需复核
A3	✅ 命中	❌ 无区县	-	引擎②全量	已解析
B1	❌ 无代码	✅ 命中	-	引擎②省/市 + 引擎①区县	已解析
C1	仅区县名	-	-	引擎②（无代码）	需复核
D1	❌ 无输出	✅ 命中	-	引擎①全量	已解析(仅证件)
D2	❌ 无输出	❌ 仅省/市	-	仅省/市	部分
D3	❌ 无输出	异常	-	-	异常
D4	❌ 无输出	❌ 无	-	-	无匹配

引擎②强化要点（v2.0 配套）
- 自治区简称展开：地址中常省略“回族自治区/维吾尔自治区/自治区”后缀，如“宁夏彭阳县”“内蒙古呼伦贝尔市”。`address_parser` 在入口将简称前缀（内蒙古、广西、西藏、宁夏、新疆）展开为全称，使省正则正确剥离；避免市/区县正则吃进省前缀（如“内蒙古呼伦贝尔市”被整体当市名）或区县名含省前缀（如“宁夏彭阳县”）。
- 乡镇/村抽取在剔除区县名后进行：长区县名（如“莫力达瓦达斡尔族自治旗”）不再被乡镇/村正则“钻取”，避免“红彦镇新多村”被截成“力达瓦...红彦镇”。
- 县级市省略市：地址层级3为「XX市」（县级市）时省略层级2的地级市，引擎②显式走“先识别县级市为层级3 → 向上补父级市/省”路径，确保反查成功。
- 乡镇/村辅助反推：新增 _lookup_by_ancestor，沿父链向上核对省/市归属范围；引擎②抽出乡镇/村（level4/5）token 后，优先在已识别市(2)范围内反查上层 level3，避免同名跨区错配（如“黑河市曙光村”在黑河辖区无数据则正确返回无匹配）。
- 历史别名扩展：_HISTORICAL_NAME_MAP 由 5 条扩至 15 条（新增 崇明县→崇明区、吴江市→吴江区、溧水县→溧水区、高淳县→高淳区、铜山县→铜山区、绍兴县→柯桥区、璧山县→璧山区、增城市→增城区、金坛市→金坛区、江都市→江都区），方向统一为旧名→新名（库仅存新名）。
- 直辖市/省直管县：_apply_county 在直辖市与省直管县分支中保留 city 名称（不再置空），决策器改用名称比对校验。
边界情况处理
1. 历史代码变更（核心场景：230105太平区→道外区）
输入：身份证前6位=230105，地址="哈尔滨市道外区..."

引擎①处理：
- 省级：230000000000 → 命中 "黑龙江省"
- 市级：230100000000 → 命中 "哈尔滨市"
- 区县：230105000000 → 未命中（太平区已撤销）

引擎②处理：
- 解析地址 → "道外区"
- 反查：层级=3, 名称="道外区", PID=哈尔滨市ID
- 命中：230102000000

输出：
  省级代码：230000000000，省级名称：黑龙江省
  市级代码：230100000000，市级名称：哈尔滨市
  区县代码：230102000000，区县名称：道外区
  匹配状态：已解析
2. 直辖市处理
省级代码：11（北京）、12（天津）、31（上海）、50（重庆）

市级匹配时，直辖市的市级即省级本身，代码取省级代码

区县级直接从地址中提取

3. 省直辖县级单位
如济源市（河南省）、仙桃市（湖北省）

表中行政层级=3但PID直接指向省级

身份证前4位匹配市级时会失败，需绕过市级匹配直接尝试区县匹配

4. 地址格式不规范
缺少省级信息：根据市级名称反查所属省份

使用旧地名：如地址中仍写"太平区"，需建立旧名→新名映射

地址与身份证完全不符：标记"无匹配"

5. 代码格式异常
身份证前6位非数字：直接标记异常

代码不在任何已知范围：完全依赖引擎②

前端设计
页面结构
┌─────────────────────────────────────────────────────┐
│  学生身份证区划代码转换工具                            │
├─────────────────────────────────────────────────────┤
│  [数据初始化]  [学生导入]  [批量处理]  [生成导出]     │
├─────────────────────────────────────────────────────┤
│                                                       │
│  当前页面内容区                                        │
│                                                       │
└─────────────────────────────────────────────────────┘
页面1：数据初始化
功能：导入"当前行政区划代码映射表"（Excel格式）

界面元素：

文件上传控件（支持.xlsx）

当前数据库状态展示（各层级记录数量）

初始化按钮

操作日志输出区域

清空/重置数据库按钮

交互流程：
选择Excel文件 → 预览数据 → 确认导入 → 显示导入结果
页面2：学生数据导入
功能：导入学生身份证和地址数据

界面元素：

文件上传控件（支持.xlsx/.csv）

列映射配置（指定身份证号列、地址列、姓名列）

导入预览表格（前10条）

导入进度条

历史批次列表

导入模板格式：

姓名	身份证号	地址
张三	230105198907140000	哈尔滨市道外区水泥路99-4号
李四	110108200003154578	北京市海淀区中关村大街1号
页面3：批量处理与结果展示
功能：执行双引擎匹配，展示处理结果

界面元素：

批次选择下拉框

处理进度条

结果表格（分页展示）

状态筛选器（全部/正确/已解析/无匹配/异常）

单条详情查看（点击展开）

结果导出按钮（Excel/CSV）

结果表格列：
姓名	身份证号	地址	省	省级代码	市	市级代码	区县	区县代码	状态	操作
张三	230105...	哈尔滨市道外区...	黑龙江省	230000000000	哈尔滨市	230100000000	道外区	230102000000	已解析	详情
详情弹窗内容：

引擎①匹配过程（12位代码构造、各级匹配结果）

引擎②解析过程（提取的地址片段）

双引擎决策路径

最终取值依据

页面4：单条查询
功能：手动输入单条数据进行查询测试

界面元素：

身份证号输入框

地址输入框

查询按钮

结果展示卡片
API接口设计（预留集成接口）
RESTful API
单条查询接口：
POST /api/match
请求体：
{
    "id_card": "23010519890714291X",
    "address": "哈尔滨市道外区水泥路99-4号"
}

响应：
{
    "code": 0,
    "data": {
        "province_code": "230000000000",
        "province_name": "黑龙江省",
        "city_code": "230100000000",
        "city_name": "哈尔滨市",
        "district_code": "230102000000",
        "district_name": "道外区",
        "match_status": "已解析"
    }
}
批量处理接口：
POST /api/match/batch
请求体：
{
    "students": [
        {"name": "张三", "id_card": "23010519890714291X", "address": "哈尔滨市道外区..."},
        ...
    ]
}
Python SDK
# 作为Python模块被其他程序调用（需将 district_matcher 目录加入 sys.path）
import sys
sys.path.insert(0, "/path/to/district_matcher")   # 包含 sdk/ 的目录
from sdk.district_matcher import DistrictMatcher

matcher = DistrictMatcher(db_path="district_matcher.db")
result = matcher.match("23010519890714291X", "哈尔滨市道外区水泥路99-4号")
print(result)
# {
#     "province_code": "230000000000",
#     "province_name": "黑龙江省",
#     "city_code": "230100000000", 
#     "city_name": "哈尔滨市",
#     "district_code": "230102000000",
#     "district_name": "道外区",
#     "match_status": "已解析"
# }
项目文件结构
district_matcher/
├── app.py                      # Flask主应用
├── config.py                   # 配置文件
├── database/
│   ├── __init__.py
│   ├── models.py               # 数据模型定义
│   ├── db_manager.py           # 数据库连接管理
│   └── district_matcher.db     # SQLite数据库文件
├── engine/
│   ├── __init__.py
│   ├── engine1.py              # 引擎①：身份证匹配
│   ├── engine2.py              # 引擎②：地址解析
│   └── decider.py              # 双引擎决策器
├── services/
│   ├── __init__.py
│   ├── import_service.py       # 数据导入服务
│   ├── match_service.py        # 匹配服务
│   └── export_service.py       # 结果导出服务
├── templates/
│   ├── base.html               # 基础模板
│   ├── init.html               # 数据初始化页面
│   ├── import.html             # 学生导入页面
│   ├── result.html             # 结果展示页面
│   └── query.html              # 单条查询页面
├── static/
│   ├── css/
│   └── js/
├── utils/
│   ├── __init__.py
│   ├── address_parser.py       # 地址解析工具
│   └── validators.py           # 数据校验工具
├── sdk/
│   └── district_matcher.py     # 对外SDK接口
├── tests/
│   ├── test_engine1.py
│   ├── test_engine2.py
│   └── test_decider.py
├── requirements.txt
├── AGENTS.MD                   # 本文档
└── README.md

开发阶段规划
第一阶段：核心引擎 + 数据库
数据库建表与初始化

行政区划数据导入功能

引擎①实现

引擎②实现

双引擎决策器实现

单元测试（重点测试230105场景）

第二阶段：Web前端
Flask应用框架搭建

数据初始化页面

学生数据导入页面

批量处理与结果展示页面

单条查询页面

第三阶段：集成接口
RESTful API封装

Python SDK封装

错误处理与日志完善

性能优化（批量处理）

运行方式与配置
- 启动入口：`python app.py`（或 `python -m app`）。默认以系统托盘模式运行（需桌面环境；
  无图形界面时自动回退为前台运行）；`--no-tray` 可强制前台运行。
- 配置文件 `config.txt`（项目根目录，UTF-8，每行 `KEY=VALUE`）：
  - `PORT`：HTTP 端口，默认 5000（越界或非整数回退 5000）
  - `TRAY`：是否启用托盘（on/true/yes/1/开启/是 视为开，默认开）
  - `OPEN_BROWSER`：启动后是否自动打开浏览器（默认开）
- 模板下载路由：`/template/student`（学生导入模板）、`/template/district`（区划导入模板）
- 说明：托盘模式、`config.txt` 配置、模板下载路由等为后续轮次新增功能，原始设计文档未涵盖，特此补记（D8）。

第四阶段：扩展功能
历史代码映射表功能

地址解析增强（旧地名识别）

数据统计看板

自动更新行政区划数据
输出格式说明
最终输出包含三级各12位代码
层级	代码格式	示例	说明
省级	XX0000000000	230000000000	前2位+10个0
市级	XXXX00000000	230100000000	前4位+8个0
区县级	XXXXXX000000	230102000000	前6位+6个0
完整输出示例
姓名	身份证号	地址	省	省级代码	市	市级代码	区县	区县代码	状态
张三	230105198907140000	哈尔滨市道外区水泥路99-4号	黑龙江省	230000000000	哈尔滨市	230100000000	道外区	230102000000	已解析
版本记录
版本	日期	修订内容
v1.0	2026-07-27	初始版本，包含数据库设计、前端设计、引擎设计、API设计
v1.1	2026-07-27	数据流方向矫正：明确学生数据为"一进一出"临时流水，导出成功后自动清理 t_student/t_result，数据库仅长期保留区划字典 t_district_code
v1.2	2026-07-27	修复一致性差异 D1-D6：列映射配置 UI（import.html 列名输入框，留空自动识别）、结果分页控件（每页50条，db_manager.count_results 支持）、SDK 独立集成（sdk 自动加项目根到 sys.path，用法改为 from sdk.district_matcher import DistrictMatcher）、身份证校验接入（run_match 用 utils.validators 校验格式，异常标"异常"）；AGENTS.MD 同步。
v1.3	2026-07-27	性能优化（数据流转分析 P01-P05）：DBManager 连接复用（进程内长连接 + 写锁）、结果批量事务写入（insert_results_batch）、区划字典内存缓存、决策器复用引擎②已反查代码、批量匹配改为后台线程并新增 /api/result/progress 进度接口与前端进度条；新增 tests/verify_perf.py。DB 文件位置说明修正为 database/ 子目录。
v1.4	2026-07-27	D3 导入预览→确认（/import/preview 路由 + 前10行预览 + 操作日志面板 + 导入中反馈）；P06 import_service 向量化（to_dict 替代 iterrows + 空值/Nan 正确跳过）；P07 区划多 sheet 合并为单次事务入库；P08 导出直读 DataFrame（pd.read_sql 一步取数 + rename，去掉三次格式转换）；新增 tests/verify_d3_p6_p8.py。
v1.5	2026-07-27	收尾：P10 t_result 自包含 batch_id（去 student_id 外键与冗余 JOIN，旧库自动 ALTER 迁移）；P11 删除 run_match 冗余 engine1_result/engine2_result 动态属性；P12 run_match 增加 with_detail 惰性（API/SDK 单条匹配默认跳过 detail 构建）；P13 config.txt 模块级缓存；P14 默认 Excel 路径 30s TTL 缓存；D4 决策规则2措辞同步为增强版（引擎②反查失败保留引擎①）；D8 补记托盘/配置/模板路由；新增 tests/verify_final.py，原有 15 单测 + 全套验证脚本无回归。
v1.6	2026-07-27	导入进度显示：将 P04 后台线程 + 进度轮询机制推广到导入环节。import_service 两导入函数新增可选 on_progress 回调（区划按数据表数、学生按行数上报 reading/building→writing→done）；新增 _start_import_task 后台任务与 _import_tasks 注册表；新增 POST /api/import/start + GET /api/import/progress 路由；init.html（区划默认/上传）与 import.html（学生）改为 AJAX 触发并展示进度条 + 阶段文案，完成后 sessionStorage 暂存结果并刷新统计；新增 tests/verify_import_progress.py，原有 15 单测 + 全套验证脚本无回归。
v1.7	2026-07-27	修复批量处理（/result）进度条无限循环：根因为"执行双引擎匹配"为表单 POST 提交，前端 done 分支用 location.reload() 会重放 POST 再次触发处理，形成刷新/处理死循环。改为 done 后通过 window.location.href = '/result?batch_id=...'（GET）重定向刷新结果页，并移除进度条 animated 动画；init.html/import.html 因导入走 AJAX fetch 无此问题。tests/smoke_test.py 与 tests/verify_perf.py 无回归。
v1.8	2026-07-27	引擎②层级3（县级市/县）匹配改进 + 兜底解包 bug 修复：address_parser 省前缀剔除后再匹配市名（防"黑龙江省嫩江市"整体当市名）；DISTRICT_RE 加 (?<!社)区 防"社区"误匹配；engine2 中 city 若为 level=3 则直接作为区县并向上补 city/province；反查区县支持历史别名 _HISTORICAL_NAME_MAP（嫩江县→嫩江市、东宁县→东宁市、漠河县→漠河市、双城市→双城区、普兰店市→普兰店区）；兜底 _find_county_candidate 用 lazy 正则扫描 XX市/县/区/旗 匹配层级3列表。BUG：_find_county_candidate 调用处双值解包与函数单值返回不匹配，进兜底分支即 TypeError 崩溃（即"代码损坏"），改为返回 (row, name)/(None, None)。验证：用户 40 条地址 40/40 命中无 CRASH；tests.test_engine2/test_engine1（unittest 9 passed）+ tests/smoke_test 无回归。
v1.9	2026-07-27	决策器规则2 修复省/市/区县不一致时混用引擎①省市+引擎②区县的问题：当引擎①区县命中但和引擎②解析不一致时，原实现仅采用引擎②区县，却保留引擎①省/市代码，导致"内蒙古自治区呼伦贝尔市嫩江市"这类错误。改为当引擎②区县反查成功时，整体采用引擎②完整地址（省/市/区县全部一致），仅引擎②未反查到代码时才回退引擎①。验证：用户特殊案例（身份证 150722 内蒙古莫力达瓦旗，地址 黑龙江省嫩江市）输出正确为黑龙江/黑河/嫩江；tests.test_decider/test_engine1/test_engine2（unittest 15 passed）+ tests/smoke_test 无回归。
v2.0	2026-07-27	决策引擎重构：引擎②（地址解析）升级为绝对主引擎，行政层级3（区县/县级市）为判定锚点；引擎①（身份证）降级为校验器+兜底补充器，仅当地址为空或引擎②无法解析层级3时提供区县。状态枚举扩展为 正确/已解析/已解析(仅证件)/需复核/部分/无匹配/异常；证件与地址省/市冲突时输出引擎②结果并标「需复核」。引擎②强化：县级市省略市显式反查路径、乡镇/村辅助反推 _lookup_by_ancestor（父链核对省/市范围）、历史别名 _HISTORICAL_NAME_MAP 扩至15条、直辖市/省直管县 _apply_county 保留 city 名称、决策器省/市校验改用名称比对。下游同步：result.html 筛选器新增三状态+色彩、match_service 统计键扩展、models 枚举注释更新。验证：15 单测 + smoke_test + verify_v2（用户40条地址引擎② 40/40 命中、决策器5分支全对、县级市省略市5例全过）全绿。
v2.1	2026-07-27	自治区简称地址解析修复：地址中“宁夏”“内蒙古”等常省略“回族自治区/自治区”后缀，导致 parser 未识别省前缀，市/区县正则吃进省名（如“宁夏彭阳县”→ district=“宁夏彭阳县”无代码；“内蒙古呼伦贝尔市”→ city=“内蒙古呼伦贝尔市”）。修复：address_parser 入口将 内蒙古/广西/西藏/宁夏/新疆 简称展开为全称；乡镇/村抽取改在剔除区县名后的剩余文本中进行，避免长区县名被乡镇/村正则截断。新增 tests/test_address_parser.py（7 例）、verify_v2.py Part D；15 单测 + smoke_test + verify_v2 全绿。验证：朱家毅（640425/宁夏彭阳县）→ 正确；曲佳欣/马洪博（内蒙古莫力达瓦旗）→ 正确。
v2.2	2026-07-27	uploads/ 临时目录防膨胀：新增 utils/cleanup.py（prune_uploads/prune_old_inputs/prune_exports/clear_all_uploads）。在 app.py 后台导入线程（学生/区划）导入完成后调用 prune_uploads(keep=当前输入)，删除旧上传输入；导出路由 /export 生成后调用 prune_exports() 保留最新 MAX_EXPORT_KEEP(10) 个导出。config.py 新增 MAX_EXPORT_KEEP 与 PROTECTED_UPLOADS（全国行政编码数据.xlsx 受保护，永不自动删除）。安全前提：uploads 仅含导入输入/导出输出的瞬态副本，持久数据在 SQLite；启动时读 DATA_DIR 而非 uploads，故整目录可安全清除（源数据删除需人工确认重新上传）。新增 tests/test_cleanup.py（6 例，临时目录验证）。验证：28 单测 + smoke_test + verify_v2 全绿。

---

