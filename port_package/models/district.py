# -*- coding: utf-8 -*-
"""区划字典 Repository、进程级缓存、录入联动与批次结果存取。

设计约束（PORTING_GUIDE 0.2 / 3.3 / 4.6 / 7.1）：
1. 只通过 models.db.get_db() 返回的连接 ATTACH 字典库，每请求只 ATTACH 一次；
2. 运行期对 dm.* 只读；字典导入走一次性独立连接（唯一例外，见 0.2 #1b）；
3. L1–L4 字典与历史映射缓存放进程级模块变量 + threading.Lock，绝不放 g；
4. fill_district_fields 是纯内存补键函数：不写库、不 commit、不 UPDATE students，
   事务边界留在 create_student / create_student_with_conn / update_student；
5. 任何异常都不向调用方抛出（失败留空，状态置空），只记 warning 日志。
"""
import logging
import os
import sqlite3
import threading
import time

from flask import g, has_app_context

import config
from models.db import get_db, _non_graduated_where
from services.district_match import constants, runner
from services.district_match import pack as pack_mod

logger = logging.getLogger(__name__)

# 字典库与数据包路径（0.2 #3：由 DATABASE_PATH 推导，禁用 config._USER_DATA_DIR）
DISTRICT_DB_PATH = os.path.join(os.path.dirname(config.DATABASE_PATH),
                                constants.DISTRICT_DB_FILENAME)
_PACK_CANDIDATES = (
    os.path.join(getattr(config, "BASE_DIR", os.path.dirname(__file__)),
                 "data", constants.DISTRICT_PACK_FILENAME),
    os.path.join(os.path.dirname(config.DATABASE_PATH),
                 constants.DISTRICT_PACK_FILENAME),
)

DISTRICT_FIELDS = constants.DISTRICT_FIELDS


class DistrictDbMissing(Exception):
    """字典库缺失或未导入数据包。"""


# ------------------------------------------------------------------ ATTACH
def ensure_attached(conn=None):
    """在当前请求连接上 ATTACH 字典库（每请求一次）。

    init_db() 等无 app context 的场景直接返回传入连接，由调用方自行 ATTACH。
    """
    conn = conn or get_db()
    if not os.path.exists(DISTRICT_DB_PATH):
        raise DistrictDbMissing(f"字典库不存在：{DISTRICT_DB_PATH}")
    if has_app_context() and getattr(g, "_dm_attached_conn", None) is conn:
        return conn
    try:
        conn.execute("ATTACH DATABASE ? AS dm", (DISTRICT_DB_PATH,))
    except sqlite3.OperationalError as exc:
        msg = str(exc)
        if "already in use" not in msg and "already attached" not in msg:
            raise
    if has_app_context():
        g._dm_attached_conn = conn
    return conn


# ------------------------------------------------------------------ 缓存
_CACHE_LOCK = threading.Lock()
_CACHE = {
    "ready": False,
    "by_code": {},     # district_code -> row
    "by_name": {},     # (district_name, admin_level) -> [row, ...]
    "level_counts": {},
    "loaded_at": 0.0,
}

_HISTORY_LOCK = threading.Lock()
_HISTORY = {
    "ready": False,
    "alias": {},   # old_name -> new_name
    "code": {},    # old_code -> new_code
    "rows": [],
}

_DICT_STATE = {"ready": None, "at": 0.0, "total": 0}
_DICT_LOCK = threading.Lock()
_DICT_TTL = 5.0


def _adapt(row):
    """sqlite3.Row / tuple / dict -> 统一 dict。"""
    if row is None:
        return None
    if isinstance(row, dict):
        return dict(row)
    try:
        return {k: row[k] for k in row.keys()}
    except AttributeError:
        # 位置元组（仅用于字典 6 列查询）
        return {
            "district_code": row[0], "district_name": row[1],
            "admin_level": row[2], "parent_code": row[3],
            "province_code": row[4], "city_code": row[5],
        }


def _adapt_district(row):
    """字典行适配：补 id/pid 别名（兼容原引擎对 id / pid 的依赖）。"""
    item = _adapt(row)
    if item and "district_code" in item:
        item["id"] = item["district_code"]
        item["pid"] = item.get("parent_code")
    return item


def _normalize_code(code):
    """统一 12 位文本代码：只保留数字并右补 0，保留前导零。"""
    if code is None:
        return ""
    text = str(code).strip()
    if not text:
        return ""
    if not text.isdigit():
        return ""
    return (text + "0" * 12)[:12]


def _dict_ready(conn, refresh=False):
    """字典表是否可用（5 秒内复用结果，避免逐行探测）。"""
    now = time.time()
    with _DICT_LOCK:
        if not refresh and _DICT_STATE["ready"] is not None \
                and now - _DICT_STATE["at"] < _DICT_TTL:
            return _DICT_STATE["ready"]
    ok = False
    total = 0
    try:
        ensure_attached(conn)
        row = conn.execute(
            "SELECT name FROM dm.sqlite_master WHERE type='table' "
            "AND name='dm_district_code'").fetchone()
        if row:
            cnt = conn.execute("SELECT COUNT(1) FROM dm.dm_district_code").fetchone()
            total = int(cnt[0]) if cnt else 0
            ok = total > 0
    except Exception as exc:  # 字典缺失不阻断业务
        logger.debug("区划字典不可用：%s", exc)
        ok = False
    with _DICT_LOCK:
        _DICT_STATE.update({"ready": ok, "at": time.time(), "total": total})
    return ok


def _load_cache(conn):
    """加载 L1–L4 常驻缓存（进程级，只加载一次）。"""
    if _CACHE["ready"]:
        return _CACHE
    with _CACHE_LOCK:
        if _CACHE["ready"]:
            return _CACHE
        started = time.time()
        ensure_attached(conn)
        rows = conn.execute(
            "SELECT district_code, district_name, admin_level, parent_code, "
            "province_code, city_code FROM dm.dm_district_code "
            "WHERE admin_level <= ?", (constants.CACHE_MAX_LEVEL,)).fetchall()
        by_code = {}
        by_name = {}
        counts = {}
        for raw in rows:
            item = _adapt_district(raw)
            by_code[item["district_code"]] = item
            by_name.setdefault((item["district_name"], item["admin_level"]), []).append(item)
            counts[item["admin_level"]] = counts.get(item["admin_level"], 0) + 1
        _CACHE.update({
            "ready": True,
            "by_code": by_code,
            "by_name": by_name,
            "level_counts": counts,
            "loaded_at": time.time(),
        })
        logger.info("区划字典缓存加载完成：%s 行，耗时 %.2fs",
                    len(by_code), time.time() - started)
        return _CACHE


def _load_history(conn):
    """加载历史映射缓存（dm_history_mapping 在 student.db）。"""
    if _HISTORY["ready"]:
        return _HISTORY
    with _HISTORY_LOCK:
        if _HISTORY["ready"]:
            return _HISTORY
        alias, code_map, rows = {}, {}, []
        try:
            cur = conn.execute(
                "SELECT old_code, new_code, old_name, new_name, change_type, "
                "change_date, remark FROM dm_history_mapping "
                "WHERE is_deleted = 0 ORDER BY id")
            for raw in cur.fetchall():
                # 直接按下标取，避免依赖 Row 的键名
                vals = list(raw)
                old_code, new_code = vals[0], vals[1]
                old_name, new_name = vals[2], vals[3]
                rows.append({
                    "old_code": old_code, "new_code": new_code,
                    "old_name": old_name, "new_name": new_name,
                    "change_type": vals[4], "change_date": vals[5],
                    "remark": vals[6],
                })
                if old_name and new_name:
                    alias[old_name] = new_name
                if old_code and new_code:
                    code_map[str(old_code)] = str(new_code)
        except sqlite3.OperationalError as exc:
            logger.warning("历史映射表不可用（按空处理）：%s", exc)
        _HISTORY.update({"ready": True, "alias": alias, "code": code_map, "rows": rows})
        return _HISTORY


def invalidate_cache():
    """字典重导/历史变更后清空缓存（进程级）。"""
    with _CACHE_LOCK:
        _CACHE.update({"ready": False, "by_code": {}, "by_name": {},
                       "level_counts": {}, "loaded_at": 0.0})
    with _HISTORY_LOCK:
        _HISTORY.update({"ready": False, "alias": {}, "code": {}, "rows": []})
    with _DICT_LOCK:
        _DICT_STATE.update({"ready": None, "at": 0.0, "total": 0})


def warm_cache(conn=None):
    """显式预热（批量导入前调用），返回缓存信息；字典缺失时返回 ready=False。"""
    conn = conn or get_db()
    try:
        if not _dict_ready(conn, refresh=True):
            return {"ready": False}
        _load_cache(conn)
        _load_history(conn)
        return cache_info()
    except Exception as exc:
        logger.warning("区划缓存预热失败：%s", exc)
        return {"ready": False, "error": str(exc)}


def cache_info():
    """缓存状态（不含大对象）。"""
    return {
        "ready": bool(_CACHE["ready"]),
        "rows": len(_CACHE["by_code"]),
        "level_counts": dict(_CACHE["level_counts"]),
        "loaded_at": _CACHE["loaded_at"],
        "history_ready": bool(_HISTORY["ready"]),
        "history_rows": len(_HISTORY["rows"]),
    }


# ------------------------------------------------------------------ Repository
class DistrictRepository:
    """字典 + 批次/结果的数据访问对象。

    引擎只依赖鸭子类型方法：get_by_district_code / get_by_id / reverse_lookup /
    search_by_name / get_history_alias_map / get_history_code_map。
    """

    def __init__(self, conn=None):
        self._conn = conn
        self._attached = False

    @property
    def conn(self):
        if self._conn is None:
            self._conn = get_db()
        return self._conn

    def _ensure(self):
        if not self._attached:
            ensure_attached(self.conn)
            self._attached = True

    # ---------------- 字典只读
    def get_by_district_code(self, code, level=None):
        code = _normalize_code(code)
        if not code:
            return None
        cache = _CACHE
        if cache["ready"] and (level is None or level <= constants.CACHE_MAX_LEVEL):
            hit = cache["by_code"].get(code)
            if hit and (level is None or hit["admin_level"] == level):
                return hit
        try:
            self._ensure()
            if level is None:
                row = self.conn.execute(
                    "SELECT district_code, district_name, admin_level, parent_code, "
                    "province_code, city_code FROM dm.dm_district_code "
                    "WHERE district_code = ? ORDER BY admin_level LIMIT 1", (code,)).fetchone()
            else:
                row = self.conn.execute(
                    "SELECT district_code, district_name, admin_level, parent_code, "
                    "province_code, city_code FROM dm.dm_district_code "
                    "WHERE district_code = ? AND admin_level = ?", (code, level)).fetchone()
            return _adapt_district(row)
        except Exception as exc:
            logger.debug("区划按代码查询失败：%s", exc)
            return None

    def get_by_id(self, rid):
        """引擎内的 pid 是 12 位代码，等价于按代码查询。"""
        if rid is None:
            return None
        return self.get_by_district_code(str(rid))

    def reverse_lookup(self, name, level, pid=None):
        """按名称反查（level<=4 走缓存，level=5 走 SQL）。"""
        if not name:
            return None
        if level <= constants.CACHE_MAX_LEVEL:
            _load_cache(self.conn)
            items = _CACHE["by_name"].get((name, level)) or []
            if pid:
                for item in items:
                    if item.get("parent_code") == str(pid) or item.get("pid") == str(pid):
                        return item
                return None
            return items[0] if items else None
        try:
            self._ensure()
            if pid:
                row = self.conn.execute(
                    "SELECT district_code, district_name, admin_level, parent_code, "
                    "province_code, city_code FROM dm.dm_district_code "
                    "WHERE district_name = ? AND admin_level = ? AND parent_code = ? LIMIT 1",
                    (name, level, str(pid))).fetchone()
                if row:
                    return _adapt_district(row)
            row = self.conn.execute(
                "SELECT district_code, district_name, admin_level, parent_code, "
                "province_code, city_code FROM dm.dm_district_code "
                "WHERE district_name = ? AND admin_level = ? LIMIT 1",
                (name, level)).fetchone()
            return _adapt_district(row)
        except Exception as exc:
            logger.debug("区划按名称反查失败：%s", exc)
            return None

    def search_by_name(self, name, level=None):
        """按名称搜索（可能多条；level=None 时全层级）。"""
        if not name:
            return []
        if level is not None and level <= constants.CACHE_MAX_LEVEL:
            _load_cache(self.conn)
            return list(_CACHE["by_name"].get((name, level)) or [])
        try:
            self._ensure()
            if level is None:
                cur = self.conn.execute(
                    "SELECT district_code, district_name, admin_level, parent_code, "
                    "province_code, city_code FROM dm.dm_district_code "
                    "WHERE district_name = ? ORDER BY admin_level, district_code", (name,))
            else:
                cur = self.conn.execute(
                    "SELECT district_code, district_name, admin_level, parent_code, "
                    "province_code, city_code FROM dm.dm_district_code "
                    "WHERE district_name = ? AND admin_level = ? ORDER BY district_code",
                    (name, level))
            return [_adapt_district(r) for r in cur.fetchall()]
        except Exception as exc:
            logger.debug("区划名称搜索失败：%s", exc)
            return []

    def query_levels_le(self, level=constants.CACHE_MAX_LEVEL):
        """取 <= level 的全部字典行（缓存优先）。"""
        if level <= constants.CACHE_MAX_LEVEL:
            _load_cache(self.conn)
            return list(_CACHE["by_code"].values())
        try:
            self._ensure()
            cur = self.conn.execute(
                "SELECT district_code, district_name, admin_level, parent_code, "
                "province_code, city_code FROM dm.dm_district_code "
                "WHERE admin_level <= ? ORDER BY admin_level, district_code", (level,))
            return [_adapt_district(r) for r in cur.fetchall()]
        except Exception:
            return []

    def get_history_alias_map(self):
        return dict(_load_history(self.conn)["alias"])

    def get_history_code_map(self):
        return dict(_load_history(self.conn)["code"])

    def list_history(self):
        _load_history(self.conn)
        return list(_HISTORY["rows"])

    def district_stats(self):
        """字典统计：总行数、各级行数、更新时间、文件大小。"""
        stats = {
            "db_path": DISTRICT_DB_PATH,
            "db_exists": os.path.exists(DISTRICT_DB_PATH),
            "db_size": 0,
            "total": 0,
            "by_level": {},
            "updated_at": "",
            "cache": cache_info(),
        }
        if not stats["db_exists"]:
            return stats
        stats["db_size"] = os.path.getsize(DISTRICT_DB_PATH)
        stats["updated_at"] = time.strftime(
            "%Y-%m-%d %H:%M:%S", time.localtime(os.path.getmtime(DISTRICT_DB_PATH)))
        try:
            if not _dict_ready(self.conn, refresh=True):
                return stats
            cur = self.conn.execute(
                "SELECT admin_level, COUNT(1) FROM dm.dm_district_code "
                "GROUP BY admin_level ORDER BY admin_level")
            by_level = {}
            total = 0
            for level, cnt in cur.fetchall():
                by_level[int(level)] = int(cnt)
                total += int(cnt)
            stats["by_level"] = by_level
            stats["total"] = total
        except Exception as exc:
            logger.debug("字典统计失败：%s", exc)
        return stats

    # ---------------- 批次 / 结果（dm_* 在 student.db，可写）
    def create_batch(self, batch_no, source, total, created_by):
        self.conn.execute(
            "INSERT INTO dm_match_batch (batch_no, source, total, status, created_by) "
            "VALUES (?,?,?, 'running', ?)", (batch_no, source, total, created_by))
        self.conn.commit()
        row = self.conn.execute(
            "SELECT id FROM dm_match_batch WHERE batch_no = ?", (batch_no,)).fetchone()
        return int(row[0]) if row else None

    def update_batch(self, batch_id, status=None, total=None, stats_json=None):
        fields, args = [], []
        if status is not None:
            fields.append("status = ?")
            args.append(status)
        if total is not None:
            fields.append("total = ?")
            args.append(int(total))
        if stats_json is not None:
            fields.append("stats_json = ?")
            args.append(stats_json)
        if not fields:
            return
        args.append(batch_id)
        self.conn.execute(
            f"UPDATE dm_match_batch SET {', '.join(fields)} WHERE id = ?", args)
        self.conn.commit()

    def get_batch(self, batch_id=None, batch_no=None):
        if batch_id:
            row = self.conn.execute(
                "SELECT * FROM dm_match_batch WHERE id = ? AND is_deleted = 0",
                (batch_id,)).fetchone()
        else:
            row = self.conn.execute(
                "SELECT * FROM dm_match_batch WHERE batch_no = ? AND is_deleted = 0",
                (batch_no,)).fetchone()
        return _adapt(row)

    def list_batches(self, limit=50, offset=0):
        cur = self.conn.execute(
            "SELECT * FROM dm_match_batch WHERE is_deleted = 0 "
            "ORDER BY id DESC LIMIT ? OFFSET ?", (limit, offset))
        return [_adapt(r) for r in cur.fetchall()]

    def count_batches(self):
        row = self.conn.execute(
            "SELECT COUNT(1) FROM dm_match_batch WHERE is_deleted = 0").fetchone()
        return int(row[0]) if row else 0

    def soft_delete_batch(self, batch_id):
        self.conn.execute(
            "UPDATE dm_match_batch SET is_deleted = 1 WHERE id = ?", (batch_id,))
        self.conn.execute(
            "UPDATE dm_match_result SET is_deleted = 1 WHERE batch_id = ?", (batch_id,))
        self.conn.commit()

    def delete_results_by_batch(self, batch_id):
        self.conn.execute(
            "DELETE FROM dm_match_result WHERE batch_id = ?", (batch_id,))
        self.conn.commit()

    def insert_results_batch(self, rows, chunk=500):
        if not rows:
            return 0
        sql = ("INSERT INTO dm_match_result "
               "(batch_id, student_id, student_name, id_card, address, "
               " province_code, province_name, city_code, city_name, "
               " district_code, district_name, match_status, engine1_detail, "
               " engine2_detail, decision_path, source) "
               "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)")
        values = [
            (r.get("batch_id"), r.get("student_id"), r.get("student_name"),
             r.get("id_card"), r.get("address"),
             r.get("province_code"), r.get("province_name"),
             r.get("city_code"), r.get("city_name"),
             r.get("district_code"), r.get("district_name"),
             r.get("match_status"), r.get("engine1_result") or r.get("engine1_detail"),
             r.get("engine2_result") or r.get("engine2_detail"),
             r.get("remark") or r.get("decision_path"),
             r.get("source") or "batch")
            for r in rows
        ]
        for i in range(0, len(values), chunk):
            self.conn.executemany(sql, values[i:i + chunk])
        self.conn.commit()
        return len(values)

    def list_results(self, batch_id, status=None, limit=200, offset=0):
        sql = ("SELECT * FROM dm_match_result WHERE batch_id = ? AND is_deleted = 0")
        args = [batch_id]
        if status:
            sql += " AND match_status = ?"
            args.append(status)
        sql += " ORDER BY id LIMIT ? OFFSET ?"
        args.extend([limit, offset])
        cur = self.conn.execute(sql, args)
        return [_adapt(r) for r in cur.fetchall()]

    def count_results(self, batch_id, status=None):
        sql = "SELECT COUNT(1) FROM dm_match_result WHERE batch_id = ? AND is_deleted = 0"
        args = [batch_id]
        if status:
            sql += " AND match_status = ?"
            args.append(status)
        row = self.conn.execute(sql, args).fetchone()
        return int(row[0]) if row else 0

    def result_stats(self, batch_id):
        cur = self.conn.execute(
            "SELECT match_status, COUNT(1) FROM dm_match_result "
            "WHERE batch_id = ? AND is_deleted = 0 GROUP BY match_status", (batch_id,))
        data = {status: int(cnt) for status, cnt in cur.fetchall()}
        data["总计"] = sum(data.values())
        return data

    # ---------------- 学生口径查询（只读 students）
    def get_student(self, student_id):
        row = self.conn.execute(
            "SELECT id, name, id_card, address, district_province_name, "
            "district_province_code, district_city_name, district_city_code, "
            "district_county_name, district_county_code, district_match_status "
            "FROM students WHERE id = ?", (student_id,)).fetchone()
        return _adapt(row)

    def latest_result_for_student(self, student_id=None, id_card=None):
        if student_id:
            row = self.conn.execute(
                "SELECT * FROM dm_match_result WHERE student_id = ? AND is_deleted = 0 "
                "ORDER BY id DESC LIMIT 1", (student_id,)).fetchone()
            if row:
                return _adapt(row)
        if id_card:
            row = self.conn.execute(
                "SELECT * FROM dm_match_result WHERE id_card = ? AND is_deleted = 0 "
                "ORDER BY id DESC LIMIT 1", (id_card,)).fetchone()
            return _adapt(row)
        return None

    def insert_single_result(self, row):
        return self.insert_results_batch([row])

    def non_graduated_students(self, student_ids=None):
        sql = ("SELECT id AS student_id, name AS student_name, id_card, address "
               "FROM students WHERE " + _non_graduated_where())
        args = []
        if student_ids:
            placeholders = ",".join("?" for _ in student_ids)
            sql += f" AND id IN ({placeholders})"
            args.extend(student_ids)
        sql += " ORDER BY id"
        cur = self.conn.execute(sql, args)
        return [_adapt(r) for r in cur.fetchall()]


# ------------------------------------------------------------------ 录入联动
def _blank(data):
    for key in DISTRICT_FIELDS:
        data.setdefault(key, "")
    return data


def fill_district_fields(data, conn=None):
    """就地补 students 的 7 个区划键，返回原 data。

    - 纯内存：不执行写 SQL、不 commit、不 UPDATE students；
    - 字典缺失/任何异常：7 键留空，绝不抛异常；
    - 事务边界由 create_student / create_student_with_conn / update_student 控制。
    """
    if data is None:
        return data
    try:
        _blank(data)
        if not data.get("id_card") and not data.get("address"):
            return data
        repo = DistrictRepository(conn)
        if not _dict_ready(repo.conn):
            return data
        _load_cache(repo.conn)
        _load_history(repo.conn)
        result = runner.run_match(repo, data.get("id_card"), data.get("address"))
        data.update(runner.to_district_fields(result))
    except Exception as exc:
        logger.warning("区划自动补全失败（已留空）：%s", exc)
        _blank(data)
    return data


def fill_district_fields_many(rows, conn=None):
    """批量补 7 键（>500 行导入用）：一次 ATTACH、一次预热、循环内存匹配。

    返回 rows 本身（就地修改），便于调用方继续插入。
    """
    rows = list(rows or [])
    if not rows:
        return rows
    try:
        repo = DistrictRepository(conn)
        if not _dict_ready(repo.conn):
            for data in rows:
                _blank(data)
            return rows
        _load_cache(repo.conn)
        _load_history(repo.conn)
        for data in rows:
            if data is None:
                continue
            _blank(data)
            if not data.get("id_card") and not data.get("address"):
                continue
            try:
                result = runner.run_match(repo, data.get("id_card"), data.get("address"))
                data.update(runner.to_district_fields(result))
            except Exception as exc:
                logger.warning("区划批量补全单行失败（已留空）：%s", exc)
                _blank(data)
    except Exception as exc:
        logger.warning("区划批量补全失败（已留空）：%s", exc)
        for data in rows:
            if data is not None:
                _blank(data)
    return rows


# ------------------------------------------------------------------ 4.9 三接口
def get_student_district(student_id=None, id_card=None):
    """先取 students 7 列；为空再取最新有效匹配结果；仍无返回 None。"""
    repo = DistrictRepository()
    student = None
    if student_id:
        student = repo.get_student(student_id)
    elif id_card:
        row = repo.conn.execute(
            "SELECT id, name, id_card, address, district_province_name, "
            "district_province_code, district_city_name, district_city_code, "
            "district_county_name, district_county_code, district_match_status "
            "FROM students WHERE id_card = ? AND is_deleted = 0 "
            "ORDER BY id DESC LIMIT 1", (id_card,)).fetchone()
        student = _adapt(row)

    if student and student.get("district_county_code"):
        return {
            "district_province_name": student.get("district_province_name") or "",
            "district_province_code": student.get("district_province_code") or "",
            "district_city_name": student.get("district_city_name") or "",
            "district_city_code": student.get("district_city_code") or "",
            "district_county_name": student.get("district_county_name") or "",
            "district_county_code": student.get("district_county_code") or "",
            "match_status": student.get("district_match_status") or "",
            "batch_id": None,
            "created_at": None,
            "source": "students",
        }

    latest = repo.latest_result_for_student(
        student_id=student_id,
        id_card=(student or {}).get("id_card") or id_card)
    if not latest:
        return None
    return {
        "district_province_name": latest.get("province_name") or "",
        "district_province_code": latest.get("province_code") or "",
        "district_city_name": latest.get("city_name") or "",
        "district_city_code": latest.get("city_code") or "",
        "district_county_name": latest.get("district_name") or "",
        "district_county_code": latest.get("district_code") or "",
        "match_status": latest.get("match_status") or "",
        "batch_id": latest.get("batch_id"),
        "created_at": latest.get("created_at"),
        "source": "dm_match_result",
    }


def ensure_student_district(student_id, refresh=False):
    """按需匹配：只写 dm_match_result（source='on_demand'），绝不回写 students。"""
    repo = DistrictRepository()
    if not refresh:
        existed = get_student_district(student_id=student_id)
        if existed:
            return existed
    student = repo.get_student(student_id)
    if not student:
        return None
    result = runner.run_match(repo, student.get("id_card"), student.get("address"),
                              with_detail=True)
    row = runner.to_result_row({
        "student_id": student_id,
        "student_name": student.get("name"),
        "id_card": student.get("id_card"),
        "address": student.get("address"),
    }, result)
    row["batch_id"] = None
    row["source"] = "on_demand"
    repo.insert_single_result(row)
    return {
        "district_province_name": result.province_name or "",
        "district_province_code": result.province_code or "",
        "district_city_name": result.city_name or "",
        "district_city_code": result.city_code or "",
        "district_county_name": result.district_name or "",
        "district_county_code": result.district_code or "",
        "match_status": result.match_status or "",
        "batch_id": None,
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "source": "on_demand",
    }


def match_students(student_ids=None, created_by="", on_progress=None):
    """学生口径批量匹配：只写 dm_match_result，不写 students。

    学生范围复用 models.db._non_graduated_where()（排除毕业与软删除）。
    """
    repo = DistrictRepository()
    students = repo.non_graduated_students(student_ids)
    batch_no = "B" + os.urandom(4).hex()
    batch_id = repo.create_batch(batch_no, "students", len(students), created_by)
    try:
        stats = runner.process_batch(repo, students, batch_id, on_progress=on_progress,
                                     source="students")
        repo.update_batch(batch_id, status="done", total=len(students),
                          stats_json=_json(stats))
    except Exception:
        repo.update_batch(batch_id, status="failed")
        raise
    return {"batch_id": batch_id, "batch_no": batch_no, "stats": stats}


def _json(obj):
    import json
    return json.dumps(obj, ensure_ascii=False)


# ------------------------------------------------------------------ 写 students（唯一批量入口）
def backfill_students(only_empty=True, limit=None, on_progress=None):
    """批量补全区划：只 UPDATE 7 列，不动 updated_at，不写 other 列。

    only_empty=True 时只处理区县代码为空的学生。
    """
    conn = get_db()
    sql = ("SELECT id, id_card, address FROM students "
           "WHERE is_deleted = 0")
    if only_empty:
        sql += " AND (district_county_code IS NULL OR district_county_code = '')"
    sql += " ORDER BY id"
    if limit:
        sql += f" LIMIT {int(limit)}"
    rows = [_adapt(r) for r in conn.execute(sql).fetchall()]
    total = len(rows)
    updated = 0
    failed = 0
    chunk = []
    for i, row in enumerate(rows, start=1):
        try:
            fields = fill_district_fields({"id_card": row.get("id_card"),
                                           "address": row.get("address")}, conn)
            chunk.append((
                fields.get("district_province_name", ""),
                fields.get("district_province_code", ""),
                fields.get("district_city_name", ""),
                fields.get("district_city_code", ""),
                fields.get("district_county_name", ""),
                fields.get("district_county_code", ""),
                fields.get("district_match_status", ""),
                row["id"],
            ))
        except Exception as exc:
            failed += 1
            logger.warning("学生 %s 区划补全失败：%s", row.get("id"), exc)
        if len(chunk) >= 200:
            conn.executemany(
                "UPDATE students SET district_province_name = ?, "
                "district_province_code = ?, district_city_name = ?, "
                "district_city_code = ?, district_county_name = ?, "
                "district_county_code = ?, district_match_status = ? "
                "WHERE id = ?", chunk)
            conn.commit()
            updated += len(chunk)
            chunk.clear()
        if on_progress and i % 200 == 0:
            on_progress(i, total)
    if chunk:
        conn.executemany(
            "UPDATE students SET district_province_name = ?, "
            "district_province_code = ?, district_city_name = ?, "
            "district_city_code = ?, district_county_name = ?, "
            "district_county_code = ?, district_match_status = ? "
            "WHERE id = ?", chunk)
        conn.commit()
        updated += len(chunk)
    if on_progress:
        on_progress(total, total)
    return {"total": total, "updated": updated, "failed": failed}


# ------------------------------------------------------------------ 字典导入 / 状态
def pack_path():
    for path in _PACK_CANDIDATES:
        if os.path.exists(path):
            return path
    return _PACK_CANDIDATES[0]


def dictionary_status():
    """字典状态 + 数据包校验（不含 sha256 计算，由接口按需触发）。"""
    stats = DistrictRepository().district_stats()
    path = pack_path()
    stats["pack"] = {
        "path": path,
        "exists": os.path.exists(path),
        "size": os.path.getsize(path) if os.path.exists(path) else 0,
    }
    return stats


def import_dictionary(pack_file=None, on_progress=None):
    """导入字典数据包。

    使用一次性独立连接（0.2 #1b 唯一例外），避免长连接/连接池；
    导入完成后清缓存，下一次请求重新 ATTACH 新库。
    """
    from models.db import DISTRICT_DDL

    path = pack_file or pack_path()
    if not os.path.exists(path):
        raise FileNotFoundError(f"数据包不存在：{path}")

    os.makedirs(os.path.dirname(DISTRICT_DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DISTRICT_DB_PATH, timeout=30)
    try:
        conn.isolation_level = None
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        from services.district_match import importer
        result = importer.import_pack(conn, path, schema_sql=DISTRICT_DDL,
                                      on_progress=on_progress)
    finally:
        conn.close()
    invalidate_cache()
    return result
