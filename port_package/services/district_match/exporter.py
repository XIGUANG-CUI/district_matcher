"""匹配结果导出（xlsx / csv），含公式注入防护。"""
import csv

EXPORT_COLUMNS = [
    ("student_name", "学生姓名"),
    ("id_card", "身份证号"),
    ("address", "地址"),
    ("province_name", "省"),
    ("city_name", "市"),
    ("district_name", "区县"),
    ("match_status", "状态"),
    ("engine1_result", "引擎①过程"),
    ("engine2_result", "引擎②过程"),
    ("remark", "说明"),
]

_FORMULA_PREFIX = ("=", "+", "-", "@", "\t", "\r")


def _safe_cell(value):
    """公式注入防护：以 = + - @ 等开头的文本前置单引号。"""
    if value is None:
        return ""
    text = str(value)
    if text.startswith(_FORMULA_PREFIX):
        return "'" + text
    return text


def export_results(rows, out_path, fmt="xlsx"):
    """把匹配结果写成 xlsx / csv，返回 out_path。

    rows: dict 列表，键见 EXPORT_COLUMNS（缺键按空字符串输出）。
    """
    ext = (fmt or "").lower().lstrip(".")
    if ext in ("xlsx", "xlsm"):
        import openpyxl
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "区划匹配结果"
        ws.append([title for _key, title in EXPORT_COLUMNS])
        for row in rows:
            ws.append([_safe_cell(row.get(key)) for key, _title in EXPORT_COLUMNS])
        wb.save(out_path)
        return out_path

    if ext in ("csv", "tsv"):
        delimiter = "\t" if ext == "tsv" else ","
        with open(out_path, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f, delimiter=delimiter)
            writer.writerow([title for _key, title in EXPORT_COLUMNS])
            for row in rows:
                writer.writerow([_safe_cell(row.get(key)) for key, _title in EXPORT_COLUMNS])
        return out_path

    raise ValueError("仅支持导出 xlsx / csv")
