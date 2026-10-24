"""区划匹配服务包（纯算法层 + 导入导出）。

约束（见 PORTING_GUIDE 3.2）：
- 本包内不得 import flask / models / config，只允许标准库与包内相对导入；
- 字典读取一律通过外部注入的 Repository（鸭子类型），便于 FakeRepo 单测；
- 包内 `models` 是 dataclass 模块，不是目标系统的 models 包，一律 `from .models import ...`。
"""
