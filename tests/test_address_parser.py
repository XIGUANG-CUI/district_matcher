import unittest

from utils import address_parser


class TestAddressParser(unittest.TestCase):
    def test_full_province_city_district(self):
        parsed = address_parser.parse_address("黑龙江省哈尔滨市道外区水泥路99-4号")
        self.assertEqual(parsed["province"], "黑龙江省")
        self.assertEqual(parsed["city"], "哈尔滨市")
        self.assertEqual(parsed["district"], "道外区")

    def test_province_without_city(self):
        parsed = address_parser.parse_address("黑龙江省依兰县三道岗镇东河村联合屯")
        self.assertEqual(parsed["province"], "黑龙江省")
        self.assertEqual(parsed["city"], None)
        self.assertEqual(parsed["district"], "依兰县")
        self.assertEqual(parsed["township"], "三道岗镇")
        self.assertEqual(parsed["village"], "东河村")

    def test_municipality(self):
        parsed = address_parser.parse_address("北京市海淀区中关村大街1号")
        self.assertEqual(parsed["province"], "北京市")
        self.assertEqual(parsed["city"], "北京市")
        self.assertEqual(parsed["district"], "海淀区")

    def test_ningxia_short_form(self):
        """宁夏简称：地址省略“回族自治区”后缀，应正确识别并剥离省前缀。"""
        parsed = address_parser.parse_address("宁夏彭阳县草庙乡周庄村前岔队348号")
        self.assertEqual(parsed["province"], "宁夏回族自治区")
        self.assertEqual(parsed["city"], None)
        self.assertEqual(parsed["district"], "彭阳县")
        self.assertEqual(parsed["township"], "草庙乡")
        self.assertEqual(parsed["village"], "周庄村")

    def test_inner_mongolia_short_form(self):
        """内蒙古简称：应展开为全称，避免市正则吃进省前缀。"""
        parsed = address_parser.parse_address(
            "内蒙古呼伦贝尔市莫力达瓦达斡尔族自治旗红彦镇新多村349室"
        )
        self.assertEqual(parsed["province"], "内蒙古自治区")
        self.assertEqual(parsed["city"], "呼伦贝尔市")
        self.assertEqual(parsed["district"], "莫力达瓦达斡尔族自治旗")
        self.assertEqual(parsed["township"], "红彦镇")
        self.assertEqual(parsed["village"], "新多村")

    def test_autonomous_full_form_unchanged(self):
        """全称与简称应等价。"""
        short = address_parser.parse_address(
            "新疆乌鲁木齐市天山区解放南路1号"
        )
        full = address_parser.parse_address(
            "新疆维吾尔自治区乌鲁木齐市天山区解放南路1号"
        )
        self.assertEqual(short, full)
        self.assertEqual(short["province"], "新疆维吾尔自治区")
        self.assertEqual(short["city"], "乌鲁木齐市")
        self.assertEqual(short["district"], "天山区")

    def test_guangxi_old_name_not_double_expanded(self):
        """罕见旧称“广西省”不应被展开成“广西壮族自治区省”。"""
        parsed = address_parser.parse_address("广西省南宁市兴宁区")
        self.assertEqual(parsed["province"], "广西省")  # 保留原样，让后续正则自然识别


if __name__ == "__main__":
    unittest.main()
