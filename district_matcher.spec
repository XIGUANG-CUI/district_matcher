# -*- mode: python ; coding: utf-8 -*-

import os
import sys
from PyInstaller.utils.hooks import collect_data_files, collect_submodules
from PyInstaller.building.api import COLLECT

# ====================== 基础配置 ======================
# 获取 spec 文件所在目录作为项目根路径
if '__file__' not in locals():
    spec_file = os.path.abspath(sys.argv[0])
else:
    spec_file = os.path.abspath(__file__)
base_path = os.path.dirname(spec_file)
block_cipher = None

# ====================== 版本信息配置 ======================
# version.txt 包含 PyInstaller 所需的 VSVersionInfo 结构
version_file = os.path.join(base_path, 'version.txt')

# 从 version.txt 动态读取产品名称和版本号
import re
exe_name = '区划代码转换工具'
if os.path.exists(version_file):
    with open(version_file, 'r', encoding='utf-8') as f:
        version_content = f.read()
    # 提取 ProductName
    match_name = re.search(r"StringStruct\(u'ProductName',\s*u'([^']+)'\)", version_content)
    if match_name:
        exe_name = match_name.group(1)
    # 提取 ProductVersion
    match_version = re.search(r"StringStruct\(u'ProductVersion',\s*u'([^']+)'\)", version_content)
    if match_version:
        exe_name += 'V' + match_version.group(1)

# ====================== 收集 DLL 二进制文件 ======================
binaries = []

# ====================== 收集数据文件 ======================
# 原则：只打包只读资源（模板/静态文件/区划源数据/版本号）。
# 可写运行时不打包，由 app.py 启动时自动创建：
#   database/     → init_db() 首次建库
#   uploads/      → 用户上传文件
datas = []

# 1. Jinja2 模板文件（保持子目录结构）
templates_dir = os.path.join(base_path, 'templates')
if os.path.exists(templates_dir):
    for root, dirs, files in os.walk(templates_dir):
        for file in files:
            src = os.path.join(root, file)
            rel = os.path.relpath(root, base_path)
            datas.append((src, rel))

# 2. 静态资源（CSS / JS）
static_dir = os.path.join(base_path, 'static')
if os.path.exists(static_dir):
    for root, dirs, files in os.walk(static_dir):
        for file in files:
            src = os.path.join(root, file)
            rel = os.path.relpath(root, base_path)
            datas.append((src, rel))

# 3. 区划源数据（全国行政编码数据.xlsx，只读参考数据，首次启动时导入到 SQLite）
data_dir = os.path.join(base_path, 'data')
if os.path.exists(data_dir):
    for file in os.listdir(data_dir):
        if file.lower().endswith('.xlsx'):
            datas.append((os.path.join(data_dir, file), 'data'))

# 4. 图标文件（如有）
for icon_name in ['favicon.ico', 'favicon.png', 'icon.ico', 'icon.png']:
    icon_path = os.path.join(base_path, icon_name)
    if os.path.exists(icon_path):
        datas.append((icon_path, '.'))

# 5. 运行配置文件（用户可修改 PORT / TRAY / OPEN_BROWSER）
config_path = os.path.join(base_path, 'config.txt')
if os.path.exists(config_path):
    datas.append((config_path, '.'))

# 6. openpyxl 的隐式依赖数据（Excel 读写需要）
datas += collect_data_files('openpyxl')

# ====================== 隐藏导入（防止分析遗漏） ======================
hiddenimports = []

# openpyxl 全部子模块（Excel 读写需要）
hiddenimports += collect_submodules('openpyxl')

# PIL/Pillow（托盘图标绘制）
hiddenimports += [
    'PIL',
    'PIL.Image',
    'PIL.ImageFile',
    'PIL.ImageDraw',
    'PIL.ImageFont',
    'PIL._imaging',
]

# pystray 系统托盘（Windows 平台 _win32 后端）
hiddenimports += [
    'pystray',
    'pystray._win32',
]

# tkinter（启动完成弹窗）
hiddenimports += [
    'tkinter',
    'tkinter.messagebox',
]

# Flask 及其依赖
hiddenimports += [
    'flask',
    'flask.json',
    'jinja2',
    'jinja2.ext',
    'itsdangerous',
    'werkzeug',
    'click',
    'markupsafe',
]

# pandas（Excel/CSV 读写）
hiddenimports += [
    'pandas',
    'pandas.io',
]

# 项目 database 包子模块
hiddenimports += [
    'database',
    'database.db_manager',
    'database.models',
]

# 项目 engine 包子模块
hiddenimports += [
    'engine',
    'engine.engine1',
    'engine.engine2',
    'engine.decider',
]

# 项目 services 子模块
hiddenimports += [
    'services',
    'services.import_service',
    'services.match_service',
    'services.export_service',
]

# 项目 utils 子模块
hiddenimports += [
    'utils',
    'utils.address_parser',
    'utils.validators',
    'utils.cleanup',
]

# 项目 sdk 子模块
hiddenimports += [
    'sdk',
    'sdk.district_matcher',
]

# 其他必要模块
hiddenimports += [
    'ipaddress',       # 局域网 IP 检测
    'threading',       # 后台任务线程
    'datetime',        # 时间处理
    'shutil',          # 文件操作
    'sqlite3',         # 数据库引擎
    'logging',         # 日志记录
    'logging.handlers',# 日志轮转
]

# ====================== 排除模块（减小体积） ======================
excludes = [
    'matplotlib',       # 不在本项目中绘图
    'scipy',            # 科学计算，不需要
    'PyQt5',            # GUI 框架，不需要
    'wx',               # GUI 框架，不需要
    'tensorflow',       # ML 框架，不需要
    'torch',            # ML 框架，不需要
    'sklearn',          # 机器学习，不需要
    'notebook',         # Jupyter，不需要
    'ipython',          # 交互式终端，不需要
    'prompt_toolkit',   # IPython 依赖
    'PIL.ImageQt',      # Qt 后端，不需要
    'PIL.ImageTk',      # ImageTk 不直接使用
    'PIL._webp',        # WebP 格式，不需要
    'tkinter.test',     # tkinter 测试
    'tkinter.ttk',      # ttk 主题组件，不需要
    'unittest',         # 单元测试框架
    'test',             # 测试目录

    'distutils',        # 已废弃
    'setuptools',       # 安装工具
]

# ====================== Analysis 分析配置 ======================
a = Analysis(
    ['app.py'],
    pathex=[base_path],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

# ====================== EXE 入口 ======================
# 子进程不需要 DLL 和资源（由 COLLECT 集中管理），exclude_binaries=True
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=exe_name,
    debug=True,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,              # 窗口化运行，不显示控制台黑窗
    disable_windowed_traceback=False,
    argv_emulation=False,
    version=version_file,        # 从 version.txt 读取版本信息
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=os.path.join(base_path, 'favicon.ico'),
)

# ====================== COLLECT 目录输出 ======================
# onedir 模式：将所有依赖收集到 dist/区划代码转换工具V1.0.0.0/ 目录
# 可写运行时数据（database/uploads）不在 datas 中，
# 由 app.py 启动时自动创建在 exe 同级目录，方便直接维护
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name=exe_name,
)
