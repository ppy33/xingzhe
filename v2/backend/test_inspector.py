# -*- coding: utf-8 -*-
"""M5 离线自测：体检规则 + 变更 diff + SQLite 存储（不调高德、不调 LLM）

覆盖：
1. 闭馆规则：周一 + 知名场馆（high）/ 泛博物馆（medium）/ 明确照常开放的（无误报）
2. 天气规则：雨天 + 户外 → 报；雨天 + 室内 → 不报；晴天 + 户外 → 不报；暴雨 → high
3. 营业时间：开门前安排 / 停留超关门 → 报
4. diff：揭示被对调/替换的项，新增地点带 added 标记
5. 存储：行程与事件的写入/读回/覆盖
"""
import os
import sys
import tempfile

sys.path.insert(0, ".")

from agents.inspector import (
    classify_scene,
    diff_plans,
    inspect_trip,
    is_heavy_rain,
    is_rainy,
    monday_closed_risk,
)
from agents.schemas import Day, DayItem, PlaceRef, TripPlan

MONDAY = "2026-09-14"  # 周一
TUESDAY = "2026-09-15"


def place(name, open_time="", category="sight"):
    return PlaceRef(name=name, category=category, open_time=open_time)


def make_plan(day_date=MONDAY, weekday="一", items=None, weather="晴"):
    items = items or []
    return TripPlan(
        title="测试行程",
        overview="",
        days=[Day(date=day_date, weekday=weekday, weather=weather, theme="测试", items=items)],
        transportation="",
        budget=[],
        tips=[],
    )


def item(name, time="09:00", dur=120, open_time=""):
    return DayItem(
        time=time,
        title=name,
        place=place(name, open_time=open_time),
        duration_min=dur,
        notes="",
    )


def test_scene_classify():
    print("=" * 60)
    print("场景 1：室内/户外分类")
    cases = {
        "成都博物馆": "indoor",
        "上海科技馆": "indoor",
        "成都远洋太古里": "indoor",
        "蜀大侠火锅": "indoor",
        "人民公园": "outdoor",
        "宽窄巷子": "outdoor",
        "都江堰景区": "outdoor",
        "青城山": "outdoor",
        "某某打卡点": "unknown",
    }
    for name, want in cases.items():
        got = classify_scene(name)
        print(f"  {name} → {got}")
        assert got == want, f"{name} 应判为 {want}，实际 {got}"
    print("✓ 通过")


def test_closure_rules():
    print("=" * 60)
    print("场景 2：闭馆规则")
    risky, sev = monday_closed_risk("故宫博物院")
    assert risky and sev == "high", f"故宫周一应 high，实际 {risky}/{sev}"
    risky, sev = monday_closed_risk("上海博物馆")
    assert risky and sev == "high"
    risky, sev = monday_closed_risk("某某市历史博物馆")
    assert risky and sev == "medium", "泛博物馆应为 medium 提示"
    risky, _ = monday_closed_risk("成都武侯祠博物馆")
    assert not risky, "武侯祠周一照常开放，不应报"
    risky, _ = monday_closed_risk("宽窄巷子")
    assert not risky
    risky, _ = monday_closed_risk("蜀大侠火锅")
    assert not risky

    # 周一：应该报；周二：不报
    plan_mon = make_plan(MONDAY, "一", [item("成都博物馆", open_time="09:00-17:00")])
    insp = inspect_trip(plan_mon)
    kinds = [i.kind for i in insp.issues]
    print(f"  周一成都博物馆 → {kinds}")
    assert "closure" in kinds, "周一安排博物馆应报闭馆"

    plan_tue = make_plan(TUESDAY, "二", [item("成都博物馆", open_time="09:00-17:00")])
    insp2 = inspect_trip(plan_tue)
    assert not any(i.kind == "closure" for i in insp2.issues), "周二不应报闭馆"
    print("✓ 通过")


def test_weather_rules():
    print("=" * 60)
    print("场景 3：天气规则")
    assert is_rainy("中雨/小雨") and not is_rainy("晴/多云")
    assert is_heavy_rain("大雨/暴雨") and not is_heavy_rain("小雨")

    plan = make_plan(
        MONDAY,
        "一",
        [
            item("人民公园", "09:00", 120),
            item("成都博物馆", "14:00", 120, open_time="09:00-17:00"),
        ],
    )
    insp = inspect_trip(plan, weather={MONDAY: "中雨/小雨"})
    w = [i for i in insp.issues if i.kind == "weather"]
    print(f"  雨天：户外 人民公园 → {len(w)} 条天气问题；室内 成都博物馆 不报")
    assert len(w) == 1 and w[0].item_index == 0, f"应只报户外项，实际 {[(i.item_index, i.reason) for i in w]}"
    assert w[0].severity == "medium"

    # 暴雨 → high
    insp_heavy = inspect_trip(plan, weather={MONDAY: "暴雨/大雨"})
    w2 = [i for i in insp_heavy.issues if i.kind == "weather"]
    assert w2 and w2[0].severity == "high", "暴雨应为 high"

    # 晴天 → 不报天气
    insp_sunny = inspect_trip(plan, weather={MONDAY: "晴/多云"})
    assert not [i for i in insp_sunny.issues if i.kind == "weather"], "晴天不该报天气问题"

    # 无天气数据 → 不报天气（避免瞎猜）
    insp_none = inspect_trip(plan, weather={})
    assert not [i for i in insp_none.issues if i.kind == "weather"], "无天气数据不该报天气问题"
    print("✓ 通过")


def test_hours_rules():
    print("=" * 60)
    print("场景 4：营业时间越界")
    plan = make_plan(
        TUESDAY,
        "二",
        [
            item("某展馆", "08:00", 120, open_time="09:00-17:00"),  # 开门前
            item("某寺庙", "16:00", 180, open_time="09:00-17:00"),  # 停留超关门
            item("全天街区", "20:00", 90, open_time="全天"),  # 不报
        ],
    )
    insp = inspect_trip(plan)
    hours = [i for i in insp.issues if i.kind == "hours"]
    idxs = sorted(i.item_index for i in hours)
    print(f"  报出营业时间问题：{[(i.item_index, i.reason[:40]) for i in hours]}")
    assert idxs == [0, 1], f"应报第 1、2 项，实际 {idxs}"
    print("✓ 通过")


def test_diff():
    print("=" * 60)
    print("场景 5：变更 diff（含新增地点标记）")
    before = make_plan(
        MONDAY,
        "一",
        [
            item("人民公园", "09:00", 120),
            item("成都博物馆", "14:00", 120, open_time="09:00-17:00"),
        ],
    )
    after = make_plan(
        MONDAY,
        "一",
        [
            item("成都博物馆", "09:00", 120, open_time="09:00-17:00"),
            DayItem(
                time="14:00",
                title="金沙遗址博物馆",
                place=place("金沙遗址博物馆", "08:00-18:00"),
                duration_min=150,
            ),
        ],
    )
    insp = inspect_trip(before, weather={MONDAY: "中雨/小雨"})
    changes = diff_plans(before, after, insp)
    for c in changes:
        print(f"  {c.day} #{c.item_index}: {c.before} → {c.after} | 新增={c.added} | {c.reason[:30]}")
    assert len(changes) == 2, f"应有两处变更，实际 {len(changes)}"
    assert changes[0].before.startswith("09:00 人民公园")
    assert changes[0].after.startswith("09:00 成都博物馆")
    assert changes[1].added is True, "金沙遗址不在初版里，应标为新增"
    assert changes[0].reason, "变更应带上原因"
    print("✓ 通过")


def test_storage():
    print("=" * 60)
    print("场景 6：SQLite 存储往返")
    import storage

    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "test.db")
        storage.init_db(path)
        plan = make_plan(MONDAY, "一", [item("人民公园")]).model_dump()
        storage.save_trip("t1", plan, answer="# 行程", title="测试行程")
        got = storage.get_trip("t1")
        assert got and got["plan"]["title"] == "测试行程", "应能按 thread_id 读回"
        assert storage.get_trip("不存在") is None

        # 覆盖写
        plan["title"] = "改过的行程"
        storage.save_trip("t1", plan, answer="# 新版")
        assert storage.get_trip("t1")["plan"]["title"] == "改过的行程"
        assert len(storage.list_trips()) == 1, "同一 thread_id 应只有一条"

        storage.add_event("t1", "check", {"issues": 2})
        storage.add_event("t1", "fix", {"changes": 2})
        events = storage.list_events("t1")
        assert [e["kind"] for e in events] == ["fix", "check"], "事件应按时间倒序"
        assert events[0]["payload"]["changes"] == 2
        storage.close_db()
    print("✓ 通过")


def main():
    test_scene_classify()
    test_closure_rules()
    test_weather_rules()
    test_hours_rules()
    test_diff()
    test_storage()
    print("=" * 60)
    print("M5 离线自测全部通过")


if __name__ == "__main__":
    main()
