"""Flask 主应用：数据初始化 / 学生导入 / 批量处理 / 单条查询 / RESTful API。"""
import os
import sys
import glob
import uuid
import threading
import collections

from flask import (Flask, render_template, request, redirect, url_for,
                   jsonify, send_file, flash)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config
import pandas as pd
from database.db_manager import DBManager
from services import import_service, match_service, export_service

app = Flask(__name__)
app.secret_key = "district_matcher_secret"
db = DBManager()

# P04：后台批处理任务注册表，key=batch_id，value={status, processed, total, stats, error}
_tasks = {}

# 导入后台任务注册表：key=task_id，value={status, phase, done, total, imported, skipped, batch_id, detail, error}
_import_tasks = {}

# D3：操作日志（最近 50 条），覆盖导入 / 处理 / 导出 / 初始化等关键动作
_oplog = collections.deque(maxlen=50)


def _log(msg):
    """记录一条操作日志（带时间戳，供页面“操作日志”面板展示）。"""
    import datetime
    _oplog.appendleft({"t": datetime.datetime.now().strftime("%H:%M:%S"),
                       "msg": msg})


# ------------------------- 页面路由 -------------------------
@app.route("/")
def index():
    return redirect(url_for("init_page"))


@app.route("/init", methods=["GET", "POST"])
def init_page():
    if request.method == "POST":
        action = request.form.get("action")
        if action == "reset":
            db.reset_db()
            flash("数据库已清空。")
        # 区划导入改由 /api/import/start 后台执行（带进度），此处不再同步阻塞请求
    stats = db.district_stats()
    total = db.count_districts()
    default_excel = _find_default_excel()
    return render_template("init.html", stats=stats, total=total,
                           default_excel=default_excel,
                           admin_level_name=config.ADMIN_LEVEL_NAME)


@app.route("/import", methods=["GET", "POST"])
def import_page():
    # 学生导入改由 /api/import/start 后台执行（带进度）；此路由仅负责渲染页面
    batches = db.list_batches()
    return render_template("import.html", batches=batches, oplog=list(_oplog))


@app.route("/import/preview", methods=["POST"])
def import_preview():
    """D3：预览学生文件前 10 行 + 自动识别列映射，供导入前确认。"""
    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify({"ok": False, "msg": "请选择学生数据文件。"}), 400
    path = os.path.join(config.UPLOAD_DIR, "_preview_" + f.filename)
    f.save(path)
    try:
        mapping = {}
        for fld, key in (("col_id_card", "id_card"),
                         ("col_address", "address"),
                         ("col_name", "name")):
            v = request.form.get(fld)
            if v and v.strip():
                mapping[key] = v.strip()
        pv = import_service.preview_student_file(path, mapping or None)
        return jsonify({"ok": True, **pv})
    except Exception as e:
        return jsonify({"ok": False, "msg": f"预览失败：{e}"}), 500
    finally:
        try:
            os.remove(path)
        except Exception:
            pass


@app.route("/result", methods=["GET", "POST"])
def result_page():
    batch_id = request.args.get("batch_id") or request.form.get("batch_id")
    status = request.args.get("status") or request.form.get("status")
    processed = None
    processing = False
    if request.method == "POST" and request.form.get("action") == "process":
        if batch_id:
            # P04：后台线程执行，避免大批次阻塞 HTTP 请求；前端轮询进度
            _start_process_task(batch_id)
            processing = True
        else:
            flash("请选择批次。")
    page_size = 50
    page = int(request.args.get("page", 1))
    total = db.count_results(batch_id=batch_id, status=status or None)
    total_pages = max(1, (total + page_size - 1) // page_size)
    page = max(1, min(page, total_pages))
    results = db.query_results(batch_id=batch_id, status=status or None,
                               page=page, page_size=page_size)
    stats = db.result_stats(batch_id)
    batches = db.list_batches()
    return render_template("result.html", results=results, stats=stats,
                           batches=batches, batch_id=batch_id, status=status,
                           processed=processed, page=page,
                           total=total, total_pages=total_pages,
                           page_size=page_size, processing=processing,
                           oplog=list(_oplog))


def _start_process_task(batch_id):
    """P04：在后台线程中运行匹配，进度写入 _tasks 供轮询。"""
    _tasks[batch_id] = {"status": "running", "processed": 0,
                        "total": 0, "stats": None}

    def _on_progress(done, total, stats):
        _tasks[batch_id] = {"status": "running", "processed": done,
                            "total": total, "stats": stats}

    def _run():
        try:
            stats = match_service.process_batch(db, batch_id, on_progress=_on_progress)
            cur = _tasks.get(batch_id, {})
            _tasks[batch_id] = {
                "status": "done",
                "processed": cur.get("total", 0),
                "total": cur.get("total", 0),
                "stats": stats,
            }
            _log(f"批次 {batch_id} 处理完成：{stats}")
        except Exception as e:  # 捕获异常，避免后台线程静默失败
            _tasks[batch_id] = {"status": "error", "error": str(e)}

    threading.Thread(target=_run, daemon=True).start()


@app.route("/api/result/progress")
def result_progress():
    """P04：返回某批次后台处理的实时进度。"""
    batch_id = request.args.get("batch_id")
    return jsonify(_tasks.get(batch_id, {"status": "idle"}))


# ------------------------- 导入后台任务（带进度） -------------------------
def _start_import_task(kind, action=None, file_path=None, mapping=None):
    """在后台线程中执行导入（区划初始化 / 学生数据），进度写入 _import_tasks 供前端轮询。

    与 P04 的 _start_process_task 同一思路：避免大文件导入阻塞 HTTP 请求，
    前端通过 /api/import/progress 实时展示进度条。
    """
    task_id = "imp_" + uuid.uuid4().hex[:10]
    _import_tasks[task_id] = {
        "status": "running", "phase": "init", "done": 0, "total": 0,
        "imported": 0, "skipped": 0, "batch_id": None,
        "detail": "准备中…", "error": None,
    }

    def _on_progress(done, total, info):
        rec = _import_tasks.get(task_id)
        if not rec:
            return
        rec["done"] = done
        rec["total"] = total
        if isinstance(info, dict):
            rec["phase"] = info.get("phase", rec["phase"])
            rec["detail"] = info.get("detail", rec.get("detail", ""))

    def _run():
        rec = _import_tasks.get(task_id)
        try:
            if kind == "district":
                if action == "import_default":
                    fpath = _find_default_excel()
                    if not fpath:
                        raise RuntimeError("未找到 data 目录下的 Excel 文件。")
                else:
                    fpath = file_path
                summary = import_service.import_district_excel(
                    db, fpath, on_progress=_on_progress)
                if rec:
                    rec.update({
                        "status": "done",
                        "imported": summary["imported"],
                        "skipped": summary.get("skipped", 0),
                        "done": rec.get("total", 0),
                        "total": rec.get("total", 0),
                        "detail": f"成功 {summary['imported']} 条，"
                                  f"跳过 {summary['skipped']} 条",
                    })
                    _log(f"初始化导入区划：成功 {summary['imported']} 条，"
                         f"跳过 {summary['skipped']} 条")
                    # 导入完成后清理 uploads/ 旧输入，防止无限膨胀（源数据受保护）
                    from utils import cleanup
                    cleanup.prune_uploads(keep=os.path.basename(file_path)
                                          if file_path else None)
            elif kind == "student":
                summary = import_service.import_students(
                    db, file_path, mapping=mapping, on_progress=_on_progress)
                if rec:
                    rec.update({
                        "status": "done",
                        "imported": summary["imported"],
                        "batch_id": summary["batch_id"],
                        "done": rec.get("total", 0),
                        "total": rec.get("total", 0),
                        "detail": f"批次 {summary['batch_id']}，共 {summary['imported']} 条",
                    })
                    _log(f"导入学生数据：批次 {summary['batch_id']}，"
                         f"共 {summary['imported']} 条"
                         + (f"，列映射 {mapping}" if mapping else "，列自动识别"))
                    # 导入完成后清理 uploads/ 旧输入，防止无限膨胀
                    from utils import cleanup
                    cleanup.prune_uploads(keep=os.path.basename(file_path)
                                          if file_path else None)
        except Exception as e:
            if rec:
                rec.update({"status": "error", "error": str(e), "detail": "导入失败"})

    threading.Thread(target=_run, daemon=True).start()
    return task_id


@app.route("/api/import/start", methods=["POST"])
def api_import_start():
    """启动后台导入任务（区划初始化 / 学生数据），返回 task_id 供前端轮询进度。"""
    kind = request.form.get("kind")
    if kind == "district":
        action = request.form.get("action")  # import_default | upload
        file_path = None
        if action == "upload":
            f = request.files.get("file")
            if not f or not f.filename:
                return jsonify({"ok": False, "msg": "请选择 Excel 文件。"}), 400
            file_path = os.path.join(config.UPLOAD_DIR, f.filename)
            f.save(file_path)
        # import_default 时后台线程内再解析默认目录，避免阻塞请求
        task_id = _start_import_task("district", action=action, file_path=file_path)
        return jsonify({"ok": True, "task_id": task_id})
    elif kind == "student":
        f = request.files.get("file")
        if not f or not f.filename:
            return jsonify({"ok": False, "msg": "请选择学生数据文件（.xlsx / .csv）。"}), 400
        file_path = os.path.join(config.UPLOAD_DIR, f.filename)
        f.save(file_path)
        mapping = {}
        for fld, key in (("col_id_card", "id_card"),
                         ("col_address", "address"),
                         ("col_name", "name")):
            v = request.form.get(fld)
            if v and v.strip():
                mapping[key] = v.strip()
        task_id = _start_import_task("student", file_path=file_path, mapping=mapping or None)
        return jsonify({"ok": True, "task_id": task_id})
    return jsonify({"ok": False, "msg": "未知导入类型。"}), 400


@app.route("/api/import/progress")
def api_import_progress():
    """返回某导入任务的实时进度。"""
    task_id = request.args.get("task_id")
    return jsonify(_import_tasks.get(task_id, {"status": "idle"}))


@app.route("/export")
def export_route():
    batch_id = request.args.get("batch_id")
    status = request.args.get("status")
    fmt = request.args.get("fmt", "xlsx")
    path = export_service.export_results(db, batch_id=batch_id,
                                         status=status or None, fmt=fmt,
                                         cleanup=True)
    _log(f"导出结果：批次 {batch_id or '全部'}，格式 {fmt}，"
         f"已自动清理对应学生流水")
    # 限制导出文件数量，防止无限膨胀（当前下载文件为最新，必在保留范围内）
    from utils import cleanup
    cleanup.prune_exports()
    return send_file(path, as_attachment=True)


# ------------------------- 导入模板下载 -------------------------
def _send_xlsx_template(df, sheet_name, filename):
    import io
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        df.to_excel(w, index=False, sheet_name=sheet_name)
    buf.seek(0)
    return send_file(
        buf,
        as_attachment=True,
        download_name=filename,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@app.route("/template/student")
def template_student():
    """学生导入模板：姓名 / 身份证号 / 地址（列名与自动识别规则一致）。"""
    df = pd.DataFrame([
        {"姓名": "张三", "身份证号": "23010519900101001X",
         "地址": "黑龙江省哈尔滨市道外区南马路1号"},
        {"姓名": "李四", "身份证号": "110108200003154578",
         "地址": "北京市海淀区中关村大街1号"},
        {"姓名": "王五", "身份证号": "44030519951220002X",
         "地址": "广东省深圳市南山区科技园路2号"},
    ])
    return _send_xlsx_template(df, "学生导入模板", "学生导入模板.xlsx")


@app.route("/template/district")
def template_district():
    """行政区划导入模板：与官方 GB/T 2260 数据集列结构一致。"""
    df = pd.DataFrame([
        {"ID": 1, "PIDS": "", "PID": "", "行政区域代码": "110000000000",
         "城乡分类代码": "", "行政区域名称": "北京市", "行政层级": 1},
        {"ID": 2, "PIDS": "110000000000", "PID": "110000000000",
         "行政区域代码": "110100000000", "城乡分类代码": "",
         "行政区域名称": "市辖区", "行政层级": 2},
        {"ID": 3, "PIDS": "110000000000,110100000000", "PID": "110100000000",
         "行政区域代码": "110108000000", "城乡分类代码": "",
         "行政区域名称": "海淀区", "行政层级": 3},
    ])
    return _send_xlsx_template(df, "区划导入模板", "区划导入模板.xlsx")


@app.route("/query", methods=["GET", "POST"])
def query_page():
    result = None
    if request.method == "POST":
        id_card = request.form.get("id_card", "")
        address = request.form.get("address", "")
        if id_card:
            # P12：查询页需展示匹配过程，构建 detail；API/SDK 单条匹配路径默认跳过
            res = match_service.run_match(db, id_card, address, with_detail=True)
            result = {
                "province_code": res.province_code,
                "province_name": res.province_name,
                "city_code": res.city_code,
                "city_name": res.city_name,
                "district_code": res.district_code,
                "district_name": res.district_name,
                "match_status": res.match_status,
                "engine1_detail": res.engine1_detail,
                "engine2_detail": res.engine2_detail,
                "decision_path": res.decision_path,
            }
        else:
            flash("请输入身份证号。")
    return render_template("query.html", result=result)


# ------------------------- RESTful API -------------------------
@app.route("/api/match", methods=["POST"])
def api_match():
    data = request.get_json(force=True, silent=True) or {}
    id_card = data.get("id_card", "")
    address = data.get("address", "")
    res = match_service.run_match(db, id_card, address)
    return jsonify({
        "code": 0,
        "data": {
            "province_code": res.province_code,
            "province_name": res.province_name,
            "city_code": res.city_code,
            "city_name": res.city_name,
            "district_code": res.district_code,
            "district_name": res.district_name,
            "match_status": res.match_status,
        },
    })


@app.route("/api/match/batch", methods=["POST"])
def api_match_batch():
    data = request.get_json(force=True, silent=True) or {}
    students = data.get("students", [])
    out = []
    for s in students:
        res = match_service.run_match(db, s.get("id_card", ""), s.get("address", ""))
        out.append({
            "name": s.get("name"),
            "province_code": res.province_code,
            "province_name": res.province_name,
            "city_code": res.city_code,
            "city_name": res.city_name,
            "district_code": res.district_code,
            "district_name": res.district_name,
            "match_status": res.match_status,
        })
    return jsonify({"code": 0, "data": out})


# ------------------------- 工具 -------------------------
def _find_default_excel():
    """返回 data 目录下首个 .xlsx 路径；加 30s TTL 缓存，避免每次请求 glob 扫盘（P14）。"""
    import time
    now = time.time()
    if now - _default_excel_cache["ts"] < 30:
        return _default_excel_cache["path"]
    files = glob.glob(os.path.join(config.DATA_DIR, "*.xlsx"))
    p = files[0] if files else None
    _default_excel_cache["path"] = p
    _default_excel_cache["ts"] = now
    return p


_default_excel_cache = {"path": None, "ts": 0}


_config_cache = None


def _read_config_file():
    """读取同级 config.txt，返回 {大写键: 值} 字典。出错返回空字典。

    模块级缓存：config.txt 为静态配置，进程内只解析一次，避免 load_port /
    _flag_on 每次重复读文件（P13）。
    """
    global _config_cache
    if _config_cache is not None:
        return _config_cache
    data = {}
    try:
        cfg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.txt")
        if not os.path.exists(cfg_path):
            _config_cache = data
            return data
        with open(cfg_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" not in line:
                    continue
                key, value = line.split("=", 1)
                data[key.strip().upper()] = value.strip()
    except Exception:
        pass
    _config_cache = data
    return data


def load_port():
    """从 config.txt 读取运行端口。

    规则：文件缺失 / 无 PORT / 非整数 / 越界(非1024~65535) -> 回退 5000
    """
    raw = _read_config_file().get("PORT")
    if raw is None:
        return 5000
    try:
        port = int(raw)
    except ValueError:
        return 5000
    return port if 1024 <= port <= 65535 else 5000


def _flag_on(name, default=True):
    """读取 config.txt 中的开关项（on/true/yes/1/开启/是 视为开启）。"""
    raw = _read_config_file().get(name.upper())
    if raw is None:
        return default
    return raw.lower() in ("1", "on", "true", "yes", "y", "开启", "是")


def _make_icon_image():
    """用 Pillow 生成托盘图标（蓝色底 + 白色方框），避免依赖外部图片文件。"""
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (64, 64), (37, 99, 235, 255))
    d = ImageDraw.Draw(img)
    d.rectangle([12, 12, 52, 52], fill=(255, 255, 255, 255))
    d.rectangle([22, 22, 42, 42], fill=(37, 99, 235, 255))
    d.rectangle([28, 28, 36, 36], fill=(255, 255, 255, 255))
    return img


def _run_server(port, debug):
    """在后台线程中启动 Flask。"""
    app.run(host="127.0.0.1", port=port, debug=debug, use_reloader=False)


def run_tray(port, open_browser):
    """系统托盘模式：Flask 跑后台线程，图标驻留托盘区。"""
    import threading
    import webbrowser
    import time
    import pystray
    from pystray import Menu, MenuItem

    url = f"http://127.0.0.1:{port}"
    server = threading.Thread(target=_run_server, args=(port, False), daemon=True)
    server.start()
    time.sleep(1.5)  # 等服务就绪后再尝试打开浏览器
    if open_browser:
        webbrowser.open(url)

    def open_home(icon, item):
        webbrowser.open(url)

    def quit_app(icon, item):
        icon.stop()
        os._exit(0)

    menu = Menu(
        MenuItem("打开主页", open_home),
        MenuItem("退出", quit_app),
    )
    icon = pystray.Icon(
        "district_matcher",
        _make_icon_image(),
        f"区划代码转换工具 · 端口 {port}",
        menu,
    )
    icon.run()


if __name__ == "__main__":
    port = load_port()
    force_no_tray = "--no-tray" in sys.argv
    use_tray = _flag_on("TRAY", default=True) and not force_no_tray

    if use_tray:
        try:
            print(f"[district_matcher] 以托盘模式启动，端口：{port}")
            run_tray(port, _flag_on("OPEN_BROWSER", default=True))
        except Exception as e:
            # 无图形界面（如服务器/无桌面环境）时回退为直接前台运行
            print(f"[district_matcher] 托盘不可用（{e}），回退为前台运行。")
            _run_server(port, True)
    else:
        print(f"[district_matcher] 启动端口：{port}（可在 config.txt 中修改 PORT / TRAY）")
        _run_server(port, True)
