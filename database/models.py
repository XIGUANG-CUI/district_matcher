"""数据模型定义（引擎输入/输出的轻量容器）。"""
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class LevelResult:
    """单级行政区划匹配结果。

    mapped=True 表示该级代码经历史映射（t_history_mapping 旧码->新码）命中，
    用于引擎①匹配过程详情展示（区分"现行直接命中"与"历史映射命中"）。
    """
    code: Optional[str] = None
    name: Optional[str] = None
    matched: bool = False
    mapped: bool = False


@dataclass
class Engine1Result:
    province: LevelResult = field(default_factory=LevelResult)
    city: LevelResult = field(default_factory=LevelResult)
    district: LevelResult = field(default_factory=LevelResult)
    error: Optional[str] = None


@dataclass
class Engine2Result:
    province: Optional[str] = None
    city: Optional[str] = None
    district: Optional[str] = None
    # 反查得到的 12 位代码
    district_code: Optional[str] = None
    city_code: Optional[str] = None
    province_code: Optional[str] = None
    # 解析抽取但未直接反查到层级的辅助 token（乡镇/村），供兜底反推
    township: Optional[str] = None
    village: Optional[str] = None


@dataclass
class MatchResult:
    province_code: Optional[str] = None
    province_name: Optional[str] = None
    city_code: Optional[str] = None
    city_name: Optional[str] = None
    district_code: Optional[str] = None
    district_name: Optional[str] = None
    # 状态枚举（v2）：正确 / 已解析 / 已解析(仅证件) / 需复核 / 部分 / 无匹配 / 异常
    match_status: str = "无匹配"
    engine1_detail: str = ""
    engine2_detail: str = ""
    decision_path: str = ""
