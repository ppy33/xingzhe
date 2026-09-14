# -*- coding: utf-8 -*-
"""M4 离线自测：用合成矩阵验证 TSPTW 求解与回写逻辑（不调高德、不调 LLM）

覆盖：
1. 「一字排开」的点被故意打乱，看求解器能否找回最短路 + 里程/耗时下降
2. 只有晚上才开放的点，求解器应把它排到最后（时间窗生效）
3. 时间窗太紧导致无解时，应降级保留原顺序而不是抛异常
4. 营业时间解析、城市猜测、耗时格式化等小工具
"""
import asyncio
import sys

sys.path.insert(0, ".")

from agents.optimizer import (
    DAY_END_MIN,
    DAY_START_MIN,
    apply_cached_matrix,
    guess_city,
    min_to_hhmm,
    optimize_day,
    parse_open_window,
)
from agents.schemas import Day, DayItem, PlaceRef


def make_day(names, locs, windows=None, durations=None, title="测试日") -> Day:
    items = []
    for i, (n, loc) in enumerate(zip(names, locs)):
        items.append(
            DayItem(
                time=min_to_hhmm(DAY_START_MIN + i * 120),
                title=n,
                place=PlaceRef(
                    name=n,
                    category="sight",
                    location=loc,
                    open_time=(windows or {}).get(n, "08:30-22:00"),
                ),
                duration_min=(durations or {}).get(n, 60),
            )
        )
    return Day(date="2026-09-15", weekday="二", weather="晴", theme="测试", items=items)


def line_matrix(km_step=1.0, min_per_km=6.0, n=5):
    """n 个点一字排开：x = 0, km_step, 2*km_step ...，耗时与距离成正比。"""
    mins = [[0.0] * n for _ in range(n)]
    kms = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(n):
            d = abs(i - j) * km_step
            kms[i][j] = d
            mins[i][j] = d * min_per_km
    return mins, kms


async def test_line_reorder():
    print("=" * 60)
    print("场景 1：一字排开的 5 个点，初版顺序被打乱")
    names = ["A", "B", "C", "D", "E"]
    locs = [f"{116.0 + i * 0.01},39.0" for i in range(5)]
    day = make_day(names, locs)
    # 故意打乱成 A→E→B→D→C
    day.items = [day.items[i] for i in [0, 4, 1, 3, 2]]
    mins, kms = line_matrix()
    apply_cached_matrix(locs, mins, kms)

    stat = await optimize_day(day, allow_api=False)
    print(f"applied={stat['applied']} reordered={stat['reordered']}")
    print(f"优化前顺序: {stat['order_before']}  往返耗时 {stat['before_min']} 分钟 / {stat['before_km']} km")
    print(f"优化后顺序: {stat['order_after']}  往返耗时 {stat['after_min']} 分钟 / {stat['after_km']} km")
    print(f"耗时下降 {stat['saving_pct']}%  求解 {stat['solve_ms']}ms")
    print(f"回写后的时间轴: {[(it.place.name, it.time) for it in day.items]}")
    print(f"交通段示例: {day.items[0].transport_to_next}")

    assert stat["applied"], "应当应用优化"
    assert stat["order_after"] == ["A", "B", "C", "D", "E"], f"应恢复一字顺序，实际 {stat['order_after']}"
    assert stat["saving_pct"] > 50, "耗时应显著下降"
    assert stat["solve_ms"] < 3500, f"求解应受时间上限约束，实际 {stat['solve_ms']}ms"
    assert "violations" not in stat, f"不应有营业时间越界：{stat.get('violations')}"
    print("✓ 通过")


async def test_time_window():
    print("=" * 60)
    print("场景 2：E 只有 20:00 后开放，应被排到最后")
    names = ["A", "B", "C", "D", "E"]
    locs = [f"{116.0 + i * 0.01},39.0" for i in range(5)]
    windows = {"E": "20:00-22:00", "A": "08:30-22:00", "B": "08:30-22:00", "C": "08:30-22:00", "D": "08:30-22:00"}
    durations = {n: 30 for n in names}
    day = make_day(names, locs, windows, durations)
    # 初版把 E 排在第二个
    day.items = [day.items[i] for i in [0, 4, 1, 2, 3]]
    mins, kms = line_matrix()
    apply_cached_matrix(locs, mins, kms)

    stat = await optimize_day(day, allow_api=False)
    print(f"优化后顺序: {stat['order_after']}")
    print(f"时间轴: {[(it.place.name, it.time) for it in day.items]}")
    assert stat["applied"], "应当应用优化"
    assert stat["order_after"][-1] == "E", f"E 应被排到最后，实际 {stat['order_after']}"
    assert "violations" not in stat, f"不应有营业时间越界：{stat.get('violations')}"
    print("✓ 通过")


async def test_infeasible_fallback():
    print("=" * 60)
    print("场景 3：起点正常但其余点时间窗互斥（都只开 08:30-09:00 且各需 60 分钟）→ 应降级保原顺序")
    names = ["A", "B", "C", "D"]
    locs = [f"{116.0 + i * 0.01},39.0" for i in range(4)]
    windows = {"A": "08:30-22:00", "B": "08:30-09:00", "C": "08:30-09:00", "D": "08:30-09:00"}
    durations = {n: 60 for n in names}
    day = make_day(names, locs, windows, durations)
    before = [it.place.name for it in day.items]
    mins, kms = line_matrix(n=4)
    apply_cached_matrix(locs, mins, kms)

    stat = await optimize_day(day, allow_api=False)
    after = [it.place.name for it in day.items]
    print(f"applied={stat['applied']} reason={stat.get('reason', '-')}")
    print(f"顺序是否保持: {before} → {after}")
    assert not stat["applied"], "无解时不应标记 applied"
    assert "无可行解" in stat.get("reason", ""), f"应走无可行解分支，实际：{stat.get('reason')}"
    assert before == after, "无解时必须保留原顺序"
    print("✓ 通过")


async def test_too_few_points():
    print("=" * 60)
    print("场景 4：地点不足 4 个 → 跳过优化")
    names = ["A", "B", "C"]
    locs = [f"{116.0 + i * 0.01},39.0" for i in range(3)]
    day = make_day(names, locs)
    mins, kms = line_matrix(n=3)
    apply_cached_matrix(locs, mins, kms)
    stat = await optimize_day(day, allow_api=False)
    print(f"applied={stat['applied']} reason={stat.get('reason', '-')}")
    assert not stat["applied"]
    print("✓ 通过")


async def test_loose_fallback():
    print("=" * 60)
    print("场景 3b：停留时长偏大导致严格建模无解 → 应降级到 loose / compressed 并给出顺序")
    names = ["A", "B", "C", "D", "E"]
    locs = [f"{116.0 + i * 0.01},39.0" for i in range(5)]
    # 全部 09:00-18:00 开放，但每个点要停 150 分钟：严格建模（到达+停留≤关门）必然无解
    windows = {n: "09:00-18:00" for n in names}
    durations = {n: 150 for n in names}
    day = make_day(names, locs, windows, durations)
    day.items = [day.items[i] for i in [0, 4, 2, 1, 3]]
    mins, kms = line_matrix()
    apply_cached_matrix(locs, mins, kms)

    stat = await optimize_day(day, allow_api=False)
    print(f"applied={stat['applied']} mode={stat.get('mode')} reason={stat.get('reason', '-')}")
    print(f"优化后顺序: {stat.get('order_after')}")
    if stat["applied"]:
        print(f"时间轴: {[(it.place.name, it.time, it.duration_min) for it in day.items]}")
        assert stat["mode"] in ("strict", "loose", "compressed")
        if stat["mode"] == "compressed":
            # 压缩模式不得改写行程里的停留时长
            assert all(it.duration_min == 150 for it in day.items), "compressed 模式不应回写压缩后的时长"
    print("✓ 通过")


async def test_repair_coordinates():
    print("=" * 60)
    print("场景 6：坐标体检（有权威参照时纠错，无参照时不乱动）")
    from agents.optimizer import parse_loc, repair_coordinates
    from agents.schemas import DayItem, PlaceRef

    def build_items():
        return [
            DayItem(time="09:00", title="武侯祠", place=PlaceRef(name="成都武侯祠博物馆", location="104.218621,31.001439")),
            DayItem(time="11:00", title="锦里", place=PlaceRef(name="锦里古街", location="114.917890,37.634344")),  # 河北邢台（编的）
            DayItem(time="13:00", title="宽窄巷子", place=PlaceRef(name="宽窄巷子景区", location="")),  # 空
            DayItem(time="15:00", title="人民公园", place=PlaceRef(name="人民公园", location="104.142684,29.992593")),
            DayItem(time="17:00", title="春熙路", place=PlaceRef(name="春熙路步行街", location="104.079,30.656")),
        ]

    # --- A. 有权威坐标表（来自工具轨迹）---
    poi_index = {
        "成都武侯祠博物馆": "104.047992,30.646168",
        "锦里古街": "104.049828,30.645994",
        "宽窄巷子景区": "104.053307,30.663869",
        "人民公园": "104.057641,30.656990",
        "春熙路步行街": "104.077774,30.655544",
    }
    items = build_items()
    info = await repair_coordinates(items, poi_index=poi_index)
    print(f"[有权威参照] 离群点: {info['outliers']}")
    print(f"[有权威参照] 来自工具: {info['from_tools']}｜重查: {info['regenerated']}｜城市 {info['city']}")
    assert info["reliable_reference"] is True
    assert "锦里古街" in info["outliers"], f"应揪出锦里，实际 {info['outliers']}"
    assert set(info["from_tools"]) >= {"锦里古街", "宽窄巷子景区"}, "应直接用工具轨迹坐标覆盖"
    assert not info["dropped"]
    for it in items:
        assert parse_loc(it.place.location), f"{it.place.name} 坐标不可用"
        assert it.place.location.startswith("104."), f"{it.place.name} 不在成都：{it.place.location}"

    # --- B. 没有权威坐标表 → 只给「空坐标」补，不拿编造的坐标互相投票 ---
    async def fake_geocoder(name, city=""):
        return {"宽窄巷子景区": "104.055,30.663"}.get(name)

    items2 = build_items()
    info2 = await repair_coordinates(items2, city="成都", geocoder=fake_geocoder)
    print(f"[无参照] reliable={info2['reliable_reference']} 重查: {info2['regenerated']} 丢弃: {info2['dropped']}")
    assert info2["reliable_reference"] is False
    assert info2["regenerated"] == ["宽窄巷子景区"], "只应补空坐标"
    assert items2[1].place.location == "114.917890,37.634344", "没有权威参照时不应擅自改动已有坐标"
    assert parse_loc("") is None and parse_loc("0,0") is None and parse_loc("abc") is None
    assert parse_loc("116.4,39.9") == (116.4, 39.9)
    print("✓ 通过")


async def test_poi_index():
    print("=" * 60)
    print("场景 7：从工具轨迹捞真实坐标（含被截断的 JSON）")
    from agents.optimizer import lookup_poi_index, poi_index_from_steps

    full = (
        '{"count": 2, "city": "成都", "pois": ['
        '{"id": "B001", "name": "成都武侯祠博物馆", "type": "博物馆", "address": "武侯祠大街231号", '
        '"location": "104.047992,30.646168", "rating": "4.8", "open_time": "08:30-18:30"}, '
        '{"id": "B0FF", "name": "锦里古街", "type": "风景名胜", "address": "武侯祠大街231号附1号", '
        '"location": "104.049828,30.645994", "rating": "4.8"}]}'
    )
    # 模拟被 [:2000] 截断：JSON 已经不完整，json.loads 必然失败
    truncated = (
        '{"count": 2, "city": "成都", "pois": ['
        '{"id": "B001", "name": "宽窄巷子景区", "type": "特色商业街", "address": "少城街道金河路口", '
        '"location": "104.053307,30.663869", "rating": "4.8", "open_time": "24小时营业"}, '
        '{"id": "B002", "name": "人民公园", "type": "国家级景点", "address": "小南街8号", '
        '"location": "104.057641,30.656990", "rating": "4.8"'
    )
    steps = [
        {"type": "tool_call", "tool": "poi_search", "args": {}},
        {"type": "tool_result", "tool": "poi_search", "content": full},
        {"type": "tool_result", "tool": "poi_search", "content": truncated},
        {"type": "tool_result", "tool": "weather_forecast", "content": '{"days": [], "name": "x", "location": "1,1"}'},
    ]
    idx = poi_index_from_steps(steps)
    print(f"解析出 {len(idx)} 个坐标: {list(idx.keys())}")
    assert len(idx) == 4, f"应解析出 4 个（含截断那段），实际 {len(idx)}: {idx}"
    assert idx["成都武侯祠博物馆"] == "104.047992,30.646168"
    assert idx["人民公园"] == "104.057641,30.656990", "截断的 JSON 也要能抠出来"
    assert "x" not in idx, "非 POI 类工具不应被当成坐标来源"
    # 名字对不齐时的包含匹配
    assert lookup_poi_index("武侯祠博物馆", idx) == "104.047992,30.646168"
    assert lookup_poi_index("锦里", idx) == "104.049828,30.645994"
    assert lookup_poi_index("不存在的景点", idx) is None
    print("✓ 通过")


def test_helpers():
    print("=" * 60)
    print("场景 5：工具函数")
    assert parse_open_window("08:30-17:00") == (510, 1020)
    assert parse_open_window("周一至周日 09:00~18:00") == (540, 1080)
    assert parse_open_window("全天开放") == (0, DAY_END_MIN)
    assert parse_open_window("") is None
    assert parse_open_window("即将开业") is None
    assert guess_city(["成都市武侯区武侯祠大街"]) == "成都"
    assert guess_city(["北京市东城区"]) == "北京"
    assert guess_city([""]) == ""
    assert min_to_hhmm(510) == "08:30"
    assert min_to_hhmm(1259) == "20:59"
    print("营业时间解析 / 城市猜测 / 时间格式化 全部通过")
    print("✓ 通过")


async def main():
    await test_line_reorder()
    await test_time_window()
    await test_infeasible_fallback()
    await test_loose_fallback()
    await test_too_few_points()
    await test_repair_coordinates()
    await test_poi_index()
    test_helpers()
    print("=" * 60)
    print("M4 离线自测全部通过")


if __name__ == "__main__":
    asyncio.run(main())
