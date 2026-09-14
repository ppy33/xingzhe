"""M5 主动感知：行程体检（inspect）+ 自动修复（fix）+ 变更明细（diff）

和 M3 的 Critic 有什么不同？
- Critic 是**生成时**的静态审查（只看文本，不查外部世界，一次性）
- Inspector 是**生成后**的持续巡检：会重新查天气、查闭馆规则、查营业时间，
  发现问题就自动改行程并把「改了什么」明确报给用户——这是「行程会被盯着」的那部分

三类检查（都是程序化规则，不靠 LLM 判断，避免幻觉）：
1. `closure` 闭馆：已知周一闭馆的场馆（硬规则）+ 博物馆类「多数周一闭馆」（软提示）
2. `weather` 天气：当天有雨却排了户外项（按名称特征分室内/户外）
3. `hours`  营业时间：安排时间点 + 停留时长超出 open_time
"""
from __future__ import annotations

import json
from datetime import date as date_cls
from datetime import datetime
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from agents.optimizer import (
    guess_city,
    hhmm_to_min,
    min_to_hhmm,
    parse_open_window,
    regeo_city,
)
from agents.schemas import PlanChange, TripInspection, TripIssue, TripPlan
from config import build_llm
from tools import poi_search
from tools.amap import _amap_get

# --------------------------------------------------------------------------
# 规则表
# --------------------------------------------------------------------------
# 明确「周一闭馆」的知名场馆（硬规则，命中即 high）
KNOWN_MONDAY_CLOSED = (
    "故宫",
    "中国国家博物馆",
    "国家博物馆",
    "首都博物馆",
    "上海博物馆",
    "上海科技馆",
    "中国美术馆",
    "陕西历史博物馆",
    "秦始皇帝陵博物院",
    "兵马俑",
    "成都博物馆",
    "四川博物院",
    "苏州博物馆",
    "南京博物院",
    "浙江省博物馆",
    "湖北省博物馆",
    "湖南省博物馆",
    "河南博物院",
    "山西博物院",
    "广东省博物馆",
    "辽宁省博物馆",
    "天津博物馆",
    "重庆中国三峡博物馆",
    "敦煌研究院",
)

# 名字里带这些词的场馆，周一大概率闭馆（软规则，medium）
MUSEUM_KEYWORDS = ("博物馆", "博物院", "美术馆", "科技馆", "纪念馆", "展览馆", "艺术馆", "陈列馆")

# 明确「全年无休 / 周一照开」的，避免误报
MONDAY_OPEN_HINTS = ("武侯祠", "杜甫草堂", "金沙遗址", "三星堆", "锦里", "宽窄巷子", "太古里")

# 天气
RAIN_HINTS = ("雨", "降水", "雷阵雨", "阵雨")
HEAVY_RAIN_HINTS = ("大雨", "暴雨", "雷阵雨", "强降水", "强降雨")

# 室内优先（必须先于户外判断，否则「博物馆步行街」之类会被判成户外）
INDOOR_HINTS = (
    "博物馆", "博物院", "美术馆", "科技馆", "纪念馆", "展览", "陈列", "室内", "水族馆", "海洋馆",
    "图书馆", "书店", "剧院", "音乐厅", "影院", "剧场", "商场", "购物中心", "百货", "奥特莱斯",
    "IFS", "太古里", "来福士", "万象城", "大悦城", "SKP", "恒隆", "银泰", "茶馆", "咖啡",
    "美食街", "小吃街", "火锅", "餐厅", "饭", "菜", "食堂", "酒店", "民宿", "客栈",
)
# 户外特征
OUTDOOR_HINTS = (
    "公园", "广场", "古镇", "古街", "步行街", "街区", "街", "巷", "山", "峰", "湖", "江", "河",
    "海", "滩", "沙滩", "植物园", "动物园", "遗址", "长城", "塔", "桥", "湿地", "栈道", "码头",
    "游船", "泛舟", "漂流", "骑行", "徒步", "登山", "露营", "滑雪", "温泉", "观光",
)


def classify_scene(name: str) -> str:
    """把一个地点名分类成 indoor / outdoor / unknown。"""
    text = (name or "").strip()
    if not text:
        return "unknown"
    for kw in INDOOR_HINTS:
        if kw in text:
            return "indoor"
    for kw in OUTDOOR_HINTS:
        if kw in text:
            return "outdoor"
    return "unknown"


def is_rainy(weather: str) -> bool:
    return any(k in (weather or "") for k in RAIN_HINTS)


def is_heavy_rain(weather: str) -> bool:
    return any(k in (weather or "") for k in HEAVY_RAIN_HINTS)


def monday_closed_risk(name: str) -> tuple[bool, str]:
    """判断某地点周一是否大概率闭馆，返回 (有风险?, 严重度)。"""
    text = (name or "").strip()
    if not text:
        return False, "low"
    if any(h in text for h in MONDAY_OPEN_HINTS):
        return False, "low"
    if any(h in text for h in KNOWN_MONDAY_CLOSED):
        return True, "high"
    if any(k in text for k in MUSEUM_KEYWORDS):
        return True, "medium"
    return False, "low"


def _weekday_cn(date_str: str) -> str:
    try:
        y, m, d = (int(x) for x in date_str.split("-")[:3])
        return "一二三四五六日"[date_cls(y, m, d).weekday()]
    except Exception:
        return ""


# --------------------------------------------------------------------------
# 天气
# --------------------------------------------------------------------------
async def fetch_weather(city: str, days: int = 4) -> dict[str, str]:
    """取未来几天天气，返回 {日期: '白天/夜间'}。失败返回空 dict（不阻塞体检）。"""
    if not city:
        return {}
    try:
        data = await _amap_get(
            "/v3/weather/weatherInfo",
            {"city": city, "extensions": "all"},
        )
    except Exception:
        return {}
    out: dict[str, str] = {}
    for fc in data.get("forecasts") or []:
        for cast in fc.get("casts") or []:
            d = cast.get("date") or ""
            day_w = cast.get("dayweather") or ""
            night_w = cast.get("nightweather") or ""
            if not d:
                continue
            out[d] = f"{day_w}/{night_w}" if night_w and night_w != day_w else day_w
    return out


# --------------------------------------------------------------------------
# 体检
# --------------------------------------------------------------------------
def inspect_trip(
    plan: TripPlan,
    weather: dict[str, str] | None = None,
    checked_at: str | None = None,
) -> TripInspection:
    """对行程做程序化体检。weather 为空时只做闭馆与营业时间检查。"""
    weather = weather or {}
    issues: list[TripIssue] = []

    for day in plan.days:
        wd = day.weekday or _weekday_cn(day.date)
        is_monday = wd == "一"
        wx = weather.get(day.date, "") or day.weather
        rainy = is_rainy(wx)
        heavy = is_heavy_rain(wx)

        for idx, item in enumerate(day.items):
            name = (item.place.name if item.place else "") or item.title or ""

            # 1) 闭馆
            if is_monday:
                risky, sev = monday_closed_risk(name)
                if risky:
                    hard = any(h in name for h in KNOWN_MONDAY_CLOSED)
                    issues.append(
                        TripIssue(
                            day=day.date,
                            item_index=idx,
                            kind="closure",
                            severity=sev,
                            reason=(
                                f"{day.date}（周一）安排了「{name}」，"
                                + ("该场馆周一固定闭馆。" if hard else "多数博物馆/展馆周一闭馆，需核实。")
                            ),
                            suggestion=(
                                "把这一项挪到不闭馆的那天，与该天等价的室内项目对调；"
                                "若当天无法调整，改为全天开放的同类景点"
                            ),
                        )
                    )

            # 2) 天气冲突（只在有天气数据时判断）
            if rainy and wx:
                scene = classify_scene(name)
                if scene == "outdoor":
                    issues.append(
                        TripIssue(
                            day=day.date,
                            item_index=idx,
                            kind="weather",
                            severity="high" if heavy else "medium",
                            reason=f"{day.date}天气预报「{wx}」，却安排了户外项目「{name}」。",
                            suggestion=(
                                "换成同城室内项目（博物馆/展馆/商场等），"
                                "或与其它天的室内项对调；不要新增没核实过的地点"
                            ),
                        )
                    )

            # 3) 营业时间越界
            if item.place and item.place.open_time:
                win = parse_open_window(item.place.open_time)
                start = hhmm_to_min(item.time)
                if win and start is not None:
                    dur = int(item.duration_min or 0)
                    if start < win[0]:
                        issues.append(
                            TripIssue(
                                day=day.date,
                                item_index=idx,
                                kind="hours",
                                severity="medium",
                                reason=(
                                    f"「{name}」安排 {item.time} 开始，但营业时间是 "
                                    f"{item.place.open_time}（{min_to_hhmm(win[0])} 才开门）。"
                                ),
                                suggestion="把开始时间推到开门之后，或与当天其它项对调顺序",
                            )
                        )
                    elif dur and start + dur > win[1]:
                        issues.append(
                            TripIssue(
                                day=day.date,
                                item_index=idx,
                                kind="hours",
                                severity="medium",
                                reason=(
                                    f"「{name}」{item.time} 开始、停留 {dur} 分钟，"
                                    f"会超过关门时间 {min_to_hhmm(win[1])}（{item.place.open_time}）。"
                                ),
                                suggestion="缩短停留时长，或把它挪到当天更早的时段",
                            )
                        )

    counts = {"high": 0, "medium": 0, "low": 0}
    for i in issues:
        counts[i.severity] = counts.get(i.severity, 0) + 1
    if not issues:
        summary = "行程体检通过：未来几天天气与安排相容，未发现闭馆或营业时间冲突。"
    else:
        parts = [f"{counts['high']} 个高风险" if counts["high"] else "", f"{counts['medium']} 个中风险" if counts["medium"] else ""]
        summary = "发现 " + str(len(issues)) + " 个问题（" + "、".join(p for p in parts if p) + "）。"
    return TripInspection(
        issues=issues,
        weather=weather,
        checked_at=checked_at or datetime.now().astimezone().isoformat(timespec="seconds"),
        summary=summary,
    )


# --------------------------------------------------------------------------
# 自动修复
# --------------------------------------------------------------------------
FIX_SYSTEM_PROMPT = """你是「行者」系统的行程体检修复官。用户已有一份结构化行程（TripPlan），
体检程序发现了若干问题（闭馆 / 雨天户外 / 营业时间越界）。你的任务是**把这些问题改掉**，
输出修订后的完整 TripPlan。

## 修复手段（按优先级）
1. **对调**：把有问题的项与**其它天**里等价的项目对调（例如雨天日的户外项 ↔ 晴天日的室内项）
2. **换序**：同一天内调整顺序，解决开门时间/停留时长越界
3. **替换**：用「候选室内地点」里的地点替换户外项（候选已由高德检索核实，只能从这里选）
4. **降级**：实在无法调整时，把该项改成"可选/备选"，在 notes 里说明

## 硬约束
- **不许编造地点名**：所有地点必须来自原行程或给定的候选列表
- **只动有问题的天**：没被体检点名的天，items 原样保留（顺序、时间、备注都不变）
- **保持结构**：title / overview / transportation / budget / tips 保留，必要时在 tips 里补一条说明
- 用候选列表里的地点时，category 按候选给的类型填，notes 里注明「替代 <原地点>」
- 输出必须调用 `TripPlan` 工具返回结构化对象，不要输出自然语言
"""


async def infer_city(plan: TripPlan, fallback: str = "") -> str:
    """推断行程所在城市，用于「换室内项」时的 POI 检索。

    顺序：地址文本 → 逆地理编码（拿第一个有坐标的地点反查）。
    为什么不能只看地址：Planner 常常不填 address，实测会得到空字符串，
    而坐标在 M4 已经过体检、是真实值，反查城市最可靠。
    """
    city = guess_city([it.place.address for d in plan.days for it in d.items if it.place])
    if city:
        return city
    for d in plan.days:
        for it in d.items:
            loc = (it.place.location if it.place else "") or ""
            if loc.strip():
                found = await regeo_city(loc)
                if found:
                    return found
    return fallback


def build_fixer(model: str | None = None, temperature: float = 0.2):
    """构造修复官：主模型 + TripPlan 结构化输出。"""
    llm = build_llm(model=model, temperature=temperature, timeout=120)
    return llm.with_structured_output(TripPlan, method="function_calling")


async def fetch_indoor_candidates(city: str, limit: int = 6) -> list[dict[str, Any]]:
    """用高德检索「室内型」替代候选（供修复官在雨天替换户外项用）。"""
    if not city:
        return []
    out: list[dict[str, Any]] = []
    for kw in ("博物馆", "美术馆", "展览馆"):
        try:
            raw = await poi_search.ainvoke(
                {"keywords": kw, "city": city, "limit": max(2, limit // 2)}
            )
            data = json.loads(raw)
        except Exception:
            continue
        for p in data.get("pois") or []:
            name = (p.get("name") or "").strip()
            if not name or any(x["name"] == name for x in out):
                continue
            out.append(
                {
                    "name": name,
                    "address": p.get("address") or "",
                    "location": p.get("location") or "",
                    "open_time": p.get("open_time") or "",
                    "rating": p.get("rating") or "",
                    "cost": p.get("cost"),
                    "category": "sight",
                }
            )
        if len(out) >= limit:
            break
    return out[:limit]


async def fix_trip(
    llm,
    plan: TripPlan,
    inspection: TripInspection,
    indoor_candidates: list[dict[str, Any]] | None = None,
    attempts: int = 3,
) -> TripPlan:
    """按体检查出的问题自动修复行程。失败时抛异常，调用方保留原行程。

    多次尝试的原因：模型偶尔会「只用自然语言回答、不调用 TripPlan 工具」，
    此时 `with_structured_output` 返回 None（M3 的 Planner 踩过同一个坑）。
    第 2 次起追加「必须调用工具」的强提醒，成功率明显提高。
    """
    if not inspection.issues:
        return plan

    issue_lines = []
    for i, it in enumerate(inspection.issues, 1):
        issue_lines.append(
            f"{i}. [{it.kind}/{it.severity}] {it.day} 第{(it.item_index or 0) + 1}项：{it.reason}"
            f"（建议：{it.suggestion}）"
        )
    weather_lines = [f"- {d}: {w}" for d, w in (inspection.weather or {}).items()]
    cand_lines = [
        f"- {c['name']}｜{c.get('address', '')}｜营业 {c.get('open_time') or '未知'}"
        f"｜评分 {c.get('rating') or '未知'}"
        for c in (indoor_candidates or [])
    ]
    # 精简输入：行程 JSON 去掉空字段，token 少一半，模型更不容易「跑题去聊天」
    plan_json = json.dumps(
        plan.model_dump(exclude_defaults=True, exclude_none=True),
        ensure_ascii=False,
        indent=1,
    )
    base = (
        "## 体检查出的问题\n"
        + "\n".join(issue_lines)
        + "\n\n## 体检时的天气\n"
        + ("\n".join(weather_lines) or "（无天气数据）")
        + "\n\n## 候选室内地点（高德检索，可选用）\n"
        + ("\n".join(cand_lines) or "（无候选，只能靠对调/换序/降级解决）")
        + "\n\n## 待修复行程（TripPlan JSON）\n"
        + plan_json
    )
    last_err = ""
    for attempt in range(max(1, attempts)):
        content = base
        if attempt:
            content += (
                "\n\n## 重要提醒\n"
                "上一次你没有调用工具。这一次**必须调用 `TripPlan` 函数**返回完整的结构化行程，"
                "不要输出任何自然语言说明。"
            )
        try:
            fixed = await llm.ainvoke(
                [SystemMessage(content=FIX_SYSTEM_PROMPT), HumanMessage(content=content)]
            )
        except Exception as exc:
            last_err = f"{type(exc).__name__}: {exc}"
            continue
        if fixed is not None:
            return fixed
        last_err = "模型没有调用 TripPlan 工具（返回了自然语言）"
    raise RuntimeError(f"修复官未返回结构化结果：{last_err}")


# --------------------------------------------------------------------------
# 变更明细
# --------------------------------------------------------------------------
def diff_plans(before: TripPlan, after: TripPlan, inspection: TripInspection | None = None) -> list[PlanChange]:
    """对比修复前后，列出逐项变更（前端据此高亮）。"""
    reason_by_day: dict[str, str] = {}
    for it in (inspection.issues if inspection else []):
        if it.day and it.day not in reason_by_day:
            reason_by_day[it.day] = it.reason

    before_names = {
        (it.place.name if it.place else it.title)
        for d in before.days
        for it in d.items
    }
    after_by_date = {d.date: d for d in after.days}
    changes: list[PlanChange] = []

    for d in before.days:
        new_day = after_by_date.get(d.date)
        if new_day is None:
            continue
        for idx, old_item in enumerate(d.items):
            if idx >= len(new_day.items):
                continue
            new_item = new_day.items[idx]
            old_name = (old_item.place.name if old_item.place else "") or old_item.title
            new_name = (new_item.place.name if new_item.place else "") or new_item.title
            if old_name == new_name and old_item.time == new_item.time:
                continue
            changes.append(
                PlanChange(
                    day=d.date,
                    item_index=idx,
                    before=f"{old_item.time} {old_name}".strip(),
                    after=f"{new_item.time} {new_name}".strip(),
                    reason=reason_by_day.get(d.date, "体检自动调整"),
                    added=bool(new_name) and new_name not in before_names,
                )
            )
    return changes


__all__ = [
    "inspect_trip",
    "fix_trip",
    "diff_plans",
    "fetch_weather",
    "fetch_indoor_candidates",
    "infer_city",
    "build_fixer",
    "classify_scene",
    "is_rainy",
    "is_heavy_rain",
    "monday_closed_risk",
    "guess_city",
]
