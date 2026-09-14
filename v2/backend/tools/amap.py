"""高德开放平台工具层（Web 服务 API）

命名沿用行者 v1 的 `namespace.action` 约定，方便和前端 Step 面板对齐：
    poi.search / poi.around / poi.detail
    weather.now / weather.forecast
    route.plan
    geo.geocode

所有工具返回**紧凑 JSON 字符串**（已裁剪字段），避免把高德的冗长响应
整包塞进 LLM 上下文烧 token。
"""
from __future__ import annotations

import asyncio
import functools
import json
import os
import time
from typing import Any, Callable, Literal

import httpx
from langchain_core.tools import tool

from config import settings

_client: httpx.AsyncClient | None = None

# 高德个人开发者 Key 有 QPS / 并发限制，Agent 会并行发工具调用，
# 这里用串行 + 最小间隔限流，并在超限时退避重试。
MIN_INTERVAL = float(os.getenv("AMAP_MIN_INTERVAL", "0.4"))
MAX_RETRY = int(os.getenv("AMAP_MAX_RETRY", "3"))
_QPS_CODES = {"10019", "10020", "10021", "10022", "10003"}

_rate_lock = asyncio.Lock()
_last_call = 0.0


class AmapError(RuntimeError):
    """高德接口业务错误。"""


def resilient(fn: Callable) -> Callable:
    """工具层护栏：把异常转成错误 JSON 回给 LLM，而不是让整个图崩掉。

    LLM 看到 {"error": ...} 后可以自己换关键词重试或降级处理。
    """

    @functools.wraps(fn)
    async def wrapper(*args: Any, **kwargs: Any) -> str:
        try:
            return await fn(*args, **kwargs)
        except AmapError as exc:
            return _dumps(
                {
                    "error": str(exc),
                    "hint": "请调整关键词/城市/参数后重试，或改用其它工具",
                }
            )
        except httpx.HTTPError as exc:
            return _dumps(
                {
                    "error": f"网络请求失败：{exc}",
                    "hint": "稍后重试",
                }
            )

    return wrapper


async def _throttle() -> None:
    """串行化 + 最小调用间隔，避免触发高德 QPS 限制。"""
    global _last_call
    async with _rate_lock:
        gap = time.monotonic() - _last_call
        if gap < MIN_INTERVAL:
            await asyncio.sleep(MIN_INTERVAL - gap)
        _last_call = time.monotonic()


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=settings.http_timeout)
    return _client


async def close_client() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


async def _amap_get(path: str, params: dict[str, Any]) -> dict[str, Any]:
    """调用高德接口，统一处理限流、退避重试和 status/infocode。"""
    params = {k: v for k, v in params.items() if v not in (None, "")}
    params["key"] = settings.require_amap()
    params["output"] = "JSON"

    last_error = ""
    for attempt in range(MAX_RETRY):
        await _throttle()
        try:
            resp = await _get_client().get(f"{settings.amap_base}{path}", params=params)
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPError as exc:
            last_error = f"网络错误：{exc}"
            if attempt < MAX_RETRY - 1:
                await asyncio.sleep(0.8 * (attempt + 1))
                continue
            raise AmapError(last_error) from exc

        if str(data.get("status")) == "1":
            return data

        infocode = str(data.get("infocode", ""))
        last_error = f"[{infocode}] {data.get('info')}（{path}）"
        # QPS / 并发超限 → 退避重试；其它错误码直接抛出
        if infocode in _QPS_CODES and attempt < MAX_RETRY - 1:
            await asyncio.sleep(0.8 * (attempt + 1))
            continue
        raise AmapError(f"高德接口错误 {last_error}")

    raise AmapError(f"高德接口错误（重试 {MAX_RETRY} 次后仍失败）：{last_error}")


def _dumps(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, default=str)


def _clean(value: Any) -> Any:
    """丢弃高德返回的空值（'', '[]', [], None），让 JSON 更紧凑。"""
    if value in (None, "", "[]", "{}", [], {}):
        return None
    return value


def _slim_poi(poi: dict[str, Any]) -> dict[str, Any]:
    """裁剪 POI 字段：只保留 LLM 规划行程真正需要的。"""
    out: dict[str, Any] = {
        "id": poi.get("id"),
        "name": poi.get("name"),
        "type": poi.get("type"),
        "address": poi.get("address"),
        "location": poi.get("location"),
    }
    if _clean(poi.get("tel")):
        out["tel"] = poi["tel"]
    biz = poi.get("biz_ext") or {}
    if isinstance(biz, dict):
        for key in ("rating", "cost", "open_time"):
            if _clean(biz.get(key)):
                out[key] = biz[key]
        if _clean(biz.get("opentime2")) and "open_time" not in out:
            out["open_time"] = biz["opentime2"]
    if _clean(poi.get("cityname")):
        out["city"] = poi["cityname"]
    return {k: v for k, v in out.items() if _clean(v)}


# --------------------------------------------------------------------------
# poi.search —— 按关键词搜景点 / 餐厅 / 酒店
# --------------------------------------------------------------------------
@tool
@resilient
async def poi_search(
    keywords: str,
    city: str,
    types: str = "",
    limit: int = 8,
) -> str:
    """按关键词搜索某城市的 POI（景点、餐厅、酒店、商场等）。

    Args:
        keywords: 搜索关键词，例如「宽窄巷子」「火锅」「青年旅舍」。
        city: 城市名或 adcode，例如「成都」或「510100」。
        types: 可选，高德 POI 分类码，例如「110000」= 风景名胜，「050000」= 餐饮。
        limit: 返回条数，1-25，默认 8。
    """
    limit = max(1, min(int(limit), 25))
    data = await _amap_get(
        "/v3/place/text",
        {
            "keywords": keywords,
            "city": city,
            "types": types,
            "offset": limit,
            "page": 1,
            "extensions": "all",
            "citylimit": "true",
        },
    )
    pois = [_slim_poi(p) for p in (data.get("pois") or [])][:limit]
    return _dumps({"count": len(pois), "city": city, "pois": pois})


# --------------------------------------------------------------------------
# poi.around —— 按坐标搜周边
# --------------------------------------------------------------------------
@tool
@resilient
async def poi_around(
    location: str,
    keywords: str = "",
    radius: int = 2000,
    types: str = "",
    limit: int = 8,
) -> str:
    """搜索某个坐标周边的 POI，适合「这附近有什么好吃的」这类需求。

    Args:
        location: 中心点坐标，格式「经度,纬度」，例如「104.06,30.67」。
        keywords: 可选关键词，例如「火锅」。留空则按 types 返回.
        radius: 搜索半径（米），默认 2000，最大 50000。
        types: 可选，高德 POI 分类码。
        limit: 返回条数，1-25，默认 8。
    """
    limit = max(1, min(int(limit), 25))
    data = await _amap_get(
        "/v3/place/around",
        {
            "location": location,
            "keywords": keywords,
            "types": types,
            "radius": int(radius),
            "offset": limit,
            "page": 1,
            "extensions": "all",
        },
    )
    pois = [_slim_poi(p) for p in (data.get("pois") or [])][:limit]
    return _dumps({"count": len(pois), "center": location, "radius": radius, "pois": pois})


# --------------------------------------------------------------------------
# poi.detail —— 单个 POI 详情（营业时间、评分、门票）
# --------------------------------------------------------------------------
@tool
@resilient
async def poi_detail(poi_id: str) -> str:
    """查询单个 POI 的详细信息：营业时间、评分、人均消费、电话、官网。

    输入必须是 poi.search / poi.around 返回结果里的 id 字段。

    Args:
        poi_id: POI 的唯一 id。
    """
    data = await _amap_get("/v3/place/detail", {"id": poi_id, "extensions": "all"})
    pois = data.get("pois") or []
    if not pois:
        return _dumps({"error": "未找到该 POI", "id": poi_id})
    poi = pois[0]
    slim = _slim_poi(poi)
    # 详情接口额外带深度信息
    deep = poi.get("biz_ext") or {}
    if isinstance(deep, dict) and deep.get("open_time"):
        slim["open_time"] = deep["open_time"]
    if poi.get("photos"):
        slim["has_photos"] = True
    return _dumps(slim)


# --------------------------------------------------------------------------
# weather.now —— 实时天气
# --------------------------------------------------------------------------
@tool
@resilient
async def weather_now(city: str) -> str:
    """查询城市实时天气（天气现象、气温、风向、风力、湿度）。

    Args:
        city: 城市名或 adcode，例如「成都」或「510100」。
    """
    data = await _amap_get(
        "/v3/weather/weatherInfo", {"city": city, "extensions": "base"}
    )
    lives = data.get("lives") or []
    if not lives:
        return _dumps({"error": "未取到实时天气", "city": city})
    live = lives[0]
    return _dumps(
        {
            "city": live.get("city"),
            "weather": live.get("weather"),
            "temperature": f"{live.get('temperature')}℃",
            "wind": f"{live.get('winddirection')}风 {live.get('windpower')}级",
            "humidity": f"{live.get('humidity')}%",
            "report_time": live.get("reporttime"),
        }
    )


# --------------------------------------------------------------------------
# weather.forecast —— 未来几天预报
# --------------------------------------------------------------------------
@tool
@resilient
async def weather_forecast(city: str, days: int = 4) -> str:
    """查询城市未来几天的天气预报，行程规划时用来判断哪天适合户外。

    Args:
        city: 城市名或 adcode，例如「成都」。
        days: 返回天数，1-4，默认 4。
    """
    days = max(1, min(int(days), 4))
    data = await _amap_get(
        "/v3/weather/weatherInfo", {"city": city, "extensions": "all"}
    )
    forecasts = data.get("forecasts") or []
    if not forecasts:
        return _dumps({"error": "未取到预报", "city": city})
    fc = forecasts[0]
    casts = (fc.get("casts") or [])[:days]
    return _dumps(
        {
            "city": fc.get("city"),
            "release_time": fc.get("reporttime"),
            "days": [
                {
                    "date": c.get("date"),
                    "week": c.get("week"),
                    "day_weather": c.get("dayweather"),
                    "night_weather": c.get("nightweather"),
                    "temp": f"{c.get('nighttemp')}~{c.get('daytemp')}℃",
                    "day_wind": f"{c.get('daywind')}风 {c.get('daypower')}级",
                }
                for c in casts
            ],
        }
    )


# --------------------------------------------------------------------------
# geo.geocode —— 地址转坐标
# --------------------------------------------------------------------------
@tool
@resilient
async def geo_geocode(address: str, city: str = "") -> str:
    """把地址/景点名转换成经纬度坐标，供 poi.around 或 route.plan 使用。

    对「成都东站」这类设施名，会自动回落到 POI 搜索拿精确坐标，
    避免地理编码只返回到市级。

    Args:
        address: 地址或地点名，例如「成都东站」「上海市南京东路」。
        city: 可选，限定城市，例如「成都」。
    """
    data = await _amap_get("/v3/geocode/geo", {"address": address, "city": city})
    geocodes = data.get("geocodes") or []
    if not geocodes:
        return _dumps({"error": "地址解析失败", "address": address})

    g = geocodes[0]
    result = {
        "name": address,
        "formatted_address": g.get("formatted_address"),
        "location": g.get("location"),
        "city": g.get("city"),
        "adcode": g.get("adcode"),
        "level": g.get("level"),
    }
    # 地理编码只定位到省/市说明没匹配到具体设施，改用 POI 搜索兜底
    if g.get("level") in ("省", "市", "区县"):
        poi_loc = await _location_from_poi(address, city or g.get("city", ""))
        if poi_loc:
            result["location"] = poi_loc
            result["source"] = "poi_search"
            result["level"] = "poi"
    return _dumps(result)


async def _location_from_poi(name: str, city: str = "") -> str | None:
    """内部工具：用 POI 文本搜索反查设施坐标。"""
    try:
        data = await _amap_get(
            "/v3/place/text",
            {
                "keywords": name,
                "city": city,
                "offset": 1,
                "page": 1,
                "extensions": "base",
                "citylimit": "true",
            },
        )
    except RuntimeError:
        return None
    pois = data.get("pois") or []
    return pois[0].get("location") if pois else None


# --------------------------------------------------------------------------
# route.plan —— 两点间路径规划（含耗时/距离/费用）
# --------------------------------------------------------------------------
@tool
@resilient
async def route_plan(
    origin: str,
    destination: str,
    mode: Literal["driving", "walking", "bicycling", "transit"] = "transit",
    city: str = "",
) -> str:
    """规划两点之间的路线，返回距离、预计耗时和分段说明。

    Args:
        origin: 起点，支持「经度,纬度」坐标或地址文字。
        destination: 终点，格式同上。
        mode: 出行方式，driving 驾车 / walking 步行 / bicycling 骑行 / transit 公交。
        city: 公交模式必填，起终点所在城市（transit 用于选城市公交库）。
    """
    origin_loc = await _to_location(origin, city)
    dest_loc = await _to_location(destination, city)
    if not origin_loc or not dest_loc:
        return _dumps({"error": "起点或终点无法解析", "origin": origin, "destination": destination})

    path_map = {
        "driving": "/v3/direction/driving",
        "walking": "/v3/direction/walking",
        "bicycling": "/v3/direction/bicycling",
        "transit": "/v3/direction/transit/integrated",
    }
    params: dict[str, Any] = {
        "origin": origin_loc,
        "destination": dest_loc,
        "extensions": "base",
    }
    if mode in ("driving", "transit"):
        params["city"] = city or ""
    if mode == "transit":
        params["cityd"] = city or ""
    data = await _amap_get(path_map[mode], params)

    route = data.get("route") or {}
    paths = route.get("paths") or []
    result: dict[str, Any] = {
        "mode": mode,
        "origin": origin_loc,
        "destination": dest_loc,
    }
    if mode != "transit":
        # 驾车/步行/骑行的 distance 与 duration 在 paths[0] 里，不在 route 顶层
        path = paths[0] if paths else {}
        distance = path.get("distance") or route.get("distance") or 0
        duration = path.get("duration") or route.get("duration") or 0
        result["distance_km"] = round(float(distance) / 1000, 2)
        result["duration_min"] = round(float(duration) / 60, 1)
        if route.get("tolls"):
            result["tolls_yuan"] = route["tolls"]
        result["steps"] = [
            s.get("instruction")
            for s in (path.get("steps") or [])[:8]
            if s.get("instruction")
        ]
    else:
        result["distance_km"] = round(float(route.get("distance", 0)) / 1000, 2)
        transits = route.get("transits") or []
        result["transit_options"] = []
        for t in transits[:3]:
            segs = []
            for seg in (t.get("segments") or [])[:4]:
                walk = (seg.get("walking") or {}).get("distance")
                bus = seg.get("bus") or {}
                lines = bus.get("buslines") or []
                if lines:
                    segs.append(f"{lines[0].get('name')}（{lines[0].get('departure_stop',{}).get('name')}→{lines[0].get('arrival_stop',{}).get('name')}）")
                elif walk:
                    segs.append(f"步行{walk}米")
            result["transit_options"].append(
                {
                    "duration_min": round(float(t.get("duration", 0)) / 60, 1),
                    "cost_yuan": t.get("cost"),
                    "segments": segs,
                }
            )
    return _dumps(result)


async def _to_location(value: str, city: str = "") -> str | None:
    """内部工具：把「经度,纬度」原样返回，否则走地理编码 + POI 兜底。"""
    value = (value or "").strip()
    if not value:
        return None
    parts = value.split(",")
    if len(parts) == 2:
        try:
            float(parts[0])
            float(parts[1])
            return value
        except ValueError:
            pass
    data = await _amap_get("/v3/geocode/geo", {"address": value, "city": city})
    geocodes = data.get("geocodes") or []
    if geocodes:
        g = geocodes[0]
        if g.get("level") not in ("省", "市", "区县"):
            return g.get("location")
    # 地理编码没落到具体设施 → 用 POI 搜索兜底
    return await _location_from_poi(value, city) or (
        geocodes[0].get("location") if geocodes else None
    )


# 供 agents 层引用的工具清单
AMAP_TOOLS = [
    poi_search,
    poi_around,
    poi_detail,
    weather_now,
    weather_forecast,
    geo_geocode,
    route_plan,
]
