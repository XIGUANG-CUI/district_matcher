"""学生身份证区划代码转换工具

双引擎策略：
  引擎① 基于身份证前 6 位层级匹配行政区划
  引擎② 基于地址文本解析，并与引擎①交叉校验

运行：
  pip install -r requirements.txt
  python app.py                # 启动 Web 服务 http://127.0.0.1:5000

运行模式（由 config.txt 控制）：
  - 默认 TRAY=on：启动后最小化到系统托盘，双击图标或点菜单「打开主页」访问，
    点「退出」一键关闭。无图形界面的服务器环境会自动回退为前台直接运行。
  - 强制前台：python app.py --no-tray
  - 不自动开浏览器：config.txt 设 OPEN_BROWSER=off

端口与托盘开关均在 config.txt 中修改，保存后重启生效。

其他：
  python -m unittest discover -s tests   # 运行测试
  python -c "from sdk.district_matcher import DistrictMatcher; print(DistrictMatcher().match('23010519890714291X','哈尔滨市道外区'))"
"""
import os
import sys

# 允许从仓库根目录直接以 `python app.py` 运行
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

__version__ = "1.0"
