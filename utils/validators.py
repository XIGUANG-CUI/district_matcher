"""数据校验工具。"""
import re

ID_CARD_RE = re.compile(r"^\d{17}[\dXx]$")
WEIGHTS = [7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2]
CHECK_CODES = ["1", "0", "X", "9", "8", "7", "6", "5", "4", "3", "2"]


def is_id_card_format(id_card):
    """是否符合 18 位身份证格式（前17位数字，末位为数字或 X）。"""
    if not id_card:
        return False
    return bool(ID_CARD_RE.match(id_card.strip()))


def id_card_checksum_ok(id_card):
    """校验 18 位身份证校验位是否正确。"""
    if not is_id_card_format(id_card):
        return False
    s = id_card.strip().upper()
    total = sum(int(s[i]) * WEIGHTS[i] for i in range(17))
    return CHECK_CODES[total % 11] == s[17]


def get_id_first6(id_card):
    """提取身份证前 6 位；非纯数字返回 None。"""
    if not id_card or len(id_card) < 6:
        return None
    first6 = id_card[:6]
    return first6 if first6.isdigit() else None


def is_first6_numeric(id_card):
    f = get_id_first6(id_card)
    return f is not None
