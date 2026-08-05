"""导入服务：Excel 行政区划数据导入、学生数据导入。

导入逻辑对常见表头做自动识别，并支持通过 column_mapping 手动指定列，
对 6 位代码自动补零为 12 位、缺失的省市代码由 12 位代码推导，
缺失 id/pid 时回退到 12 位代码本身，保证层级自洽。
"""
import os
import uuid

import pandas as pd

import config


def _auto_map(columns, keyword_map):
    """根据关键词自动匹配列名。keyword_map: {field: [keywords]}。
    同一列只会被分配一次，避免“行政区划代码”同时命中 code 与 level。"""
    Lower = [str(c).strip().lower() for c in columns]
    used = set()
    mapping = {}
    for field, kws in keyword_map.items():
        for i, c in enumerate(Lower):
            if i in used:
                continue
            if any(kw in c for kw in kws):
                mapping[field] = columns[i]
                used.add(i)
                break
    return mapping


def _norm_code(val):
    """规范化区划代码为 12 位字符串；6 位补零，其余原样返回。"""
    if val is None:
        return None
    s = str(val).strip().replace(" ", "").replace("\t", "")
    if s.isdigit():
        if len(s) == 6:
            return s + config.DISTRICT_PAD
        if len(s) == 12:
            return s
        if len(s) == 2:
            return s + config.PROVINCE_PAD
        if len(s) == 4:
            return s + config.CITY_PAD
    return s or None


def _clean_cell(val):
    """将单元格值归一为字符串；空值 / NaN / 'nan' 返回 None。"""
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    s = str(val).strip()
    if not s or s.lower() == "nan":
        return None
    return s


def _norm_level(val):
    """行政层级规范化为整数 1-5。"""
    if val is None:
        return None
    s = str(val).strip()
    text_map = {"省": 1, "直辖市": 1, "自治区": 1, "市": 2, "自治州": 2,
                "地区": 2, "盟": 2, "区": 3, "县": 3, "市辖区": 3,
                "县级市": 3, "街道": 4, "镇": 4, "乡": 4,
                "村": 5, "社区": 5, "居委会": 5, "村委会": 5}
    if s in text_map:
        return text_map[s]
    if s.isdigit():
        lvl = int(s)
        return lvl if 1 <= lvl <= 5 else None
    # 尝试从文本里解析“第X级”
    return None


def _build_district_rows(df, mapping):
    """将单个 DataFrame 解析为 t_district_code 行列表。"""
    rows = []
    skipped = 0
    # P06：用 to_dict("records") 替代 iterrows()，避免逐行 Series/索引转换开销
    for r in df.to_dict("records"):
        raw_code = r.get(mapping.get("code"))
        raw_name = r.get(mapping.get("name"))
        code = _norm_code(raw_code)
        name = str(raw_name).strip() if raw_name else None
        if not code or not name:
            skipped += 1
            continue
        level = _norm_level(r.get(mapping.get("level"))) if mapping.get("level") else None
        if level is None:
            # 依据代码长度推导层级
            digits = code.rstrip("0")
            if len(digits) <= 2:
                level = 1
            elif len(digits) <= 4:
                level = 2
            elif len(digits) <= 6:
                level = 3
            elif len(digits) <= 9:
                level = 4
            else:
                level = 5

        rid = (str(r.get(mapping["id"])).strip()
               if mapping.get("id") and r.get(mapping["id"]) else code)

        # 推导 pid / pids（优先使用 Excel 提供的 pid/pids）
        if mapping.get("pid") and r.get(mapping["pid"]):
            pid = str(r.get(mapping["pid"])).strip()
        else:
            digits = code.rstrip("0")
            if level == 1:
                pid = None
            elif level == 2:
                pid = code[:2] + config.PROVINCE_PAD
            elif level == 3:
                pid = code[:4] + config.CITY_PAD
            elif level == 4:
                pid = code[:6] + config.DISTRICT_PAD
            else:
                pid = code[:9] + "000"

        if mapping.get("pids") and r.get(mapping["pids"]):
            pids = str(r.get(mapping["pids"])).strip()
        else:
            if level == 1:
                pids = ""
            elif level == 2:
                pids = code[:2] + config.PROVINCE_PAD
            elif level == 3:
                pids = f"{code[:2] + config.PROVINCE_PAD},{code[:4] + config.CITY_PAD}"
            elif level == 4:
                pids = f"{code[:2] + config.PROVINCE_PAD},{code[:4] + config.CITY_PAD},{code[:6] + config.DISTRICT_PAD}"
            else:
                pids = (f"{code[:2] + config.PROVINCE_PAD},"
                        f"{code[:4] + config.CITY_PAD},"
                        f"{code[:6] + config.DISTRICT_PAD},{code[:9] + '000'}")

        province_code = (str(r.get(mapping["province_code"])).strip()
                         if mapping.get("province_code") and r.get(mapping["province_code"])
                         else code[:2] + config.PROVINCE_PAD)
        city_code = (str(r.get(mapping["city_code"])).strip()
                     if mapping.get("city_code") and r.get(mapping["city_code"])
                     else (code[:4] + config.CITY_PAD if level >= 2 else None))

        rows.append({
            "id": rid,
            "pids": pids,
            "pid": pid,
            "district_code": code,
            "district_name": name,
            "admin_level": level,
            "province_code": province_code,
            "city_code": city_code,
        })
    return rows, skipped


def detect_student_columns(df, mapping=None):
    """返回学生表的列映射 {name, id_card, address}（用户指定优先，否则自动识别）。"""
    keyword_map = {
        "name": ["姓名", "name"],
        "id_card": ["身份证", "id_card", "idcard", "证件"],
        "address": ["地址", "address", "住址"],
    }
    return mapping or _auto_map(list(df.columns), keyword_map)


def preview_student_file(path, mapping=None, n=10):
    """读取学生文件前 n 行用于页面预览。返回 {columns, detected, rows}。"""
    if path.lower().endswith(".csv"):
        df = pd.read_csv(path, dtype=str)
    else:
        df = pd.read_excel(path, dtype=str)
    df = df.where(pd.notna(df), None)
    detected = detect_student_columns(df, mapping)
    rows = df.head(n).to_dict("records")
    return {
        "columns": list(df.columns),
        "detected": detected,
        "rows": rows,
    }


def import_district_excel(db, path, column_mapping=None, on_progress=None):
    """
    导入行政区划 Excel（支持多 sheet：本数据集按省分 sheet）。
    column_mapping: 可选 {id,name,code,level,pid,pids,province_code,city_code}
    on_progress(done, total, info): 可选进度回调（info 含 phase/detail，前端轮询用）。
    返回统计 dict。

    P07：所有 sheet 的行先累计，最后单次 insert_districts 提交，
    避免每个 sheet 一次事务（31 省 = 31 次提交）。
    """
    keyword_map = {
        "code": ["代码", "code", "区划", "gbm", "gb"],
        "name": ["名称", "name", "地名"],
        "level": ["级别", "等级", "level", "层级", "行政"],
        "id": ["id", "编号", "序号"],
        "pids": ["pids", "全路径", "path"],
        "pid": ["pid", "父", "上级", "parent"],
        "province_code": ["省代码", "province_code", "省级代码"],
        "city_code": ["市代码", "city_code", "市级代码"],
    }

    xls = pd.ExcelFile(path)
    num_sheets = len(xls.sheet_names)
    total_rows = 0
    imported = 0
    skipped = 0
    detected_mapping = None
    all_rows = []
    sheet_for_prov = {}  # prov_code -> 该省所在 sheet 名（缺失省时用于补省名）

    if on_progress:
        on_progress(0, num_sheets, {"phase": "reading",
                                    "detail": f"准备导入 {num_sheets} 个数据表…"})

    for idx, sheet in enumerate(xls.sheet_names, 1):
        df = pd.read_excel(xls, sheet_name=sheet, dtype=str)
        df = df.where(pd.notna(df), None)
        if detected_mapping is None:
            detected_mapping = column_mapping or _auto_map(list(df.columns), keyword_map)
            if not detected_mapping.get("code") or not detected_mapping.get("name"):
                raise ValueError("无法识别代码列或名称列，请在页面手动指定列映射后重试。")
        rows, sk = _build_district_rows(df, detected_mapping)
        skipped += sk
        total_rows += len(df)
        imported += len(rows)
        for r in rows:
            all_rows.append(r)
            if r["admin_level"] >= 2:
                sheet_for_prov.setdefault(
                    r["district_code"][:2] + config.PROVINCE_PAD, sheet)
        if on_progress:
            on_progress(idx, num_sheets, {"phase": "reading", "sheet": sheet,
                                          "detail": f"已读取「{sheet}」（{len(df)} 行），"
                                                    f"第 {idx}/{num_sheets} 个表"})

    # 合成缺失的省级(admin_level=1)行：以 sheet 名为省名，补全层级顶层
    existing_prov = {r["district_code"] for r in all_rows if r["admin_level"] == 1}
    for prov_code, sheet_name in sheet_for_prov.items():
        if prov_code in existing_prov:
            continue
        if db.get_by_district_code(prov_code, 1):  # DB 已存在该省，跳过合成
            continue
        prov_id = "PV-" + prov_code
        # 该省下属市级行的 pid 指向合成省（与单 sheet 时行为一致）
        for r in all_rows:
            if r["admin_level"] == 2 and r["district_code"][:2] + config.PROVINCE_PAD == prov_code:
                r["pid"] = prov_id
        all_rows.insert(0, {
            "id": prov_id, "pids": "", "pid": None,
            "district_code": prov_code, "district_name": sheet_name,
            "admin_level": 1, "province_code": prov_code, "city_code": None,
        })
        existing_prov.add(prov_code)
        imported += 1

    # P07：所有行一次性批量入库（单次事务）
    if all_rows:
        if on_progress:
            on_progress(num_sheets, num_sheets, {"phase": "writing",
                                                 "detail": f"正在写入 {len(all_rows)} 条区划记录…"})
        db.insert_districts(all_rows)
    if on_progress:
        on_progress(num_sheets, num_sheets, {"phase": "done", "detail": "导入完成"})

    return {
        "total_rows": total_rows,
        "imported": imported,
        "skipped": skipped,
        "sheets": len(xls.sheet_names),
        "detected_mapping": detected_mapping,
    }


def import_students(db, path, mapping=None, batch_id=None, on_progress=None):
    """
    导入学生数据（支持 .xlsx / .csv）。
    mapping: 可选 {name, id_card, address}
    on_progress(done, total, info): 可选进度回调（info 含 phase/detail，前端轮询用）。
    """
    if path.lower().endswith(".csv"):
        df = pd.read_csv(path, dtype=str)
    else:
        df = pd.read_excel(path, dtype=str)
    df = df.where(pd.notna(df), None)

    # D1/P06：列映射仅收集用户填写的列名（留空 = 自动识别）
    mapping = mapping or detect_student_columns(df)
    if not mapping.get("id_card"):
        raise ValueError("无法识别身份证号列，请在页面手动指定列映射后重试。")

    batch_id = batch_id or f"B{uuid.uuid4().hex[:8].upper()}"
    # P06：用 to_dict("records") 替代 iterrows()，消除逐行 Series 转换开销
    records = df.to_dict("records")
    total = len(records)
    rows = []
    # 约上报 100 次，避免高频回调影响吞吐
    step = max(1, total // 100)
    if on_progress:
        on_progress(0, total, {"phase": "building", "detail": f"准备导入 {total} 行…"})
    for i, r in enumerate(records):
        raw_id = r.get(mapping["id_card"])
        # 空值 / NaN / "nan" 视为无效身份证，跳过该行
        if raw_id is None or (isinstance(raw_id, float) and pd.isna(raw_id)):
            continue
        id_card = str(raw_id).strip()
        if not id_card or id_card.lower() == "nan":
            continue
        name = _clean_cell(r.get(mapping["name"])) if mapping.get("name") else None
        address = _clean_cell(r.get(mapping["address"])) if mapping.get("address") else None
        rows.append({
            "batch_id": batch_id,
            "student_name": name,
            "id_card": id_card,
            "address": address,
        })
        if on_progress and (i + 1) % step == 0:
            on_progress(i + 1, total, {"phase": "building",
                                       "detail": f"已解析 {i + 1}/{total} 行"})
    if on_progress:
        on_progress(total, total, {"phase": "writing",
                                   "detail": f"正在写入 {len(rows)} 条学生记录…"})
    db.insert_students(rows)
    if on_progress:
        on_progress(total, total, {"phase": "done", "detail": "导入完成"})
    return {"batch_id": batch_id, "imported": len(rows)}


def detect_history_columns(df, mapping=None):
    """返回历史映射表的列映射（用户指定优先，否则自动识别）。"""
    keyword_map = {
        "old_code": ["旧代码", "旧码", "old_code", "oldcode", "old"],
        "new_code": ["新代码", "新码", "new_code", "newcode", "new"],
        "old_name": ["旧名称", "旧名", "old_name", "oldname"],
        "new_name": ["新名称", "新名", "new_name", "newname"],
        "change_type": ["变更", "类型", "change_type", "changetype"],
        "change_date": ["日期", "年份", "change_date", "changedate", "date"],
        "remark": ["备注", "remark", "说明"],
    }
    return mapping or _auto_map(list(df.columns), keyword_map)


def _norm_history_code(val):
    """历史映射代码规范化为 6 位数字串；12 位取前 6 位，其余去除非数字。"""
    if val is None:
        return None
    s = str(val).strip().replace(" ", "").replace("	", "")
    digits = "".join(ch for ch in s if ch.isdigit())
    if not digits:
        return None
    return digits[:6]


def import_history_mappings(db, path, column_mapping=None):
    """导入历史代码映射 Excel/CSV。

    列：旧代码 / 新代码 / 旧名称 / 新名称 / 变更类型 / 变更日期 / 备注。
    校验：每行至少提供 (旧代码+新代码) 或 (旧名称+新名称) 之一，否则跳过；
    以 old_code 为自然键 upsert（存在则覆盖）。
    返回 {"imported": n, "skipped": n}。
    """
    if path.lower().endswith(".csv"):
        df = pd.read_csv(path, dtype=str)
    else:
        df = pd.read_excel(path, dtype=str)
    df = df.where(pd.notna(df), None)
    detected = column_mapping or detect_history_columns(df)
    if not detected.get("old_code") and not detected.get("old_name"):
        raise ValueError("无法识别“旧代码”或“旧名称”列，请使用模板后重试。")
    records = df.to_dict("records")
    rows = []
    skipped = 0
    for r in records:
        old_code = (_norm_history_code(r.get(detected["old_code"]))
                    if detected.get("old_code") else None)
        new_code = (_norm_history_code(r.get(detected["new_code"]))
                    if detected.get("new_code") else None)
        old_name = (_clean_cell(r.get(detected["old_name"]))
                    if detected.get("old_name") else None)
        new_name = (_clean_cell(r.get(detected["new_name"]))
                    if detected.get("new_name") else None)
        if not ((old_code and new_code) or (old_name and new_name)):
            skipped += 1
            continue
        rows.append({
            "old_code": old_code,
            "new_code": new_code,
            "old_name": old_name,
            "new_name": new_name,
            "change_type": (_clean_cell(r.get(detected["change_type"]))
                            if detected.get("change_type") else None),
            "change_date": (_clean_cell(r.get(detected["change_date"]))
                            if detected.get("change_date") else None),
            "remark": (_clean_cell(r.get(detected["remark"]))
                       if detected.get("remark") else None),
        })
    db.insert_history_mappings(rows)
    return {"imported": len(rows), "skipped": skipped}
