"""导出服务：将处理结果导出为 Excel / CSV。"""
import os

import pandas as pd

import config

COLUMNS = [
    ("student_name", "姓名"),
    ("id_card", "身份证号"),
    ("address", "地址"),
    ("province_name", "省"),
    ("province_code", "省级代码"),
    ("city_name", "市"),
    ("city_code", "市级代码"),
    ("district_name", "区县"),
    ("district_code", "区县代码"),
    ("match_status", "状态"),
]


def export_results(db, batch_id=None, status=None, fmt="xlsx", cleanup=False):
    """
    导出结果。返回生成的文件路径。

    一进一出：当 cleanup=True 且为整批/全量导出（status 为空）时，文件生成
    成功后自动清除对应的 t_student / t_result 流水，避免学生数据长期占用数据库；
    区划字典 t_district_code 不受影响。带 status 过滤的部分导出不触发清理，
    以免误删用户仍需查看的其他状态数据。
    """
    import datetime

    # P08：直接以 SQL 取数生成 DataFrame（一步到位），再 rename 为中文列名，
    # 消除原 Row→dict→中文键 dict→DataFrame 三次格式转换与整表复制。
    df = db.get_results_dataframe(batch_id=batch_id, status=status)
    if df.empty:
        df = pd.DataFrame(columns=[cn for _, cn in COLUMNS])
    else:
        df = df.rename(columns={en: cn for en, cn in COLUMNS})
        df = df[[cn for _, cn in COLUMNS]]

    os.makedirs(config.UPLOAD_DIR, exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    scope = batch_id or "all"
    if fmt == "csv":
        path = os.path.join(config.UPLOAD_DIR, f"result_export_{scope}_{ts}.csv")
        df.to_csv(path, index=False, encoding="utf-8-sig")
    else:
        path = os.path.join(config.UPLOAD_DIR, f"result_export_{scope}_{ts}.xlsx")
        df.to_excel(path, index=False)
    # 一进一出：导出成功后清理对应范围的学生流水
    if cleanup and status is None:
        db.delete_batch(batch_id)
    return path
