"""决策引擎 v2 + 引擎②强化 集成验证（基于真实区划库）。

覆盖：
A. 用户 40 条地址：引擎②层级3解析命中率
B. 决策器 v2 各分支：冲突→需复核 / 一致→正确 / 空地址→已解析(仅证件)
   / e2省市+e1区县→已解析 / 乡镇反推→引擎②命中
C. 县级市省略层级2的市（用户强调项）反查成功
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database.db_manager import DBManager
from engine import engine1, engine2, decider


ADDRESSES = [
    "黑龙江省嫩江市新华街三委18组阳光银座小区5号楼2单元201室",
    "黑龙江省嫩江市建边农垦社区C区一委142号",
    "黑龙江省嫩江市建边农垦社区C区十四委40号",
    "黑龙江省嫩江市联兴乡双兴村北兴西南街屯121号",
    "黑龙江省东宁市老黑山镇南村九佛沟屯225-2号",
    "黑龙江省嫩江市联兴乡银河村1组454号",
    "黑龙江省嫩江市科洛镇石头沟子村1组120号",
    "黑龙江省嫩江市霍龙门乡前屯村1组135号",
    "黑龙江省嫩江市前进镇繁荣村四组70号",
    "黑龙江省嫩江市长福镇德胜村1组151号",
    "黑龙江省铁力市桃山镇人和社区五组",
    "黑龙江省嫩江县临江乡赤卫村1组306号",
    "黑龙江省嫩江市塔溪乡沐河村二组539号",
    "黑龙江省嫩江市海江镇五星村朝阳屯71号",
    "黑龙江省嫩江市多宝山镇先富村5组67号",
    "黑龙江省嫩江市大西江农垦社区C区一委189号",
    "黑龙江省嫩江市海江镇中心村兴业屯47号",
    "黑龙江省黑河市嫩江市曙光村",
    "黑龙江省嫩江市塔溪乡塔溪村光明屯95号",
    "黑龙江省东宁县老黑山镇二道沟村204号",
    "黑龙江省东宁市东宁镇繁荣街阳光五区9号楼2单元501室",
    "黑龙江省东宁市道河镇和平村284号",
    "黑龙江省抚远市前哨农垦社区C区八委79号",
    "黑龙江省抚远市乌苏镇抓吉赫哲族村二组",
    "黑龙江省铁力市王杨乡北河村",
    "黑龙江省嫩江市海江镇新胜村后东发屯309号",
    "黑龙江省嫩江市东风街二委三组民政局北楼351室",
    "黑龙江省嫩江市白云乡青良村1组1854号",
    "黑龙江省嫩江县临江乡江南村1组426号",
    "黑龙江省嫩江市建边农垦社区C区一委111号",
    "辽宁省普兰店市丰荣街道办事处谷泡子村满店屯2-22号",
    "黑龙江省嫩江市联兴乡联兴村1组766号",
    "黑龙江省嫩江市东风街二委三组民政局北楼351室",
    "黑龙江省抚远市乌苏镇永丰村一组",
    "黑龙江省东宁市道河镇洞庭村15号",
    "黑龙江省嫩江市塔溪乡西荒村西荒屯142号",
    "河北省三河市燕郊开发区大街北欧小镇16号楼B单元1106室",
    "黑龙江省漠河县西林吉镇东1街2组沿湖城小区39号楼1单元602室",
    "黑龙江省嫩江市临江街四委29组15号",
    "黑龙江省双城市希勤乡玉丰村十四委",
]


def part_a_engine2(db):
    print("=== A. 引擎②层级3解析（用户40条地址）===")
    ok = 0
    for a in ADDRESSES:
        r = engine2.engine2_parse(db, a)
        if r.district_code:
            ok += 1
        else:
            print("  [未命中]", a)
    print(f"  命中 {ok}/{len(ADDRESSES)}\n")


def part_b_decider(db):
    print("=== B. 决策器 v2 分支 ===")

    def run(idc, addr):
        e1 = engine1.engine1_match(db, idc)
        e2 = engine2.engine2_parse(db, addr)
        return decider.dual_engine_decide(db, e1, e2)

    # 冲突：内蒙古证件 vs 黑龙江地址 -> 需复核，输出引擎②结果
    r = run("15072220100606273X", "黑龙江省嫩江市铁西街二委4组长城商场综合楼1单元201室")
    assert r.match_status == "需复核", r.match_status
    assert r.district_name == "嫩江市" and r.province_name == "黑龙江省"
    print(f"  冲突→需复核 ✓  ({r.province_name}/{r.city_name}/{r.district_name})")

    # 一致：嫩江证件 vs 嫩江地址 -> 正确
    r = run("23118319900101001X", "黑龙江省嫩江市铁西街二委4组长城商场综合楼1单元201室")
    assert r.match_status == "正确", r.match_status
    print(f"  一致→正确 ✓  ({r.province_name}/{r.city_name}/{r.district_name})")

    # 空地址 -> 纯引擎①兜底 -> 已解析(仅证件)
    r = run("23118319900101001X", "")
    assert r.match_status == "已解析(仅证件)", r.match_status
    print(f"  空地址→已解析(仅证件) ✓  ({r.province_name}/{r.city_name}/{r.district_name})")

    # e2 仅省市 + e1 区县 -> 已解析
    r = run("23118319900101001X", "黑龙江省黑河市")
    assert r.match_status == "已解析", r.match_status
    assert r.district_name == "嫩江市"
    print(f"  e2省市+e1区县→已解析 ✓  ({r.province_name}/{r.city_name}/{r.district_name})")

    # 乡镇反推：地址省略区县，仅乡镇 -> 引擎②命中层级3
    r = run("23118319900101001X", "黑龙江省联兴乡双兴村北兴西南街屯121号")
    assert r.district_code, "乡镇反推失败"
    assert r.district_name == "嫩江市"
    print(f"  乡镇反推→引擎②命中 ✓  ({r.province_name}/{r.city_name}/{r.district_name})")
    print()


def part_c_county_city_omit(db):
    print("=== C. 县级市省略层级2的市（用户强调项）===")
    cases = [
        "黑龙江省嫩江市铁西街二委4组长城商场综合楼1单元201室",
        "黑龙江省抚远市乌苏镇抓吉赫哲族村二组",
        "黑龙江省铁力市桃山镇人和社区五组",
        "辽宁省普兰店市丰荣街道办事处谷泡子村满店屯2-22号",
        "河北省三河市燕郊开发区大街北欧小镇16号楼B单元1106室",
    ]
    for a in cases:
        r = engine2.engine2_parse(db, a)
        assert r.district_code, f"反查失败: {a}"
        assert r.city_code, f"父级市未推导: {a}"
        print(f"  ✓ {r.district}({r.district_code}) / 市={r.city}({r.city_code}) / 省={r.province}")
    print()


def part_d_autonomous_region_short_form(db):
    print("=== D. 自治区简称省前缀识别（宁夏/内蒙古等）===")

    # 宁夏：地址省略“回族自治区”且省略地级市，应正确推导固原市/彭阳县
    r = engine2.engine2_parse(db, "宁夏彭阳县草庙乡周庄村前岔队348号")
    assert r.district_code == "640425000000", r
    assert r.city_code == "640400000000", r
    assert r.province_code == "640000000000", r
    print(f"  ✓ 宁夏/彭阳县 -> {r.province}/{r.city}/{r.district}({r.district_code})")

    # 内蒙古：简称应展开，避免“市”正则吃进省前缀
    r = engine2.engine2_parse(db, "内蒙古呼伦贝尔市莫力达瓦达斡尔族自治旗红彦镇新多村349室")
    assert r.district_code == "150722000000", r
    assert r.city_code == "150700000000", r
    assert r.province_code == "150000000000", r
    print(f"  ✓ 内蒙古/呼伦贝尔/莫力达瓦旗 -> {r.province}/{r.city}/{r.district}({r.district_code})")

    # 决策器：宁夏证件 + 宁夏地址 -> 一致 -> 正确
    r = decider.dual_engine_decide(
        db,
        engine1.engine1_match(db, "640425200908272036"),
        engine2.engine2_parse(db, "宁夏彭阳县草庙乡周庄村前岔队348号"),
    )
    assert r.match_status == "正确", r.match_status
    print(f"  ✓ 宁夏证件+地址 -> 正确 ({r.province_name}/{r.city_name}/{r.district_name})")
    print()


if __name__ == "__main__":
    db = DBManager()
    part_a_engine2(db)
    part_b_decider(db)
    part_c_county_city_omit(db)
    part_d_autonomous_region_short_form(db)
    print("全部 v2 验证通过 ✓")
    db.close()
