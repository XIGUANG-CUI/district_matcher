"""数据库连接管理与基础查询接口。

性能优化（对应数据流转分析 P01 / P02 / P03）：
- P01 连接复用：进程内持有单一长连接，不再每次查询新建/关闭连接。
- P02 批量事务：新增 insert_results_batch，批量写入结果一次提交。
- P03 区划缓存：t_district_code 只读且量小，首次访问全量载入内存，
  匹配热路径（get_by_district_code / get_by_id / reverse_lookup）走内存，
  消除每条学生重复查库的开销。
"""
import os
import sqlite3
import threading

import pandas as pd

import config


class DBManager:
    def __init__(self, db_path=None):
        self.db_path = db_path or config.DB_PATH
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        # P01：长连接 + 关闭同线程检查（Flask 多线程 / 后台任务线程复用同一连接）
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.execute("PRAGMA synchronous = NORMAL")
        self._conn.execute("PRAGMA busy_timeout = 5000")
        self._lock = threading.Lock()
        # P03：区划字典内存缓存
        self._cache_loaded = False
        self._by_code = {}        # (district_code, admin_level) -> row
        self._by_id = {}          # id -> row
        self._by_name_level = {}  # (district_name, admin_level) -> [row, ...]
        # 历史代码映射缓存（t_history_mapping，小表：内置种子 + 用户增删）
        self._history_loaded = False
        self._history_seeded = False
        self._history_rows = []   # DB 记录（含 id）
        self._history_alias = {}  # 旧名 -> 新名
        self._history_code = {}   # 旧码 -> 新码
        self._init_db()

    # ------------------------- 连接 -------------------------
    def _init_db(self):
        with self._lock:
            self._conn.executescript(SCHEMA_SQL)
            # P10：兼容旧库——t_result 早期版本用 student_id 关联 t_student，
            # 现改为自包含 batch_id。旧库缺该列时 ALTER 补列（旧数据 batch_id
            # 为空，因“一进一出”通常为空，不影响查询）。索引依赖 batch_id 列，
            # 必须在补列之后创建，故从 SCHEMA_SQL 移至此。
            cols = [r[1] for r in self._conn.execute(
                "PRAGMA table_info(t_result)").fetchall()]
            if "batch_id" not in cols:
                self._conn.execute(
                    "ALTER TABLE t_result ADD COLUMN batch_id TEXT")
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_result_batch "
                "ON t_result(batch_id)")
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_result_status "
                "ON t_result(match_status)")
            self._conn.commit()

    def close(self):
        """关闭长连接（进程退出 / 测试结束时调用，避免 ResourceWarning）。"""
        try:
            self._conn.close()
        except Exception:
            pass

    def __del__(self):
        self.close()

    def reset_db(self):
        """清空全部业务数据（保留表结构）。"""
        with self._lock:
            self._conn.execute("DELETE FROM t_result")
            self._conn.execute("DELETE FROM t_student")
            self._conn.execute("DELETE FROM t_district_code")
            self._conn.execute("DELETE FROM t_history_mapping")
            self._conn.commit()
        self._invalidate_cache()
        # 重置后回到出厂状态：历史映射在下次访问时重新写入内置种子
        self._history_seeded = False
        self._invalidate_history()

    # ------------------------- 区划字典内存缓存（P03） -------------------------
    # 内存缓存仅常驻 level 1-4（省/市/区县/乡镇街道）。
    # level 5（村/社区，约 62 万行）为冷路径，按需走 SQL（方案 B：大幅降低内存占用）。
    _CACHE_MAX_LEVEL = 4

    def _ensure_cache(self):
        if self._cache_loaded:
            return
        with self._lock:
            if self._cache_loaded:
                return
            # 只加载引擎热路径所需的 level 1-4
            rows = self._conn.execute(
                "SELECT * FROM t_district_code WHERE admin_level <= ?",
                (self._CACHE_MAX_LEVEL,)).fetchall()
            self._by_code = {}
            self._by_id = {}
            self._by_name_level = {}
            for r in rows:
                d = dict(r)
                self._by_id[d["id"]] = d
                self._by_code[(d["district_code"], d["admin_level"])] = d
                self._by_name_level.setdefault(
                    (d["district_name"], d["admin_level"]), []).append(d)
            self._cache_loaded = True

    def _invalidate_cache(self):
        self._cache_loaded = False
        self._by_code = {}
        self._by_id = {}
        self._by_name_level = {}

    def _query_sql_districts(self, name=None, admin_level=None,
                             district_code=None, pid=None):
        """冷路径（level 5 / 不分层级名称搜索）走 SQL 兜底。"""
        sql = "SELECT * FROM t_district_code WHERE 1=1"
        params = []
        if name is not None:
            sql += " AND district_name = ?"
            params.append(name)
        if admin_level is not None:
            sql += " AND admin_level = ?"
            params.append(admin_level)
        if district_code is not None:
            sql += " AND district_code = ?"
            params.append(district_code)
        if pid is not None:
            sql += " AND pid = ?"
            params.append(pid)
        rows = self._conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    # ------------------------- 区划代码 -------------------------
    def insert_districts(self, rows):
        """批量插入区划数据。rows: list[dict]，键见 t_district_code。"""
        sql = (
            "INSERT OR REPLACE INTO t_district_code "
            "(id, pids, pid, district_code, district_name, admin_level, "
            " province_code, city_code) "
            "VALUES (:id, :pids, :pid, :district_code, :district_name, "
            ":admin_level, :province_code, :city_code)"
        )
        with self._lock:
            self._conn.executemany(sql, rows)
            self._conn.commit()
        self._invalidate_cache()

    def get_by_district_code(self, district_code, admin_level=None):
        self._ensure_cache()
        if admin_level is not None:
            if admin_level <= self._CACHE_MAX_LEVEL:
                # level 1-4 全量常驻内存，未命中即不存在（避免热路径 SQL 兜底）
                return self._by_code.get((district_code, admin_level))
            rows = self._query_sql_districts(district_code=district_code,
                                             admin_level=admin_level)
            return rows[0] if rows else None
        # 不带层级时保留原 fetchone 语义：按 1→2→3 顺序返回首个匹配
        for lvl in (1, 2, 3):
            row = self._by_code.get((district_code, lvl))
            if row:
                return row
        rows = self._query_sql_districts(district_code=district_code)
        return rows[0] if rows else None

    def get_by_id(self, row_id):
        self._ensure_cache()
        return self._by_id.get(row_id)

    def reverse_lookup(self, name, admin_level, pid=None):
        """按名称+层级反查；可选限定父级 pid。level 5 冷路径走 SQL。"""
        self._ensure_cache()
        if admin_level <= self._CACHE_MAX_LEVEL:
            candidates = self._by_name_level.get((name, admin_level), [])
            if pid is not None:
                for r in candidates:
                    if r["pid"] == pid:
                        return r
                return None
            return candidates[0] if candidates else None
        rows = self._query_sql_districts(name=name, admin_level=admin_level,
                                         pid=pid)
        return rows[0] if rows else None

    def search_by_name(self, name, admin_level=None):
        self._ensure_cache()
        if admin_level is not None and admin_level <= self._CACHE_MAX_LEVEL:
            return list(self._by_name_level.get((name, admin_level), []))
        # level 5 或不分层级：走 SQL（引擎当前仅以 level 4/5 触发该路径）
        return self._query_sql_districts(name=name, admin_level=admin_level)

    def count_districts(self):
        row = self._conn.execute(
            "SELECT COUNT(*) AS c FROM t_district_code").fetchone()
        return row["c"]

    def district_stats(self):
        """各层级记录数统计。"""
        rows = self._conn.execute(
            "SELECT admin_level, COUNT(*) AS c FROM t_district_code "
            "GROUP BY admin_level ORDER BY admin_level"
        ).fetchall()
        return {r["admin_level"]: r["c"] for r in rows}

    # ------------------------- 学生 -------------------------
    def insert_students(self, rows):
        sql = (
            "INSERT INTO t_student (batch_id, student_name, id_card, address, created_at) "
            "VALUES (:batch_id, :student_name, :id_card, :address, "
            "datetime('now','localtime'))"
        )
        with self._lock:
            self._conn.executemany(sql, rows)
            self._conn.commit()

    def list_batches(self):
        rows = self._conn.execute(
            "SELECT batch_id, COUNT(*) AS cnt, MAX(created_at) AS last "
            "FROM t_student GROUP BY batch_id ORDER BY last DESC"
        ).fetchall()
        return [dict(r) for r in rows]

    def get_students_by_batch(self, batch_id):
        rows = self._conn.execute(
            "SELECT * FROM t_student WHERE batch_id = ?", (batch_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------- 处理结果 -------------------------
    def delete_results_by_batch(self, batch_id):
        """删除指定学生批次的旧匹配结果，供重新处理时覆盖历史结果。"""
        with self._lock:
            self._conn.execute(
                "DELETE FROM t_result WHERE batch_id = ?", (batch_id,))
            self._conn.commit()

    def delete_batch(self, batch_id=None):
        """清理学生流水（t_student + t_result），保留区划字典 t_district_code。

        一进一出：导出成功后调用，避免学生数据长期占用数据库。
        batch_id 指定时仅清该批；为 None 时清空全部学生流水。
        """
        with self._lock:
            if batch_id:
                self._conn.execute(
                    "DELETE FROM t_result WHERE batch_id = ?", (batch_id,))
                self._conn.execute(
                    "DELETE FROM t_student WHERE batch_id = ?", (batch_id,))
            else:
                self._conn.execute("DELETE FROM t_result")
                self._conn.execute("DELETE FROM t_student")
            self._conn.commit()

    def insert_result(self, row):
        """单条写入（保留以兼容脚本/测试）。批量场景请使用 insert_results_batch。"""
        sql = (
            "INSERT INTO t_result "
            "(batch_id, student_name, id_card, address, "
            " province_code, province_name, city_code, city_name, "
            " district_code, district_name, match_status, "
            " engine1_result, engine2_result, remark, created_at) "
            "VALUES (:batch_id, :student_name, :id_card, :address, "
            " :province_code, :province_name, :city_code, :city_name, "
            " :district_code, :district_name, :match_status, "
            " :engine1_result, :engine2_result, :remark, "
            " datetime('now','localtime'))"
        )
        with self._lock:
            self._conn.execute(sql, row)
            self._conn.commit()

    def insert_results_batch(self, rows):
        """P02：批量写入匹配结果，单次事务提交。"""
        if not rows:
            return
        sql = (
            "INSERT INTO t_result "
            "(batch_id, student_name, id_card, address, "
             " province_code, province_name, city_code, city_name, "
             " district_code, district_name, match_status, "
             " engine1_result, engine2_result, remark, created_at) "
            "VALUES (:batch_id, :student_name, :id_card, :address, "
             " :province_code, :province_name, :city_code, :city_name, "
             " :district_code, :district_name, :match_status, "
             " :engine1_result, :engine2_result, :remark, "
             " datetime('now','localtime'))"
        )
        with self._lock:
            self._conn.executemany(sql, rows)
            self._conn.commit()

    def query_results(self, batch_id=None, status=None, page=1, page_size=50):
        # P10：t_result 已自包含 batch_id，直接按 r.batch_id 过滤，无需 JOIN t_student
        sql = "SELECT r.* FROM t_result r"
        params = []
        if batch_id:
            sql += " WHERE r.batch_id = ?"
            params.append(batch_id)
        if status:
            sql += (" AND" if batch_id else " WHERE") + " r.match_status = ?"
            params.append(status)
        sql += " ORDER BY r.id LIMIT ? OFFSET ?"
        params.extend([page_size, (page - 1) * page_size])
        rows = self._conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def result_stats(self, batch_id=None):
        if batch_id:
            sql = (
                "SELECT match_status, COUNT(*) AS c FROM t_result "
                "WHERE batch_id = ? GROUP BY match_status"
            )
            rows = self._conn.execute(sql, (batch_id,)).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT match_status, COUNT(*) AS c FROM t_result "
                "GROUP BY match_status"
            ).fetchall()
        return {r["match_status"]: r["c"] for r in rows}

    def count_results(self, batch_id=None, status=None):
        """统计结果总数，供分页使用。"""
        sql = "SELECT COUNT(*) AS c FROM t_result r"
        params = []
        if batch_id:
            sql += " WHERE r.batch_id = ?"
            params.append(batch_id)
        if status:
            sql += (" AND" if batch_id else " WHERE") + " r.match_status = ?"
            params.append(status)
        row = self._conn.execute(sql, params).fetchone()
        return row["c"]

    def get_results_dataframe(self, batch_id=None, status=None):
        """P08：直接以 SQL 取数生成 DataFrame（一步到位，避免 Row→dict→
        DataFrame 多次复制）。返回英文列名，调用方再用 COLUMNS 映射 rename。"""
        sql = (
            "SELECT r.student_name, r.id_card, r.address, "
            "r.province_name, r.province_code, r.city_name, r.city_code, "
            "r.district_name, r.district_code, r.match_status "
            "FROM t_result r"
        )
        params = []
        if batch_id:
            sql += " WHERE r.batch_id = ?"
            params.append(batch_id)
        if status:
            sql += (" AND" if batch_id else " WHERE") + " r.match_status = ?"
            params.append(status)
        sql += " ORDER BY r.id"
        return pd.read_sql_query(sql, self._conn, params=params)



    # ------------------------- 历史代码映射（t_history_mapping） -------------------------
    def _ensure_history(self):
        """确保历史映射已加载；首次建库（表从未写入）时自动写入内置种子。

        种子语义：内置默认仅在建库后第一次访问（或 reset_db 重置后）写入；
        之后表为唯一权威——用户删除/修改的记录即时生效，不会被内置兜底覆盖。
        """
        if self._history_loaded:
            return
        with self._lock:
            if self._history_loaded:
                return
            rows = self._conn.execute(
                "SELECT * FROM t_history_mapping").fetchall()
            if not rows and not self._history_seeded:
                self._seed_history_locked()
                rows = self._conn.execute(
                    "SELECT * FROM t_history_mapping").fetchall()
            self._history_rows = [dict(r) for r in rows]
            self._history_alias = {}
            self._history_code = {}
            for d in self._history_rows:
                if d.get("old_name") and d.get("new_name"):
                    self._history_alias[d["old_name"]] = d["new_name"]
                if d.get("old_code") and d.get("new_code"):
                    self._history_code[d["old_code"]] = d["new_code"]
            self._history_loaded = True

    def _seed_history_locked(self):
        """向 t_history_mapping 写入内置种子（须在持锁状态下调用）。"""
        from database import history_data
        sql = (
            "INSERT INTO t_history_mapping "
            "(old_code, new_code, old_name, new_name, change_type, change_date, remark) "
            "VALUES (:old_code, :new_code, :old_name, :new_name, "
            ":change_type, :change_date, :remark)"
        )
        self._conn.executemany(sql, history_data.HISTORY_SEED_ROWS)
        self._conn.commit()
        self._history_seeded = True

    def _invalidate_history(self):
        """清空历史映射缓存（保留 _history_seeded 标志，避免用户删除后自动回种）。"""
        self._history_loaded = False
        self._history_rows = []
        self._history_alias = {}
        self._history_code = {}

    def get_history_alias_map(self):
        """旧名称 -> 现行名称 映射 dict（引擎②地址别名翻译用）。只读，勿修改。"""
        self._ensure_history()
        return self._history_alias

    def get_history_code_map(self):
        """旧 6 位代码 -> 现行 6 位代码 映射 dict（引擎①旧码直查用）。只读，勿修改。"""
        self._ensure_history()
        return self._history_code

    def list_history_mappings(self):
        """历史映射记录全量列表（含 id，按 old_code 排序）。"""
        self._ensure_history()
        rows = sorted(self._history_rows, key=lambda r: r.get("old_code") or "")
        return [dict(r) for r in rows]

    def count_history_mappings(self):
        self._ensure_history()
        return len(self._history_rows)

    def insert_history_mapping(self, row):
        """新增/更新一条映射：以 old_code 为自然键，存在则替换（保留其余记录）。

        写入前先 _ensure_history()，保证首次操作即触发内置种子（而非绕过）。
        """
        self._ensure_history()
        row = self._normalize_history_row(row)
        with self._lock:
            if row.get("old_code"):
                self._conn.execute(
                    "DELETE FROM t_history_mapping WHERE old_code = ?",
                    (row["old_code"],))
            self._conn.execute(
                "INSERT INTO t_history_mapping "
                "(old_code, new_code, old_name, new_name, change_type, change_date, remark) "
                "VALUES (:old_code, :new_code, :old_name, :new_name, "
                ":change_type, :change_date, :remark)", row)
            self._conn.commit()
        self._invalidate_history()

    def insert_history_mappings(self, rows):
        """批量新增/更新：按 old_code 替换，单次事务提交。"""
        if not rows:
            return
        self._ensure_history()
        rows = [self._normalize_history_row(r) for r in rows]
        with self._lock:
            seen = set()
            for row in rows:
                oc = row.get("old_code")
                if oc and oc not in seen:
                    seen.add(oc)
                    self._conn.execute(
                        "DELETE FROM t_history_mapping WHERE old_code = ?", (oc,))
            self._conn.executemany(
                "INSERT INTO t_history_mapping "
                "(old_code, new_code, old_name, new_name, change_type, change_date, remark) "
                "VALUES (:old_code, :new_code, :old_name, :new_name, "
                ":change_type, :change_date, :remark)", rows)
            self._conn.commit()
        self._invalidate_history()

    @staticmethod
    def _normalize_history_row(row):
        """补齐历史映射行缺失的字段（默认为 None），避免 SQL 绑定缺失键报错。"""
        base = {"old_code": None, "new_code": None, "old_name": None,
                "new_name": None, "change_type": None, "change_date": None,
                "remark": None}
        base.update({k: v for k, v in (row or {}).items()})
        return base

    def delete_history_mapping(self, mapping_id):
        """按记录 id 删除一条历史映射。"""
        with self._lock:
            self._conn.execute(
                "DELETE FROM t_history_mapping WHERE id = ?", (mapping_id,))
            self._conn.commit()
        self._invalidate_history()

    def reset_history_mappings(self):
        """清空历史映射并恢复内置种子（管理页“恢复默认”按钮）。"""
        with self._lock:
            self._conn.execute("DELETE FROM t_history_mapping")
            self._seed_history_locked()
        self._invalidate_history()


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS t_district_code (
    id              TEXT PRIMARY KEY,
    pids            TEXT,
    pid             TEXT,
    district_code   TEXT NOT NULL,
    district_name   TEXT NOT NULL,
    admin_level     INTEGER NOT NULL,
    province_code   TEXT,
    city_code       TEXT,
    created_at      TEXT DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_district_code ON t_district_code(district_code);
CREATE INDEX IF NOT EXISTS idx_admin_level ON t_district_code(admin_level);
CREATE INDEX IF NOT EXISTS idx_pid ON t_district_code(pid);
CREATE INDEX IF NOT EXISTS idx_province_code ON t_district_code(province_code);
CREATE INDEX IF NOT EXISTS idx_city_code ON t_district_code(city_code);
CREATE INDEX IF NOT EXISTS idx_district_name ON t_district_code(district_name);
CREATE INDEX IF NOT EXISTS idx_name_level ON t_district_code(district_name, admin_level);

CREATE TABLE IF NOT EXISTS t_student (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id        TEXT,
    student_name    TEXT,
    id_card         TEXT NOT NULL,
    address         TEXT,
    created_at      TEXT DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_batch_id ON t_student(batch_id);

CREATE TABLE IF NOT EXISTS t_result (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id        TEXT,
    student_name    TEXT,
    id_card         TEXT,
    address         TEXT,
    province_code   TEXT,
    province_name   TEXT,
    city_code       TEXT,
    city_name       TEXT,
    district_code   TEXT,
    district_name   TEXT,
    match_status    TEXT,
    engine1_result  TEXT,
    engine2_result  TEXT,
    remark          TEXT,
    created_at      TEXT DEFAULT (datetime('now','localtime'))
);
-- t_result 索引在 _init_db 中 ALTER 补列后再创建（避免旧库缺 batch_id 列时建索引失败）

CREATE TABLE IF NOT EXISTS t_history_mapping (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    old_code        TEXT NOT NULL,
    new_code        TEXT NOT NULL,
    old_name        TEXT,
    new_name        TEXT,
    change_type     TEXT,
    change_date     TEXT,
    remark          TEXT
);
CREATE INDEX IF NOT EXISTS idx_old_code ON t_history_mapping(old_code);
"""
