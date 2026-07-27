"""uploads/ 临时目录清理工具：防止上传输入与导出输出无限膨胀。

安全前提
--------
uploads/ 下所有文件均为「导入输入」或「导出输出」的瞬态副本：
- 学生/区划 Excel 上传后会被导入进 SQLite（t_student / t_result / t_district_code），
  文件本身只是一次性副本，导入完成后即可删除，不影响已入库数据；
- 导出文件（result_export_*）在下载请求当下生成并立即 send_file 返回，旧文件无长生命周期引用；
- 预览临时文件（_preview_*）在请求 finally 中已清理，残留的也可安全删除；
- 启动时绝不会读取 uploads/（区划默认导入走 DATA_DIR）。

唯一例外：`PROTECTED_UPLOADS` 中的源数据文件（如 全国行政编码数据.xlsx），
它是用户重建区划库的来源，自动清理时永不动它；如要删除需人工确认并重新上传。

注意：清理仅应在「无导入/下载进行中」或「文件已不再被读取」时执行，
否则可能中断正在进行的请求（如刚生成的导出文件被并发下载时删除）。
"""
import os
import re

from config import UPLOAD_DIR, MAX_EXPORT_KEEP, PROTECTED_UPLOADS

# 导出文件名：result_export_<scope>_<ts>.{xlsx,csv}
_EXPORT_RE = re.compile(r"^result_export_.*\.(xlsx|csv)$")
# 预览临时文件
_PREVIEW_RE = re.compile(r"^_preview_")


def _safe_remove(fp):
    try:
        os.remove(fp)
        return True
    except OSError:
        return False


def prune_exports(max_keep=None, base_dir=None):
    """仅修剪导出文件，保留最新的 max_keep 个（默认 config.MAX_EXPORT_KEEP）。

    导出文件在下载时实时生成，当前正在下载的文件永远是最新的，因此
    按 mtime 保留最新 N 个即可安全限制数量，不会误删正在使用的文件。
    """
    d = base_dir or UPLOAD_DIR
    if not os.path.isdir(d):
        return 0
    if max_keep is None:
        max_keep = MAX_EXPORT_KEEP
    exports = []
    for fn in os.listdir(d):
        fp = os.path.join(d, fn)
        if os.path.isfile(fp) and _EXPORT_RE.match(fn):
            exports.append((os.path.getmtime(fp), fp))
    exports.sort(reverse=True)  # 最新在前
    removed = 0
    for _, fp in exports[max_keep:]:
        if _safe_remove(fp):
            removed += 1
    return removed


def prune_old_inputs(keep=None, base_dir=None):
    """删除上传输入文件（非导出、非受保护），但保留 keep 指定的当前文件。

    适用于导入完成后清理：keep 传刚保存的文件名（basename），避免把
    正在被后台线程读取的输入误删。受保护源数据永不删除。
    """
    d = base_dir or UPLOAD_DIR
    if not os.path.isdir(d):
        return 0
    keep = os.path.basename(keep) if keep else None
    removed = 0
    for fn in os.listdir(d):
        fp = os.path.join(d, fn)
        if not os.path.isfile(fp):
            continue
        if fn in PROTECTED_UPLOADS:
            continue
        if keep and fn == keep:
            continue
        # 导出与预览各有专门处理；此处只删其它（即用户上传的输入文件）
        if _EXPORT_RE.match(fn) or _PREVIEW_RE.match(fn):
            continue
        if _safe_remove(fp):
            removed += 1
    return removed


def prune_uploads(keep=None, max_keep=None, base_dir=None):
    """综合清理：删除预览临时 + 修剪导出 + 删除旧上传输入。

    keep: 当前正在使用/刚保存的文件名（basename），不删除。
    返回 (deleted_inputs, deleted_exports)。
    """
    # 预览临时永远可清
    d = base_dir or UPLOAD_DIR
    if os.path.isdir(d):
        for fn in os.listdir(d):
            fp = os.path.join(d, fn)
            if os.path.isfile(fp) and _PREVIEW_RE.match(fn):
                _safe_remove(fp)
    di = prune_old_inputs(keep=keep, base_dir=base_dir)
    de = prune_exports(max_keep=max_keep, base_dir=base_dir)
    return di, de


def clear_all_uploads(include_protected=False, base_dir=None):
    """一次性清空整个 uploads/（手动维护用）。

    include_protected=True 时连源数据文件一起删（危险，需用户明确确认）。
    返回删除的文件数。
    """
    d = base_dir or UPLOAD_DIR
    if not os.path.isdir(d):
        return 0
    removed = 0
    for fn in os.listdir(d):
        fp = os.path.join(d, fn)
        if not os.path.isfile(fp):
            continue
        if not include_protected and fn in PROTECTED_UPLOADS:
            continue
        if _safe_remove(fp):
            removed += 1
    return removed
