"""M4 运筹优化：单日行程的 VRPTW 重排（OR-Tools）

问题定义
--------
某一天已有一组 POI（Planner 产出，带坐标、营业时间、停留时长），
求「在营业时间窗内、从当天首个地点出发、把这一天走完」的最短路径。
这是单车辆的 TSP with Time Windows（TSPTW），用 OR-Tools 的 routing 求解器
（1 vehicle + Time 维度）即可覆盖。

设计取舍
--------
1. **时间矩阵走高德「距离测量」批量接口**：`/v3/distance` 一次可传多个 origin，
   对 n 个点只需 n 次调用（而非 n²-n 次 direction 接口），个人 Key 的 QPS 压力小得多。
   仍然复用 `tools.amap.py` 的 `_amap_get`，限流/退避/重试逻辑不重复实现。
2. **矩阵缓存**：进程内缓存 (origin, dest) → (分钟, 公里)，同一天多次求解/多次请求不重复打接口。
3. **只在结构化行程上优化**：依赖 `place.location` + `open_time`，所以放在 Planner 之后。
4. **求解放线程池**：OR-Tools 是 C++ 阻塞调用，必须 `asyncio.to_thread` 否则会卡住事件循环。
5. **失败/超时一律降级返回原顺序**：优化是增值功能，绝不能把整个请求拖死。
6. **只重排、不增删**：非地点项（午餐/交通段）保持原来的槽位，只置换地点项的先后，
   这样一天的「骨架」不变，只是顺序更顺路。
"""
from __future__ import annotations

import asyncio
import math
import re
import time
from typing import Any

from agents.schemas import Day, DayItem, TripPlan
from tools.amap import _amap_get, _location_from_poi

# 当天行程的默认时间轴
DAY_START_MIN = 8 * 60 + 30  # 08:30 出发
DAY_END_MIN = 22 * 60  # 22:00 前结束

# 少于这个点数就不值得优化（点数太少时"最优"没意义，还白花接口调用）
MIN_POINTS_TO_OPTIMIZE = 4

# 单次求解的时间上限（毫秒），超过就用当前最优解。
# 一天通常 4–8 个点，这个规模 2.5 秒足够收敛，不必烧满更长的预算。
# 注意：OR-Tools 的 `time_limit.FromSeconds()` 只接受整数，所以这里用毫秒。
SOLVE_TIME_LIMIT_MS = 2500

# 节点默认停留时长（分钟），Planner 没给 duration_min 时用
DEFAULT_SERVICE_MIN = 90

# 矩阵缓存：{(origin, dest): (分钟, 公里)}
_MATRIX_CACHE: dict[tuple[str, str], tuple[float, float]] = {}

# 地址里抽城市：'成都市武侯区…' → '成都'
_CITY_RE = re.compile(r"^([\u4e00-\u9fa5]{2,4})(?:市|省)")


# --------------------------------------------------------------------------
# 小工具
# --------------------------------------------------------------------------
def _round_loc(loc: str) -> str:
    """坐标字符串归一化（保留 5 位小数），作为缓存 key。"""
    try:
        lng, lat = (float(x) for x in loc.split(","))
        return f"{lng:.5f},{lat:.5f}"
    except Exception:
        return loc


def _haversine_km(a: str, b: str) -> float:
    """两点直线距离（公里）。接口失败时的兜底估算。"""
    try:
        lng1, lat1 = (float(x) for x in a.split(","))
        lng2, lat2 = (float(x) for x in b.split(","))
    except Exception:
        return 0.0
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lng2 - lng1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def _hhmm_to_min(text: str) -> int | None:
    m = re.search(r"(\d{1,2})\s*[:：]\s*(\d{2})", text or "")
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2))
    if h > 23 or mi > 59:
        return None
    return h * 60 + mi


def min_to_hhmm(minutes: int) -> str:
    minutes = max(0, min(int(minutes), 23 * 60 + 59))
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def parse_open_window(open_time: str) -> tuple[int, int] | None:
    """把营业时间原文解析成 [开门, 关门] 分钟。解析不出来返回 None。

    支持 '08:30-17:00'、'08:30~17:00'、'全天开放'、'周一至周日 09:00-18:00'。
    """
    text = (open_time or "").strip()
    if not text:
        return None
    if "全天" in text or "24小时" in text:
        return (0, DAY_END_MIN)
    times = re.findall(r"(\d{1,2}\s*[:：]\s*\d{2})", text)
    if len(times) >= 2:
        start = _hhmm_to_min(times[0])
        end = _hhmm_to_min(times[1])
        if start is not None and end is not None and end > start:
            return (start, end)
    return None


def guess_city(texts: list[str]) -> str:
    """从地址文本里猜城市名，用于坐标缺失时的 POI 检索。"""
    for t in texts:
        m = _CITY_RE.match((t or "").strip())
        if m:
            name = m.group(1)
            if name.endswith(("省", "市")):
                name = name[:-1]
            if 2 <= len(name) <= 4:
                return name
    return ""


# --------------------------------------------------------------------------
# 坐标体检与修复
# --------------------------------------------------------------------------
# 同一座城市内的点位互相不会超过这个距离；超过就说明坐标是坏的
# （成都到都江堰约 50km，取 80km 留些余量，又不会把北京和廊坊算成同城）
SAME_CITY_KM = 80.0

# 同城内的正常点位之间，市内通勤不会超过这个距离；
# 超过就认为这个坐标可疑，需要用地点名去高德核一遍（都江堰这类远郊点会被核，属预期）
VERIFY_KM = 25.0


# 从工具结果原文里抠「地名 + 坐标」对。
# 为什么用正则而不是 json.loads：工具结果在返回前端时会被截断到 2000 字符，
# 截断后 JSON 不完整、解析必然失败，而正则只依赖局部片段，照样能抠出坐标。
_POI_PAIR_RE = re.compile(
    r'"name"\s*:\s*"([^"]{2,60})"[^{}]*?"location"\s*:\s*"([0-9.]{5,},[0-9.]{5,})"'
)


def poi_index_from_steps(steps: list[dict[str, Any]]) -> dict[str, str]:
    """从 Agent 工具轨迹里收集「地名 → 真实坐标」。

    这是坐标的**权威来源**：Researcher 调高德 POI 检索拿到的坐标是真实值，
    而 Planner 转结构化时会写错（实测把锦里写到河北）。与其再打一次接口，
    不如直接把工具结果里的坐标捞回来用，零额外开销。
    """
    index: dict[str, str] = {}
    for s in steps or []:
        if s.get("type") != "tool_result":
            continue
        if s.get("tool") not in ("poi_search", "poi_around", "poi_detail"):
            continue
        content = str(s.get("content") or "")
        if not content:
            continue
        # 先试完整 JSON（未截断时最准）
        try:
            data = json.loads(content)
            pois = data.get("pois") or ([data] if data.get("name") and data.get("location") else [])
            for p in pois:
                name = (p.get("name") or "").strip()
                loc = (p.get("location") or "").strip()
                if name and loc:
                    index[name] = loc
            continue
        except Exception:
            pass
        # 截断过的 JSON 用正则抠
        for name, loc in _POI_PAIR_RE.findall(content):
            index.setdefault(name.strip(), loc.strip())
    return index


def lookup_poi_index(name: str, index: dict[str, str]) -> str | None:
    """在权威坐标表里找地名。先精确匹配，再做包含匹配（取最长的那个）。"""
    if not index or not name:
        return None
    if name in index:
        return index[name]
    best: str | None = None
    best_len = 0
    for k, v in index.items():
        if (name in k or k in name) and len(k) > best_len:
            best, best_len = v, len(k)
    return best


def parse_loc(loc: str) -> tuple[float, float] | None:
    """解析 'lng,lat'，非法或明显越界返回 None。"""
    try:
        lng, lat = (float(x) for x in (loc or "").split(","))
    except Exception:
        return None
    if not (-180 <= lng <= 180 and -90 <= lat <= 90):
        return None
    if lng == 0 and lat == 0:
        return None
    return (lng, lat)


async def _regeo_city(loc: str) -> str:
    """逆地理编码拿城市名（用于给 POI 检索限定城市）。失败返回空串。"""
    try:
        data = await _amap_get("/v3/geocode/regeo", {"location": loc, "radius": "1000"})
    except Exception:
        return ""
    comp = ((data.get("regeocode") or {}).get("addressComponent")) or {}
    city = comp.get("city") or comp.get("province") or ""
    if isinstance(city, list):  # 直辖市有时返回空数组
        city = ""
    return str(city).strip()


async def repair_coordinates(
    items: list[DayItem],
    city: str = "",
    geocoder=None,
    poi_index: dict[str, str] | None = None,
    prefer_name: bool = True,
) -> dict[str, Any]:
    """修正行程里的坐标，防止「LLM 编坐标」毁掉距离矩阵和地图。原地改写 item.place.location。

    背景：Planner 会把坐标填错（实测把锦里填到河北邢台、宽窄巷子填到顺义、
    人民公园填到承德），脏坐标会让距离矩阵彻底失真、路线优化必然无解，地图也标到别的省。

    坐标的取信顺序（越靠前越权威）：
    1. **工具轨迹里的真实坐标**（poi_index）：Researcher 调高德 POI 检索拿到的原始值，
       零额外接口开销，命中率最高
    2. **按地点名重新检索**（geocoder）：兜底，带城市限定，避免重名地点查错
    3. 行程里原有的坐标：只在与「主簇」同城且不太远时才保留

    城市来源：入参 → 权威坐标簇中心逆地理编码 → 地址文本。
    """
    geocoder = geocoder or _location_from_poi
    poi_index = poi_index or {}
    info: dict[str, Any] = {
        "outliers": [],
        "regenerated": [],
        "city": city,
        "dropped": [],
        "from_tools": [],
    }

    parsed: list[tuple[float, float] | None] = [
        parse_loc(it.place.location) if it.place else None for it in items
    ]

    # 1. 参考坐标只用**工具轨迹里的真实坐标**。
    #    绝不能拿行程里的坐标去投票推城市——那批坐标可能就是 LLM 编的，
    #    实测 5 个假点会凑成一个「廊坊簇」，反而把城市推错、把好坐标改坏。
    refs = [p for p in (parse_loc(v) for v in poi_index.values()) if p]

    center: tuple[float, float] | None = None
    reliable = False
    if refs:
        def dist(a: tuple[float, float], b: tuple[float, float]) -> float:
            return _haversine_km(f"{a[0]},{a[1]}", f"{b[0]},{b[1]}")

        best: list[tuple[float, float]] = []
        for r in refs:
            members = [q for q in refs if dist(r, q) <= SAME_CITY_KM]
            if len(members) > len(best):
                best = members
        if len(best) >= 2:
            center = (
                sum(q[0] for q in best) / len(best),
                sum(q[1] for q in best) / len(best),
            )
            reliable = True

    # 2. 城市：入参 → 真实坐标簇中心反查 → 地址文本
    if not city and center:
        city = await _regeo_city(f"{center[0]},{center[1]}")
    if not city:
        city = guess_city([it.place.address for it in items if it.place])
    info["city"] = city
    info["reliable_reference"] = reliable

    def accept(candidate: tuple[float, float] | None) -> bool:
        """没有可靠参照时只能放行；有可靠参照时必须落在同一城市范围内。"""
        if not candidate:
            return False
        if not reliable or center is None:
            return True
        return _haversine_km(f"{center[0]},{center[1]}", f"{candidate[0]},{candidate[1]}") <= SAME_CITY_KM

    # 3. 逐点修正：权威坐标表 → 按名字重查（可疑点）→ 保留原坐标（仅当有可靠参照且同城）
    for i, it in enumerate(items):
        if not it.place:
            continue
        original = parsed[i]
        fresh: tuple[float, float] | None = None
        source = ""

        # 3.1 权威坐标表（零接口开销，命中率最高）
        candidate = parse_loc(lookup_poi_index(it.place.name, poi_index) or "")
        if accept(candidate):
            fresh, source = candidate, "tools"
            if fresh != original:
                if original:
                    info["outliers"].append(it.place.name)
                info["from_tools"].append(it.place.name)

        # 3.2 按名字重查：只查「没坐标」或「有可靠参照但明显离群」的点
        if fresh is None and prefer_name and it.place.name:
            suspicion = (
                not original
                or (
                    reliable
                    and center is not None
                    and _haversine_km(f"{center[0]},{center[1]}", f"{original[0]},{original[1]}") > VERIFY_KM
                )
            )
            if suspicion:
                try:
                    loc = await geocoder(it.place.name, city or it.place.address)
                except Exception:
                    loc = None
                candidate = parse_loc(loc or "")
                if accept(candidate):
                    fresh, source = candidate, "geocode"

        if fresh:
            if source == "geocode" and fresh != original:
                if original:
                    info["outliers"].append(it.place.name)
                info["regenerated"].append(it.place.name)
            it.place.location = f"{fresh[0]:.6f},{fresh[1]:.6f}"
            parsed[i] = fresh
        elif original and (not reliable or not center or _haversine_km(
            f"{center[0]},{center[1]}", f"{original[0]},{original[1]}"
        ) <= SAME_CITY_KM):
            # 查不到，但原坐标可用（有可靠参照时要求同城；没有参照就保留）
            continue
        else:
            it.place.location = ""
            parsed[i] = None
            info["dropped"].append(it.place.name)
    return info


# --------------------------------------------------------------------------
# 时间矩阵
# --------------------------------------------------------------------------
async def _column_from_api(points: list[str], dest: str) -> list[tuple[float, float]]:
    """一次接口拿到「所有点 → dest」的 (分钟, 公里) 列表。"""
    data = await _amap_get(
        "/v3/distance",
        {
            "origins": "|".join(points),
            "destination": dest,
            "type": "1",  # 1=驾车（城市内以打车/驾车为主）
        },
    )
    results = data.get("results") or []
    col: list[tuple[float, float]] = [(0.0, 0.0)] * len(points)
    for item in results:
        try:
            idx = int(item.get("origin_id", "0")) - 1
        except Exception:
            continue
        if not (0 <= idx < len(points)):
            continue
        try:
            minutes = float(item.get("duration", 0)) / 60.0
            km = float(item.get("distance", 0)) / 1000.0
        except Exception:
            continue
        col[idx] = (minutes, km)
    return col


async def build_matrix(points: list[str], allow_api: bool = True) -> tuple[list[list[float]], list[list[float]]]:
    """构建 n×n 的耗时（分钟）与距离（公里）矩阵。

    - 命中缓存的不再请求
    - 单个目标点整列走一次 `/v3/distance`
    - 接口失败/未配置 key → 该列用直线距离 × 1.4（路网系数）/ 25km/h 估算
    """
    n = len(points)
    keys = [_round_loc(p) for p in points]
    mins = [[0.0] * n for _ in range(n)]
    kms = [[0.0] * n for _ in range(n)]

    for j in range(n):
        missing = [i for i in range(n) if i != j and (keys[i], keys[j]) not in _MATRIX_CACHE]
        if missing and allow_api:
            try:
                col = await _column_from_api(points, points[j])
                for i in range(n):
                    if i == j:
                        continue
                    _MATRIX_CACHE[(keys[i], keys[j])] = col[i]
            except Exception:
                pass  # 落到底下的估算兜底

        for i in range(n):
            if i == j:
                continue
            cached = _MATRIX_CACHE.get((keys[i], keys[j]))
            if cached is None:
                d = _haversine_km(keys[i], keys[j]) * 1.4
                cached = (d / 25.0 * 60.0, d)  # 25km/h 城市均速
            mins[i][j], kms[i][j] = cached
    return mins, kms


def apply_cached_matrix(points: list[str], mins: list[list[float]], kms: list[list[float]]) -> None:
    """把一份算好的矩阵写进缓存（离线测试用）。"""
    keys = [_round_loc(p) for p in points]
    for i, ki in enumerate(keys):
        for j, kj in enumerate(keys):
            if i != j:
                _MATRIX_CACHE[(ki, kj)] = (mins[i][j], kms[i][j])


# --------------------------------------------------------------------------
# OR-Tools 求解（同步，调用方负责放线程池）
# --------------------------------------------------------------------------
def solve_tsptw(
    mins: list[list[float]],
    windows: list[tuple[int, int]],
    service: list[int],
    start_min: int = DAY_START_MIN,
    horizon: int = DAY_END_MIN,
    time_limit_ms: int = SOLVE_TIME_LIMIT_MS,
    enforce_service_in_window: bool = True,
) -> list[int] | None:
    """单车辆带时间窗的最短路。

    Args:
        mins: n×n 耗时矩阵（分钟，向上取整后用）
        windows: 每个点的 [最早, 最晚] 到达窗口（分钟）；index 0 是出发节点
        service: 每个点的停留时长（分钟）
        start_min: 出发时刻
        horizon: 时间轴上限
        enforce_service_in_window: True=「到达+停留 ≤ 关门」（严格）；
            False=只要求「到达 ≤ 关门」（宽松，行程是计划而非精确时刻表）

    Returns:
        访问顺序（节点下标列表，以 0 开头）；无可行解返回 None。
    """
    from ortools.constraint_solver import pywrapcp, routing_enums_pb2

    n = len(mins)
    if n < 3:
        return list(range(n))

    cost = [[int(round(mins[i][j])) for j in range(n)] for i in range(n)]
    # 终点：dummy 节点（从任何点回来代价 0，时间窗开到底）
    num_nodes = n + 1
    end = n
    cost_with_end = [row[:] + [0] for row in cost] + [[0] * num_nodes]

    manager = pywrapcp.RoutingIndexManager(num_nodes, 1, [0], [end])
    routing = pywrapcp.RoutingModel(manager)

    def transit_cb(from_index: int, to_index: int) -> int:
        i = manager.IndexToNode(from_index)
        j = manager.IndexToNode(to_index)
        return cost_with_end[i][j]

    transit_id = routing.RegisterTransitCallback(transit_cb)
    routing.SetArcCostEvaluatorOfAllVehicles(transit_id)

    def time_cb(from_index: int, to_index: int) -> int:
        """弧上的时间 = 路上耗时 + 起点停留时长。"""
        i = manager.IndexToNode(from_index)
        j = manager.IndexToNode(to_index)
        add = service[i] if i < len(service) else 0  # 终点节点没有停留时长
        if j == end:
            return cost_with_end[i][j]
        return cost_with_end[i][j] + add

    time_id = routing.RegisterTransitCallback(time_cb)
    routing.AddDimension(
        time_id,
        slack_max=horizon,  # 允许等待（到达后等到开门）
        capacity=horizon,
        fix_start_cumul_to_zero=False,
        name="Time",
    )
    time_dim = routing.GetDimensionOrDie("Time")

    # 注意：终点节点必须用 routing.End(0) 取索引，
    # 用 manager.NodeToIndex(end) 会拿到 -1，CumulVar(-1) 会让 OR-Tools 直接段错误。
    start_index = routing.Start(0)
    end_index = routing.End(0)
    # 出发时刻固定
    time_dim.CumulVar(start_index).SetRange(start_min, start_min)
    # 各点的时间窗：到达时刻 + 停留 ≤ 关门 → 到达必须 ≤ 关门 - 停留
    for i in range(1, n):
        open_min, close_min = windows[i]
        close_cap = min(close_min, horizon)
        latest = close_cap - service[i] if enforce_service_in_window else close_cap
        latest = max(open_min, latest)
        time_dim.CumulVar(manager.NodeToIndex(i)).SetRange(max(open_min, start_min), latest)
    # 终点只要求不超过时间轴上限
    time_dim.CumulVar(end_index).SetRange(start_min, horizon)
    routing.AddVariableMinimizedByFinalizer(time_dim.CumulVar(end_index))

    # 每个点必须被访问（本问题不允许舍弃 POI）
    for i in range(1, n):
        routing.AddDisjunction([manager.NodeToIndex(i)], 10_000_000)

    params = pywrapcp.DefaultRoutingSearchParameters()
    params.first_solution_strategy = routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
    params.local_search_metaheuristic = routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    params.time_limit.FromMilliseconds(int(time_limit_ms))

    solution = routing.SolveWithParameters(params)
    if solution is None:
        return None

    order: list[int] = []
    idx = routing.Start(0)
    while not routing.IsEnd(idx):
        order.append(manager.IndexToNode(idx))
        idx = solution.Value(routing.NextVar(idx))
    return order  # 以 0 开头，不含 dummy 终点


# --------------------------------------------------------------------------
# 单日优化
# --------------------------------------------------------------------------
def _collect_place_slots(day: Day) -> list[int]:
    """找出这一天里「代表地点」的槽位下标（可优化的项）。"""
    return [
        i
        for i, it in enumerate(day.items)
        if it.place is not None and (it.place.name or "").strip()
    ]


def _estimate_order_travel(mins: list[list[float]]) -> float:
    """按原顺序估一个路程耗时（用于判断当天时间预算够不够）。"""
    return sum(mins[i][i + 1] for i in range(len(mins) - 1))


async def _solve_with_fallback(
    mins: list[list[float]],
    windows: list[tuple[int, int]],
    service: list[int],
    start_min: int,
    horizon: int,
) -> tuple[list[int] | None, list[int], str]:
    """三级降级求解，返回 (顺序, 实际使用的停留时长, 模式)。

    为什么需要降级：LLM 给的停留时长常常偏大（一个点 2–3 小时），
    5 个点加起来就超过一天，严格建模必然无解。但行程是**计划**而不是精确时刻表，
    「到得了、别撞闭馆」比「停留时长严格塞进营业窗口」更重要。所以：

    1. strict —— 到达 + 停留 ≤ 关门（最严谨）
    2. loose  —— 只要求到达 ≤ 关门（放宽停留时长）
    3. compressed —— 把停留时长等比压缩到当天时间预算内再排（**只用于排序，不回写行程**）
    """
    n = len(mins)

    def valid(order: list[int] | None) -> bool:
        return bool(order) and sorted(order) == list(range(n))  # type: ignore[arg-type]

    order = await asyncio.to_thread(
        solve_tsptw, mins, windows, service, start_min, horizon, SOLVE_TIME_LIMIT_MS, True
    )
    if valid(order):
        return order, service, "strict"

    order = await asyncio.to_thread(
        solve_tsptw, mins, windows, service, start_min, horizon, SOLVE_TIME_LIMIT_MS, False
    )
    if valid(order):
        return order, service, "loose"

    # 压缩服务时长：留出预算 = 最早的那个「关门/收尾」deadline − 出发时刻 − 路程
    # （用最紧的 deadline 而不是 horizon，否则压完还是赶不上闭馆）
    travel = _estimate_order_travel(mins)
    deadline = min(min(w[1], horizon) for w in windows[1:]) if n > 1 else horizon
    budget = deadline - start_min - travel
    total = sum(service)
    if total > budget > 0:
        scale = budget / total
        squeezed = [max(10, int(s * scale)) for s in service]
        order = await asyncio.to_thread(
            solve_tsptw, mins, windows, squeezed, start_min, horizon, SOLVE_TIME_LIMIT_MS, False
        )
        if valid(order):
            return order, squeezed, "compressed"

    return None, service, "infeasible"


async def optimize_day(
    day: Day,
    city: str = "",
    allow_api: bool = True,
    poi_index: dict[str, str] | None = None,
) -> dict[str, Any]:
    """优化一天的地点顺序，原地改写 day.items。

    返回该天的对比统计（供前端展示）。
    """
    slots = _collect_place_slots(day)
    result: dict[str, Any] = {
        "date": day.date,
        "points": len(slots),
        "applied": False,
        "reordered": False,
        "reason": "",
    }

    if len(slots) < MIN_POINTS_TO_OPTIMIZE:
        result["reason"] = f"地点不足 {MIN_POINTS_TO_OPTIMIZE} 个，未优化"
        return result

    items = [day.items[i] for i in slots]

    # 坐标体检：把 Planner 可能编错的坐标洗一遍（优先用工具轨迹里的真实坐标）
    geo = await repair_coordinates(items, city=city, poi_index=poi_index)
    if geo.get("outliers") or geo.get("regenerated") or geo.get("from_tools"):
        result["geo"] = {
            "outliers": geo.get("outliers", []),
            "regenerated": geo.get("regenerated", []),
            "from_tools": geo.get("from_tools", []),
            "city": geo.get("city", ""),
        }
    city = geo.get("city") or city

    ok = [it for it in items if (it.place.location or "").strip()]
    if len(ok) < MIN_POINTS_TO_OPTIMIZE or len(ok) < len(items):
        result["reason"] = f"仅 {len(ok)}/{len(items)} 个地点有可用坐标，未优化"
        return result

    points = [it.place.location for it in items]  # type: ignore[union-attr]
    service = [int(it.duration_min or DEFAULT_SERVICE_MIN) for it in items]
    # 注意：求解器里节点 0 就是 items[0]（当天首个地点，兼作起点），
    # 所以时间窗数组必须与 items 一一对应，不能在前面插占位元素（否则整体错位一位）。
    windows: list[tuple[int, int]] = []
    for it in items:
        win = parse_open_window(it.place.open_time)  # type: ignore[union-attr]
        windows.append(win or (DAY_START_MIN, DAY_END_MIN))

    # 起点时刻：不早于 08:30，也不早于首个地点的开门时间
    start_min = max(DAY_START_MIN, windows[0][0])
    if start_min + service[0] > windows[0][1]:
        result["reason"] = "首个地点当日营业窗口太短，未优化"
        return result

    t0 = time.monotonic()
    try:
        mins, kms = await build_matrix(points, allow_api=allow_api)
        order, used_service, mode = await _solve_with_fallback(
            mins, windows, service, start_min, DAY_END_MIN
        )
    except Exception as exc:
        result["reason"] = f"求解失败：{exc}"
        result["solve_ms"] = int((time.monotonic() - t0) * 1000)
        return result
    result["solve_ms"] = int((time.monotonic() - t0) * 1000)

    if order is None:
        result["reason"] = "无可行解（时间窗太紧），保留原顺序"
        return result
    result["mode"] = mode

    def leg_total(seq: list[int]) -> tuple[float, float]:
        m = k = 0.0
        for a, b in zip(seq, seq[1:]):
            m += mins[a][b]
            k += kms[a][b]
        return m, k

    before_min, before_km = leg_total(list(range(len(items))))
    after_min, after_km = leg_total(order)
    result.update(
        {
            "applied": True,
            "reordered": order != list(range(len(items))),
            "before_min": round(before_min, 1),
            "after_min": round(after_min, 1),
            "before_km": round(before_km, 1),
            "after_km": round(after_km, 1),
            "saving_pct": round((before_min - after_min) / before_min * 100, 1) if before_min else 0.0,
            "order_before": [items[i].place.name for i in range(len(items))],  # type: ignore[union-attr]
            "order_after": [items[i].place.name for i in order],  # type: ignore[union-attr]
        }
    )

    # 回写：按新顺序重排地点项，非地点项（午餐/交通段）保持原槽位
    # compressed 模式下 service 被等比压缩过，只用于排序，不能覆盖行程里的停留时长
    write_service = mode != "compressed"
    new_items: list[DayItem] = list(day.items)
    t = start_min
    for pos, src in enumerate(order):
        item = items[src]
        t = max(t, windows[src][0])
        item.time = min_to_hhmm(t)
        nxt = order[pos + 1] if pos + 1 < len(order) else None
        if nxt is not None:
            item.transport_to_next = (
                f"到「{items[nxt].place.name}」约 {int(round(mins[src][nxt]))} 分钟"  # type: ignore[union-attr]
                f"（{kms[src][nxt]:.1f} km）"
            )
        if write_service:
            item.duration_min = used_service[src]
        t += used_service[src] + (mins[src][nxt] if nxt is not None else 0)
        new_items[slots[pos]] = item
    day.items = new_items

    # 兜底校验：每个点都得落在营业时间内
    violations = []
    for it in items:
        win = parse_open_window(it.place.open_time)  # type: ignore[union-attr]
        if not win:
            continue
        arrive = _hhmm_to_min(it.time) or DAY_START_MIN
        if arrive < win[0] or arrive + int(it.duration_min or DEFAULT_SERVICE_MIN) > win[1]:
            violations.append(it.place.name)  # type: ignore[union-attr]
    if violations:
        result["violations"] = violations

    if day.summary and result["reordered"]:
        day.summary = day.summary.rstrip("。") + "（顺序已按最短路重新编排）"
    return result


async def optimize_plan(
    plan: TripPlan,
    city: str = "",
    allow_api: bool = True,
    poi_index: dict[str, str] | None = None,
) -> tuple[TripPlan, dict[str, Any]]:
    """对整个行程按天优化，返回 (行程, 汇总统计)。任何一天失败都不影响其它天。"""
    if not plan.days:
        return plan, {"applied": False, "reason": "行程为空", "days": []}

    if not city:
        city = guess_city([it.place.address for d in plan.days for it in d.items if it.place])

    day_stats: list[dict[str, Any]] = []
    for day in plan.days:
        try:
            day_stats.append(
                await optimize_day(day, city=city, allow_api=allow_api, poi_index=poi_index)
            )
        except Exception as exc:  # 单天失败不影响其它天
            day_stats.append({"date": day.date, "applied": False, "reason": f"异常：{exc}"})

    done = [d for d in day_stats if d.get("applied")]
    before_min = round(sum(d["before_min"] for d in done), 1)
    after_min = round(sum(d["after_min"] for d in done), 1)
    before_km = round(sum(d["before_km"] for d in done), 1)
    after_km = round(sum(d["after_km"] for d in done), 1)
    return plan, {
        "applied": bool(done),
        "solver": "ortools-tspTW",
        "days": day_stats,
        "before_min": before_min,
        "after_min": after_min,
        "before_km": before_km,
        "after_km": after_km,
        "saving_pct": round((before_min - after_min) / before_min * 100, 1) if before_min else 0.0,
        "solve_ms": sum(d.get("solve_ms", 0) for d in done),
    }


__all__ = [
    "optimize_plan",
    "optimize_day",
    "build_matrix",
    "apply_cached_matrix",
    "solve_tsptw",
    "parse_open_window",
    "parse_loc",
    "repair_coordinates",
    "poi_index_from_steps",
    "lookup_poi_index",
    "guess_city",
    "min_to_hhmm",
    "DAY_START_MIN",
    "DAY_END_MIN",
    "MIN_POINTS_TO_OPTIMIZE",
]
