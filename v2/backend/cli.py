"""行者 v2 · CLI 调试入口

用法：
    python cli.py "从上海出发，去成都玩4天，喜欢美食和人文"
    python cli.py                      # 交互模式
"""
from __future__ import annotations

import asyncio
import os
import sys

# 让 `python cli.py` 在 backend 目录下能正确 import
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage  # noqa: E402

from agents import build_researcher  # noqa: E402
from tools import close_client  # noqa: E402


def _print_step(msg) -> None:
    """把中间步骤打印出来，方便观察 Agent 在做什么。"""
    if isinstance(msg, AIMessage):
        for call in msg.tool_calls or []:
            args = ", ".join(f"{k}={v!r}" for k, v in call["args"].items())
            print(f"  🔧 调用工具 {call['name']}({args})")
    elif isinstance(msg, ToolMessage):
        text = str(msg.content).replace("\n", " ")
        preview = text[:160]
        print(f"  📦 返回 {preview}{'…' if len(text) > 160 else ''}")


async def run_once(agent, query: str, thread_id: str = "cli") -> str:
    print(f"\n👤 {query}\n" + "-" * 60)
    config = {"configurable": {"thread_id": thread_id}}
    answer = ""
    seen = 0
    async for chunk in agent.astream(
        {"messages": [HumanMessage(content=query)]},
        config=config,
        stream_mode="values",
    ):
        msgs = chunk.get("messages") or []
        # stream_mode="values" 每步都返回完整消息列表，只处理新增的
        for msg in msgs[seen:]:
            _print_step(msg)
        seen = len(msgs)
        if msgs and isinstance(msgs[-1], AIMessage) and msgs[-1].content and not msgs[-1].tool_calls:
            answer = msgs[-1].content
    print("-" * 60)
    return answer


async def main() -> None:
    agent = build_researcher()
    args = sys.argv[1:]
    try:
        if args:
            answer = await run_once(agent, " ".join(args))
            print(f"\n🤖 行者：\n\n{answer}\n")
        else:
            print("行者 v2 · 交互模式（输入 exit 退出）")
            while True:
                try:
                    query = input("\n👤 ").strip()
                except (EOFError, KeyboardInterrupt):
                    break
                if query.lower() in ("exit", "quit", "q"):
                    break
                if not query:
                    continue
                answer = await run_once(agent, query)
                print(f"\n🤖 行者：\n\n{answer}\n")
    finally:
        await close_client()


if __name__ == "__main__":
    asyncio.run(main())
