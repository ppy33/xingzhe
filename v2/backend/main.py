"""行者 v2 · FastAPI 服务入口

M3 深入主流程：Researcher（调研）→ Critic（审查↔修订闭环）→ Planner（结构化）。

    GET  /health              健康检查 + 密钥配置状态
    POST /api/chat            单轮对话（返回完整结果，含 plan_json）
    POST /api/chat/stream     SSE 流式输出（含工具调用过程，前端 Step 面板用）
"""
from __future__ import annotations

import json
import os
import sys
import uuid
from typing import Any, Awaitable, Callable

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fastapi import FastAPI, HTTPException  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import StreamingResponse  # noqa: E402
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

from agents import (  # noqa: E402
    TripPlan,
    build_critic,
    build_planner,
    build_researcher,
    build_reviser,
    build_whitelist_checker,
    check_new_places,
    optimize_plan,
    plan_to_struct,
    poi_index_from_steps,
    report_brief,
    review_report,
    revise_plan,
)
from config import settings  # noqa: E402
from tools import close_client  # noqa: E402

app = FastAPI(title="行者 v2 · 智能旅游助手", version="2.2.0-m4")

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

_agent = None
_critic = None
_reviser = None
_planner = None
_whitelist = None


def get_agent():
    global _agent
    if _agent is None:
        _agent = build_researcher()
    return _agent


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


@app.post("/api/chat", response_model=ChatResponse)
async def chat(req: ChatRequest) -> ChatResponse:
    if not req.message.strip():
        raise HTTPException(status_code=400, detail="message 不能为空")
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
        raise HTTPException(status_code=500, detail=f"Agent 执行失败：{exc}") from exc

    answer, steps = _collect_steps(result.get("messages", []))
    plan_json: dict[str, Any] | None = None
    review: dict[str, Any] | None = None
    optimize: dict[str, Any] | None = None

    if answer:
        # Critic 审查 ↔ 修订闭环
        answer, critic_steps, review = await _run_critic_loop(req.message, answer)
        steps.extend(critic_steps)
        # Planner：结构化
        plan, plan_steps = await _run_planner(req.message, answer)
        steps.extend(plan_steps)
        if plan is not None:
            # Optimizer：单日最优排序（M4）；坐标用工具轨迹里的真实值兜底
            plan, opt_steps, optimize = await _run_optimizer(
                plan, poi_index=poi_index_from_steps(steps)
            )
            steps.extend(opt_steps)
            plan_json = plan.model_dump()
            answer = plan.to_markdown()

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
                        for call in msg.tool_calls or []:
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

            # Critic 审查 ↔ 修订闭环（步数少，收集后统一回放）
            plan_json: dict[str, Any] | None = None
            if answer:
                answer, critic_steps, review = await _run_critic_loop(req.message, answer)
                for s in critic_steps:
                    yield sse("step", s)
                if review is not None:
                    yield sse("review", review)

                # Planner：结构化
                plan, plan_steps = await _run_planner(req.message, answer)
                for s in plan_steps:
                    yield sse("step", s)
                if plan is not None:
                    # Optimizer：单日最优排序（M4）；坐标用工具轨迹里的真实值兜底
                    plan, opt_steps, optimize = await _run_optimizer(
                        plan, poi_index=poi_index_from_steps(tool_results)
                    )
                    for s in opt_steps:
                        yield sse("step", s)
                    if optimize is not None:
                        yield sse("optimize", optimize)
                    plan_json = plan.model_dump()
                    answer = plan.to_markdown()
                    yield sse("plan_json", plan_json)

            yield sse("token", {"content": answer})
            yield sse("done", {"thread_id": req.thread_id})
        except Exception as exc:
            # 带上异常类型：只给 str(exc) 时，像 NameError 这种空描述在界面上是空白，很难排查
            yield sse("error", {"message": f"{type(exc).__name__}: {exc}"})

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.on_event("shutdown")
async def _shutdown() -> None:
    await close_client()