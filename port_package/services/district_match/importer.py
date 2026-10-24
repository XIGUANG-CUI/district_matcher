"""数据包导入与批量输入解析。

导入只接收调用方提供的连接（一次性连接由蓝图/脚本创建，见 0.2 #1b）：
- 不 import Flask / models，便于离线脚本复用；
- DDL 由调用方以 schema_sql 传入（DDL 统一维护在 models/db.py，遵守项目铁律 3）；
- 清空 + 重灌，中途失败回滚并清空半成品，避免“半套字典”；
- 导入期间不建临时表、不执行 VACUUM（105 MB 库约数秒）。
"""
import os

from . import pack as pack_mod

_INSERT_SQL = (
    "INSERT INTO dm_district_code "
    "(district_code, district_name, admin_level, parent_code, province_code, city_code) "
    "VALUES (?,?,?,?,?,?)"
)


def import_pack(conn, pack_path, schema_sql=(), batch_size=5000,
                on_progress=None, max_level=5):
    """把 district_pack.tsv.gz 导入 dm_district_code。

    返回 {"total": int, "by_level": {...}}；失败抛异常且库内不留半成品。
    前置：conn 为裸 sqlite3.Connection（建议 isolation_level=None，显式事务）。
    """
    if not os.path.exists(pack_path):
        raise FileNotFoundError(f"数据包不存在：{pack_path}")

    statements = (schema_sql.split(';') if isinstance(schema_sql, str)
                  else schema_sql)
    for stmt in statements:
        if stmt.strip():
            conn.execute(stmt)

    total = 0
    by_level = {}
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DELETE FROM dm_district_code")
        buf = []
        for row in pack_mod.iter_pack_rows(pack_path):
            if row[2] > max_level:
                continue
            buf.append(row)
            total += 1
            by_level[row[2]] = by_level.get(row[2], 0) + 1
            if len(buf) >= batch_size:
                conn.executemany(_INSERT_SQL, buf)
                buf.clear()
                if on_progress:
                    on_progress(total)
        if buf:
            conn.executemany(_INSERT_SQL, buf)
        conn.execute("COMMIT")
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        # 二次兜底：清掉可能残留的半成品（ROLLBACK 失效时）
        try:
            conn.execute("DELETE FROM dm_district_code")
            conn.commit()
        except Exception:
            pass
        raise

    if on_progress:
        on_progress(total)
    return {"total": total, "by_level": by_level}


# ---------------------------------------------------------------- 批量输入解析
def _norm_header(value):
    return str(value or "").strip().replace("　", "")


def _row_to_dict(header, raw):
    item = {}
    for i, key in enumerate(header):
        if not key:
            continue
        item[key] = "" if i >= len(raw) or raw[i] is None else str(raw[i]).strip()
    return item


def read_input_rows(path, limit=None):
    """读取上传的 xlsx/csv，返回 (headers, rows)。

    rows 为 dict 列表（列名 -> 文本值），用于“预览 + 列映射”弹窗。
    只用 openpyxl（项目已有依赖），不引入 pandas。
    """
    ext = os.path.splitext(path)[1].lower()
    if ext in (".xlsx", ".xlsm"):
        import openpyxl
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        try:
            ws = wb.active
            it = ws.iter_rows(values_only=True)
            try:
                header = [_norm_header(x) for x in next(it)]
            except StopIteration:
                return [], []
            rows = []
            for raw in it:
                if limit is not None and len(rows) >= limit:
                    break
                if raw is None or all(x is None or str(x).strip() == "" for x in raw):
                    continue
                rows.append(_row_to_dict(header, raw))
            return header, rows
        finally:
            wb.close()

    if ext == ".csv":
        import csv
        for enc in ("utf-8-sig", "gbk"):
            try:
                with open(path, "r", encoding=enc, newline="") as f:
                    reader = csv.reader(f)
                    try:
                        header = [_norm_header(x) for x in next(reader)]
                    except StopIteration:
                        return [], []
                    rows = []
                    for raw in reader:
                        if limit is not None and len(rows) >= limit:
                            break
                        if not raw or all(str(x).strip() == "" for x in raw):
                            continue
                        rows.append(_row_to_dict(header, raw))
                    return header, rows
            except UnicodeDecodeError:
                continue
        raise ValueError("CSV 编码无法识别（支持 UTF-8 / GBK）")

    raise ValueError("仅支持 .xlsx / .xlsm / .csv 文件")


def iter_input_rows(path, id_card_col, address_col):
    """流式读取全量输入行，产出 (row_no, id_card, address)。"""
    ext = os.path.splitext(path)[1].lower()

    def _pick(header, raw, col):
        if col not in header:
            return ""
        idx = header.index(col)
        if idx >= len(raw) or raw[idx] is None:
            return ""
        return str(raw[idx]).strip()

    if ext in (".xlsx", ".xlsm"):
        import openpyxl
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        try:
            ws = wb.active
            it = ws.iter_rows(values_only=True)
            try:
                header = [_norm_header(x) for x in next(it)]
            except StopIteration:
                return
            for n, raw in enumerate(it, start=2):
                if raw is None:
                    continue
                id_card = _pick(header, raw, id_card_col)
                address = _pick(header, raw, address_col)
                if not id_card and not address:
                    continue
                yield n, id_card, address
        finally:
            wb.close()
        return

    import csv
    for enc in ("utf-8-sig", "gbk"):
        try:
            with open(path, "r", encoding=enc, newline="") as f:
                reader = csv.reader(f)
                try:
                    header = [_norm_header(x) for x in next(reader)]
                except StopIteration:
                    return
                for n, raw in enumerate(reader, start=2):
                    id_card = _pick(header, raw, id_card_col)
                    address = _pick(header, raw, address_col)
                    if not id_card and not address:
                        continue
                    yield n, id_card, address
                return
        except UnicodeDecodeError:
            continue
    raise ValueError("CSV 编码无法识别（支持 UTF-8 / GBK）")
