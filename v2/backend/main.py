"""行者 v2 · FastAPI 服务入口

主流程：Researcher（调研）→ Critic（审查↔修订闭环）→ Planner（结构化）→ Optimizer（路线优化）。
M5 追加：行程落 SQLite + 行程体检（主动感知：闭馆/天气/营业时间巡检 + 自动修复）。

    GET  /health                    健康检查 + 密钥配置状态
    POST /api/chat                  单轮对话（返回完整结果，含 plan_json）
    POST /api/chat/stream           SSE 流式输出（含工具调用过程，前端 Step 面板用）
    GET  /api/trips                 历史行程列表（M5 持久化）
    GET  /api/trips/{thread_id}     取回某次行程（M5 持久化）
    POST /api/trip/check            行程体检（可自动修复，返回问题清单 + 变更明细）
    POST /api/trip/check/stream     行程体检（SSE，逐条推问题与变更）
    GET  /api/metrics               埋点汇总统计（M6 可观测）
"""
from __future__ import annotations

import json
import os
import sys
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any, Awaitable, Callable

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fastapi import FastAPI, HTTPException  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import StreamingResponse  # noqa: E402
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

import storage  # noqa: E402
from agents import (  # noqa: E402
    TripPlan,
    build_critic,
    build_fixer,
    build_planner,
    build_researcher,
    build_reviser,
    build_whitelist_checker,
    check_new_places,
    diff_plans,
    fetch_indoor_candidates,
    fetch_weather,
    fix_trip,
    infer_city,
    inspect_trip,
    optimize_plan,
    plan_to_struct,
    poi_index_from_steps,
    report_brief,
    review_report,
    revise_plan,
)
from config import settings  # noqa: E402
from tools import close_client  # noqa: E402

_agent = None
_critic = None
_reviser = None
_planner = None
_whitelist = None
_fixer = None
_saver_cm = None  # AsyncSqliteSaver 的上下文管理器，需在 lifespan 里持有
_checkpointer = None


def get_agent():
    global _agent
    if _agent is None:
        _agent = build_researcher(checkpointer=_checkpointer)
    return _agent


@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动时初始化 SQLite（行程库 + Agent 对话记忆），关闭时释放。"""
    global _checkpointer, _saver_cm
    path = storage.init_db(settings.db_path)
    print(f"[storage] 行程库就绪：{path}")
    try:
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

        _saver_cm = AsyncSqliteSaver.from_conn_string(str(path))
        _checkpointer = await _saver_cm.__aenter__()
        print("[storage] Agent 对话记忆已切到 SQLite（重启后同一 thread_id 可继续追问）")
    except Exception as exc:  # 退化到内存，不影响主流程
        print(f"[storage] SQLite checkpointer 不可用，回退内存：{exc}")
        _checkpointer = None
    yield
    if _saver_cm is not None:
        await _saver_cm.__aexit__(None, None, None)
        _saver_cm = None
    storage.close_db()
    await close_client()


app = FastAPI(title="行者 v2 · 智能旅游助手", version="2.4.0-m6", lifespan=lifespan)

# Critic 审查-修订最大轮数：第 1 轮不通过会修订并复审，第 2 轮结果无论通过与否都放行
MAX_REVIEW_ROUNDS = 2

# 前端本地开发地址；上线后收紧为正式域名
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def get_critic():
    global _critic
    if _critic is None:
        _critic = build_critic()
    return _critic


def get_reviser():
    global _reviser
    if _reviser is None:
        _reviser = build_reviser()
    return _reviser


def get_planner():
    global _planner
    if _planner is None:
        _planner = build_planner()
    return _planner


def get_whitelist():
    global _whitelist
    if _whitelist is None:
        _whitelist = build_whitelist_checker()
    return _whitelist


def get_fixer():
    global _fixer
    if _fixer is None:
        _fixer = build_fixer()
    return _fixer


class ChatRequest(BaseModel):
    message: str = Field(..., description="用户自然语言输入")
    thread_id: str = Field(default_factory=lambda: uuid.uuid4().hex, description="会话 ID")


class ChatResponse(BaseModel):
    thread_id: str
    answer: str
    steps: list[dict[str, Any]] = []
    plan_json: dict[str, Any] | None = None  # M3：结构化行程
    review: dict[str, Any] | None = None  # M3 打磨：Critic 审查报告（含修订轮次）
    optimize: dict[str, Any] | None = None  # M4：VRPTW 优化前后对比


@app.get("/health")
async def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "amap_key_configured": bool(settings.amap_server_key),
        "llm_configured": bool(settings.deepseek_api_key),
        "model": settings.deepseek_model,
    }


def _collect_steps(msgs: list) -> tuple[str, list[dict[str, Any]]]:
    """从消息列表里抽出工具调用轨迹和最终答案。"""
    steps: list[dict[str, Any]] = []
    answer = ""
    for msg in msgs:
        if isinstance(msg, AIMessage):
            for call in msg.tool_calls or []:
                steps.append(
                    {"type": "tool_call", "tool": call["name"], "args": call["args"]}
                )
            if msg.content and not msg.tool_calls:
                answer = msg.content
        elif isinstance(msg, ToolMessage):
            steps.append(
                {
                    "type": "tool_result",
                    "tool": msg.name,
                    "content": str(msg.content)[:2000],
                }
            )
    return answer, steps


def _extract_usage(msgs: list) -> tuple[int, int]:
    """从消息列表汇总 token 用量（M6 埋点）。返回 (input_tokens, output_tokens)。"""
    total_in = 0
    total_out = 0
    for msg in msgs:
        if not isinstance(msg, AIMessage):
            continue
        um = getattr(msg, "usage_metadata", None) or {}
        total_in += int(um.get("input_tokens") or 0)
        total_out += int(um.get("output_tokens") or 0)
    return total_in, total_out


def _count_tool_calls(steps: list[dict[str, Any]]) -> int:
    """统计 tool_call 步数（不含伪工具，也不含 tool_result）。"""
    return sum(1 for s in steps if s.get("type") == "tool_call")


def _safe_log_metric(**kwargs: Any) -> None:
    """埋点写库，失败不阻塞主流程。"""
    try:
        storage.log_metric(**kwargs)
    except Exception as exc:  # pragma: no cover - 埋点不应拖垮请求
        print(f"[metrics] 埋点写入失败：{exc}")


async def _run_critic_loop(
    user_query: str,
    draft_answer: str,
    emit: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
) -> tuple[str, list[dict[str, Any]], dict[str, Any] | None]:
    """M3 深入：Critic 审查 ↔ 修订闭环。

    流程（最多 MAX_REVIEW_ROUNDS 轮）：
        review → passed? → 放行
                └─ no → revise（带问题清单）→ 下一轮 review → 放行

    返回 (final_answer, steps, review_dict)。
    - review_dict 是给前端展示的完整报告：最终一轮 passed/issues/summary + 修订轮数
    - Critic/Reviser 任何一环失败都兜底返回上一版答案（review_dict 相应为 None 或部分信息）
    - emit 不为 None 时每产生一个 step 就实时推给调用方（SSE 用）。
    """
    steps: list[dict[str, Any]] = []

    async def push(step: dict[str, Any]) -> None:
        steps.append(step)
        if emit is not None:
            await emit(step)

    answer = draft_answer
    final_review: dict[str, Any] | None = None
    revisions = 0
    new_places_total: list[str] = []
    for rnd in range(1, MAX_REVIEW_ROUNDS + 1):
        await push(
            {
                "type": "tool_call",
                "tool": "critic_review",
                "args": {"round": rnd, "focus": "weather,budget,time,geo"},
            }
        )
        try:
            report = await review_report(get_critic(), user_query, answer)
        except Exception as exc:
            await push(
                {
                    "type": "tool_result",
                    "tool": "critic_review",
                    "content": json.dumps(
                        {"round": rnd, "error": str(exc)[:200]}, ensure_ascii=False
                    ),
                }
            )
            return answer, steps, final_review  # 审查挂了就用当前版本放行

        final_review = {
            **report_brief(report),
            "rounds": rnd,
            "revisions": revisions,
            "new_places": new_places_total,
        }
        await push(
            {
                "type": "tool_result",
                "tool": "critic_review",
                "content": json.dumps(
                    {"round": rnd, **report_brief(report)}, ensure_ascii=False
                ),
            }
        )
        if report.passed or rnd == MAX_REVIEW_ROUNDS:
            break

        # 不通过 → 带意见修订
        await push(
            {
                "type": "tool_call",
                "tool": "revise_plan",
                "args": {"round": rnd, "issues": len(report.issues)},
            }
        )
        pre_revision = answer
        try:
            answer = await revise_plan(get_reviser(), user_query, answer, report)
            revisions += 1
            # 程序化白名单核验：提示词约束守不住时，用 flash 兜底把关
            new_places: list[str] = []
            try:
                new_places = await check_new_places(get_whitelist(), pre_revision, answer)
            except Exception:
                pass  # 核验失败不阻塞主流程
            if new_places:
                answer += (
                    "\n\n> **修订审计**：本次修订补充了初版未包含的地点——"
                    + "、".join(new_places)
                    + "（未经工具数据核实，建议出行前自行确认营业时间与门票）\n"
                )
                new_places_total.extend(p for p in new_places if p not in new_places_total)
                new_places_step = new_places
            else:
                new_places_step = []
            await push(
                {
                    "type": "tool_result",
                    "tool": "revise_plan",
                    "content": json.dumps(
                        {
                            "round": rnd,
                            "chars": len(answer),
                            "new_places": new_places_step,
                        },
                        ensure_ascii=False,
                    ),
                }
            )
        except Exception as exc:
            await push(
                {
                    "type": "tool_result",
                    "tool": "revise_plan",
                    "content": json.dumps(
                        {"round": rnd, "error": str(exc)[:200]}, ensure_ascii=False
                    ),
                }
            )
            return answer, steps, final_review  # 修订失败保留修订前的版本

    return answer, steps, final_review


async def _run_planner(user_query: str, draft_answer: str) -> tuple[TripPlan | None, list[dict[str, Any]]]:
    """M3 主流程第二棒：把 Researcher 初版 Markdown 转成 TripPlan 结构化对象。

    返回 (TripPlan | None, steps)。结构化输出偶发失败（超时 / JSON 截断 /
    模型不调工具直接回文本 → 返回 None）都可能发生，因此最多试 3 次：
    第 1 次正常问，后两次追加"必须调用工具"的强提醒。
    """
    steps: list[dict[str, Any]] = [
        {
            "type": "tool_call",
            "tool": "planner_struct",
            "args": {"schema": "TripPlan"},
        }
    ]
    planner = get_planner()
    last_err = ""
    for attempt in range(3):
        try:
            plan = await plan_to_struct(planner, user_query, draft_answer, remind=bool(attempt))
            steps.append(
                {
                    "type": "tool_result",
                    "tool": "planner_struct",
                    "content": json.dumps(
                        {
                            "days": len(plan.days),
                            "items": sum(len(d.items) for d in plan.days),
                            "budget_items": len(plan.budget),
                            "tips": len(plan.tips),
                            **({"attempt": attempt + 1} if attempt else {}),
                        },
                        ensure_ascii=False,
                    ),
                }
            )
            return plan, steps
        except Exception as exc:
            last_err = str(exc)[:200]
            continue
    steps.append(
        {
            "type": "tool_result",
            "tool": "planner_struct",
            "content": json.dumps({"error": last_err}, ensure_ascii=False),
        }
    )
    return None, steps


async def _run_optimizer(
    plan: TripPlan, poi_index: dict[str, str] | None = None
) -> tuple[TripPlan, list[dict[str, Any]], dict[str, Any] | None]:
    """M4 第三棒：对结构化行程做 VRPTW 重排（OR-Tools），原地改写 plan 的顺序与时间。

    返回 (plan, steps, optimize_dict)。优化是增值功能：
    - 无解 / 点数不足 / 接口挂了都只记录原因，行程原样放行
    - optimize_dict 始终返回（含未优化的原因），便于前端如实展示
    - poi_index 是「地名 → 真实坐标」，来自 Researcher 的高德检索结果，
      用于修正 Planner 可能编造的坐标
    """
    steps: list[dict[str, Any]] = [
        {
            "type": "tool_call",
            "tool": "optimizer_route",
            "args": {"solver": "ortools", "objective": "travel_time", "time_limit_ms": 2500},
        }
    ]
    try:
        plan, stat = await optimize_plan(plan, poi_index=poi_index)
    except Exception as exc:
        steps.append(
            {
                "type": "tool_result",
                "tool": "optimizer_route",
                "content": json.dumps({"error": str(exc)[:200]}, ensure_ascii=False),
            }
        )
        return plan, steps, {"applied": False, "reason": f"异常：{exc}"}

    steps.append(
        {
            "type": "tool_result",
            "tool": "optimizer_route",
            "content": json.dumps(
                {
                    "applied": stat.get("applied"),
                    "days": len([d for d in stat.get("days", []) if d.get("applied")]),
                    "before_min": stat.get("before_min", 0),
                    "after_min": stat.get("after_min", 0),
                    "saving_pct": stat.get("saving_pct", 0),
                    "solve_ms": stat.get("solve_ms", 0),
                },
                ensure_ascii=False,
            ),
        }
    )
    return plan, steps, stat


# --------------------------------------------------------------------------
# M5 行程体检（主动感知）
# --------------------------------------------------------------------------
class TripCheckRequest(BaseModel):
    thread_id: str = Field(default="", description="要体检的行程 ID（与 /api/chat 的一致）")
    plan_json: dict[str, Any] | None = Field(
        default=None, description="也可以直接传一份行程，做无状态体检"
    )
    auto_fix: bool = Field(default=True, description="发现问题后是否自动修复")
    reoptimize: bool = Field(default=True, description="修复后是否重跑路线优化")
    mock_weather: dict[str, str] | None = Field(
        default=None,
        description="演示/测试用：{日期: 天气}，给定时覆盖真实天气（例如 {'2026-09-15': '中雨'}）",
    )
    save: bool = Field(default=True, description="是否把修复结果写回行程库")


class TripCheckResponse(BaseModel):
    thread_id: str
    inspection: dict[str, Any]
    changes: list[dict[str, Any]] = []
    plan_json: dict[str, Any] | None = None
    answer: str = ""
    optimize: dict[str, Any] | None = None
    fixed: bool = False


def _load_plan_for_check(req: TripCheckRequest) -> tuple[str, TripPlan]:
    """取要体检的行程：优先用请求里的 plan_json，否则按 thread_id 从库里取。"""
    if req.plan_json:
        return req.thread_id or "adhoc", TripPlan.model_validate(req.plan_json)
    if not req.thread_id:
        raise HTTPException(status_code=400, detail="需要 thread_id 或 plan_json 之一")
    saved = storage.get_trip(req.thread_id)
    if saved is None:
        raise HTTPException(
            status_code=404, detail=f"没找到 thread_id={req.thread_id} 的行程，请先生成行程"
        )
    return req.thread_id, TripPlan.model_validate(saved["plan"])


async def _run_trip_check(
    req: TripCheckRequest,
    emit: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
) -> dict[str, Any]:
    """体检核心：取行程 → 查天气 → 规则巡检 → （可选）自动修复 → （可选）重优化 → 存档。

    返回给接口的 payload；emit 不为 None 时每步实时回调（SSE 用）。
    """
    t_start = time.perf_counter()
    steps: list[dict[str, Any]] = []

    async def push(step: dict[str, Any]) -> None:
        steps.append(step)
        if emit is not None:
            await emit(step)

    thread_id, plan = _load_plan_for_check(req)

    # 1) 天气
    city = await infer_city(plan)
    await push(
        {
            "type": "tool_call",
            "tool": "insp_weather",
            "args": {"city": city or "（未知）", "mock": bool(req.mock_weather)},
        }
    )
    if req.mock_weather:
        weather = dict(req.mock_weather)
        note = "（演示用模拟天气）"
    else:
        weather = await fetch_weather(city)
        note = ""
    await push(
        {
            "type": "tool_result",
            "tool": "insp_weather",
            "content": json.dumps(
                {"city": city, "days": len(weather), "note": note, "weather": weather},
                ensure_ascii=False,
            ),
        }
    )

    # 2) 规则巡检
    await push(
        {
            "type": "tool_call",
            "tool": "insp_rules",
            "args": {"checks": "closure,weather,hours"},
        }
    )
    inspection = inspect_trip(plan, weather)
    counts = {"high": 0, "medium": 0, "low": 0}
    for i in inspection.issues:
        counts[i.severity] = counts.get(i.severity, 0) + 1
    await push(
        {
            "type": "tool_result",
            "tool": "insp_rules",
            "content": json.dumps(
                {
                    "issue_count": len(inspection.issues),
                    "high": counts["high"],
                    "medium": counts["medium"],
                    "low": counts["low"],
                    "summary": inspection.summary,
                },
                ensure_ascii=False,
            ),
        }
    )

    result: dict[str, Any] = {
        "thread_id": thread_id,
        "inspection": inspection.model_dump(),
        "changes": [],
        "plan_json": plan.model_dump(),
        "answer": plan.to_markdown(),
        "optimize": None,
        "fixed": False,
    }

    # 3) 自动修复
    if req.auto_fix and inspection.issues:
        await push(
            {
                "type": "tool_call",
                "tool": "insp_fix",
                "args": {"issues": len(inspection.issues), "auto": True},
            }
        )
        try:
            candidates: list[dict[str, Any]] = []
            if any(i.kind == "weather" for i in inspection.issues):
                candidates = await fetch_indoor_candidates(city)
            fixed_plan = await fix_trip(get_fixer(), plan, inspection, candidates)
            changes = diff_plans(plan, fixed_plan, inspection)
            plan = fixed_plan
            result.update(
                {
                    "plan_json": plan.model_dump(),
                    "answer": plan.to_markdown(),
                    "changes": [c.model_dump() for c in changes],
                    "fixed": bool(changes),
                }
            )
            await push(
                {
                    "type": "tool_result",
                    "tool": "insp_fix",
                    "content": json.dumps(
                        {
                            "changes": len(changes),
                            "added_places": len([c for c in changes if c.added]),
                            "candidates": len(candidates),
                        },
                        ensure_ascii=False,
                    ),
                }
            )
        except Exception as exc:
            await push(
                {
                    "type": "tool_result",
                    "tool": "insp_fix",
                    "content": json.dumps({"error": str(exc)[:200]}, ensure_ascii=False),
                }
            )

        # 4) 修复后重跑路线优化
        if result["fixed"] and req.reoptimize:
            plan, opt_steps, optimize = await _run_optimizer(plan)
            for s in opt_steps:
                await push(s)
            result["plan_json"] = plan.model_dump()
            result["answer"] = plan.to_markdown()
            result["optimize"] = optimize

    # 5) 存档 + 事件（无 thread_id 的临时行程不落库）
    if req.save and thread_id and thread_id != "adhoc":
        storage.add_event(
            thread_id,
            "trip_check",
            {
                "issues": len(inspection.issues),
                "changes": len(result["changes"]),
                "fixed": result["fixed"],
            },
        )
        if result["fixed"]:
            storage.save_trip(thread_id, result["plan_json"], answer=result["answer"])
            storage.add_event(thread_id, "trip_fixed", {"changes": result["changes"]})

    # M6 埋点：体检耗时 / 工具数
    _safe_log_metric(
        thread_id=thread_id,
        kind="trip_check",
        status="ok",
        total_ms=int((time.perf_counter() - t_start) * 1000),
        tool_calls=_count_tool_calls(steps),
        stages={"inspect": int((time.perf_counter() - t_start) * 1000)},
    )

    result["steps"] = steps
    return result


@app.post("/api/trip/check", response_model=TripCheckResponse)
async def trip_check(req: TripCheckRequest) -> TripCheckResponse:
    """行程体检：巡检闭馆/天气/营业时间，可自动修复并重排路线。"""
    data = await _run_trip_check(req)
    return TripCheckResponse(**{k: v for k, v in data.items() if k != "steps"})


@app.post("/api/trip/check/stream")
async def trip_check_stream(req: TripCheckRequest) -> StreamingResponse:
    """行程体检（SSE）：逐条推问题与变更，前端可边收边高亮。

    事件：check_start → step×N → issue×N → trip_update → plan_json → optimize? → done
    """
    async def event_gen():
        def sse(event: str, data: Any) -> str:
            return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

        yield sse("check_start", {"thread_id": req.thread_id, "auto_fix": req.auto_fix})
        try:
            queue: list[dict[str, Any]] = []

            async def emit(step: dict[str, Any]) -> None:
                queue.append(step)

            data = await _run_trip_check(req, emit=emit)
            for step in data.pop("steps", []):
                yield sse("step", step)
            for issue in data["inspection"]["issues"]:
                yield sse("issue", issue)
            yield sse(
                "trip_update",
                {
                    "thread_id": data["thread_id"],
                    "inspection": data["inspection"],
                    "changes": data["changes"],
                    "fixed": data["fixed"],
                },
            )
            if data.get("plan_json"):
                yield sse("plan_json", data["plan_json"])
            if data.get("optimize"):
                yield sse("optimize", data["optimize"])
            yield sse("done", {"thread_id": data["thread_id"]})
        except HTTPException as exc:
            yield sse("error", {"message": exc.detail})
        except Exception as exc:
            yield sse("error", {"message": f"{type(exc).__name__}: {exc}"})

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/trips")
async def list_trips(limit: int = 20) -> dict[str, Any]:
    """历史行程列表（M5 持久化：重启后仍可查）。"""
    return {"trips": storage.list_trips(limit)}


@app.get("/api/trips/{thread_id}")
async def get_trip(thread_id: str) -> dict[str, Any]:
    """取回某次行程（含修复历史事件）。"""
    saved = storage.get_trip(thread_id)
    if saved is None:
        raise HTTPException(status_code=404, detail=f"没找到 thread_id={thread_id}")
    saved["events"] = storage.list_events(thread_id)
    return saved


@app.get("/api/metrics")
async def metrics(limit: int = 50) -> dict[str, Any]:
    """M6 可观测：汇总统计 + 最近明细（前端埋点面板用）。"""
    return {
        "summary": storage.summary_metrics(),
        "recent": storage.list_metrics(limit),
    }


@app.post("/api/chat", response_model=ChatResponse)
async def chat(req: ChatRequest) -> ChatResponse:
    if not req.message.strip():
        raise HTTPException(status_code=400, detail="message 不能为空")
    t_start = time.perf_counter()
    stages: dict[str, int] = {}
    input_tokens = output_tokens = 0
    try:
        agent = get_agent()
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    config = {"configurable": {"thread_id": req.thread_id}, "recursion_limit": 60}
    try:
        result = await agent.ainvoke(
            {"messages": [HumanMessage(content=req.message)]}, config=config
        )
    except Exception as exc:  # 让前端拿到可读错误
        _safe_log_metric(
            thread_id=req.thread_id, status="error",
            total_ms=int((time.perf_counter() - t_start) * 1000), stages={"researcher": int((time.perf_counter() - t_start) * 1000)},
        )
        raise HTTPException(status_code=500, detail=f"Agent 执行失败：{exc}") from exc

    msgs = result.get("messages", [])
    input_tokens, output_tokens = _extract_usage(msgs)
    stages["researcher"] = int((time.perf_counter() - t_start) * 1000)

    answer, steps = _collect_steps(msgs)
    plan_json: dict[str, Any] | None = None
    review: dict[str, Any] | None = None
    optimize: dict[str, Any] | None = None

    if answer:
        # Critic 审查 ↔ 修订闭环
        t = time.perf_counter()
        answer, critic_steps, review = await _run_critic_loop(req.message, answer)
        steps.extend(critic_steps)
        stages["critic"] = int((time.perf_counter() - t) * 1000)
        # Planner：结构化
        t = time.perf_counter()
        plan, plan_steps = await _run_planner(req.message, answer)
        steps.extend(plan_steps)
        stages["planner"] = int((time.perf_counter() - t) * 1000)
        if plan is not None:
            # Optimizer：单日最优排序（M4）；坐标用工具轨迹里的真实值兜底
            t = time.perf_counter()
            plan, opt_steps, optimize = await _run_optimizer(
                plan, poi_index=poi_index_from_steps(steps)
            )
            steps.extend(opt_steps)
            stages["optimizer"] = int((time.perf_counter() - t) * 1000)
            plan_json = plan.model_dump()
            answer = plan.to_markdown()
            # M5：行程落库，重启后仍可体检 / 追问
            try:
                storage.save_trip(req.thread_id, plan_json, answer=answer, title=plan.title)
            except Exception as exc:
                print(f"[storage] 行程入库失败（不影响本次响应）：{exc}")

    # M6 埋点：耗时 / 工具数 / token
    _safe_log_metric(
        thread_id=req.thread_id,
        kind="chat",
        status="ok",
        total_ms=int((time.perf_counter() - t_start) * 1000),
        tool_calls=_count_tool_calls(steps),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        stages=stages,
    )

    return ChatResponse(
        thread_id=req.thread_id,
        answer=answer,
        steps=steps,
        plan_json=plan_json,
        review=review,
        optimize=optimize,
    )


@app.post("/api/chat/stream")
async def chat_stream(req: ChatRequest) -> StreamingResponse:
    """SSE 流式接口：把 Agent 的每一步实时推给前端。

    事件类型：
        step       —— 工具调用/返回（含 planner_struct / optimizer_route）
        review     —— Critic 审查报告（结构化，前端展示用）
        plan_json  —— 结构化行程对象（前端可用来驱动交互）
        optimize   —— VRPTW 优化前后对比（里程/耗时/下降百分比）
        token      —— 最终答案的完整 Markdown
        done       —— 结束
        error      —— 出错
    """
    if not req.message.strip():
        raise HTTPException(status_code=400, detail="message 不能为空")
    agent = get_agent()
    config = {"configurable": {"thread_id": req.thread_id}, "recursion_limit": 60}

    async def event_gen():
        def sse(event: str, data: Any) -> str:
            return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

        t_start = time.perf_counter()
        stages: dict[str, int] = {}
        input_tokens = output_tokens = 0
        tool_call_count = 0

        yield sse("start", {"thread_id": req.thread_id})
        try:
            seen = 0
            answer = ""
            # 工具结果的副本：SSE 是边收边发的，但坐标体检需要回头翻工具轨迹，
            # 所以这里必须自己留一份（不能复用非流式分支的 steps 变量）
            tool_results: list[dict[str, Any]] = []
            async for chunk in agent.astream(
                {"messages": [HumanMessage(content=req.message)]},
                config=config,
                stream_mode="values",
            ):
                msgs = chunk.get("messages") or []
                for msg in msgs[seen:]:
                    if isinstance(msg, AIMessage):
                        um = getattr(msg, "usage_metadata", None) or {}
                        input_tokens += int(um.get("input_tokens") or 0)
                        output_tokens += int(um.get("output_tokens") or 0)
                        for call in msg.tool_calls or []:
                            tool_call_count += 1
                            yield sse(
                                "step",
                                {
                                    "type": "tool_call",
                                    "tool": call["name"],
                                    "args": call["args"],
                                },
                            )
                        if msg.content and not msg.tool_calls:
                            answer = msg.content
                    elif isinstance(msg, ToolMessage):
                        tool_results.append(
                            {
                                "type": "tool_result",
                                "tool": msg.name,
                                "content": str(msg.content)[:2000],
                            }
                        )
                        yield sse(
                            "step",
                            {
                                "type": "tool_result",
                                "tool": msg.name,
                                "content": str(msg.content)[:2000],
                            },
                        )
                seen = len(msgs)
            stages["researcher"] = int((time.perf_counter() - t_start) * 1000)

            # Critic 审查 ↔ 修订闭环（步数少，收集后统一回放）
            plan_json: dict[str, Any] | None = None
            if answer:
                t = time.perf_counter()
                answer, critic_steps, review = await _run_critic_loop(req.message, answer)
                for s in critic_steps:
                    yield sse("step", s)
                stages["critic"] = int((time.perf_counter() - t) * 1000)
                if review is not None:
                    yield sse("review", review)

                # Planner：结构化
                t = time.perf_counter()
                plan, plan_steps = await _run_planner(req.message, answer)
                for s in plan_steps:
                    yield sse("step", s)
                stages["planner"] = int((time.perf_counter() - t) * 1000)
                if plan is not None:
                    # Optimizer：单日最优排序（M4）；坐标用工具轨迹里的真实值兜底
                    t = time.perf_counter()
                    plan, opt_steps, optimize = await _run_optimizer(
                        plan, poi_index=poi_index_from_steps(tool_results)
                    )
                    for s in opt_steps:
                        yield sse("step", s)
                    stages["optimizer"] = int((time.perf_counter() - t) * 1000)
                    if optimize is not None:
                        yield sse("optimize", optimize)
                    plan_json = plan.model_dump()
                    answer = plan.to_markdown()
                    # M5：行程落库（失败不影响本次响应）
                    try:
                        storage.save_trip(
                            req.thread_id, plan_json, answer=answer, title=plan.title
                        )
                    except Exception as exc:
                        print(f"[storage] 行程入库失败（不影响本次响应）：{exc}")
                    yield sse("plan_json", plan_json)

            # M6 埋点
            _safe_log_metric(
                thread_id=req.thread_id,
                kind="chat",
                status="ok",
                total_ms=int((time.perf_counter() - t_start) * 1000),
                tool_calls=tool_call_count,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                stages=stages,
            )

            yield sse("token", {"content": answer})
            yield sse("done", {"thread_id": req.thread_id})
        except Exception as exc:
            _safe_log_metric(
                thread_id=req.thread_id,
                kind="chat",
                status="error",
                total_ms=int((time.perf_counter() - t_start) * 1000),
                tool_calls=tool_call_count,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                stages=stages,
            )
            # 带上异常类型：只给 str(exc) 时，像 NameError 这种空描述在界面上是空白，很难排查
            yield sse("error", {"message": f"{type(exc).__name__}: {exc}"})

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
