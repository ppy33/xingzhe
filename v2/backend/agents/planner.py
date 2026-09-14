"""Planner Agent —— M3 第二棒（结构化输出）

职责：
- 接收 Researcher 产出的初版 Markdown 行程 + 用户原始需求
- 输出 TripPlan 结构化对象（Pydantic 模型）
- 再用 to_markdown() 渲染回 Markdown，前端两路都能用

设计取舍：
- 用 deepseek-v4-pro（主模型）保结构稳定；temperature=0.1
- 强制 `thinking=disabled`：DeepSeek 该账号所有模型默认开 thinking，
  thinking 模式不支持 function_calling，必须关。
- 一次输出 Pydantic JSON 比让 LLM 拼 Markdown 更可控
"""
from __future__ import annotations

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from agents.schemas import TripPlan
from config import settings

PLANNER_SYSTEM_PROMPT = """你是「行者」系统的 Planner（结构化规划官）。
Researcher 已通过工具拿到了景点、天气、交通等真实数据，并产出了一份 Markdown 初版行程。
你的任务是**严格按 Researcher 的初版**把它改写成 TripPlan 结构化对象。

## 严格约束
1. **绝不新增 Researcher 没有的信息**：地点名、营业时间、票价、坐标等都必须从初版里来。
2. **绝不删减 Researcher 写出的主要安排**：每天的核心景点、餐饮、住宿都要保留。
3. **地点分类 category 必须用枚举**：`sight`(景点) / `food`(餐饮) / `stay`(住宿) / `transit`(交通) / `other`(其他)。
4. **时间用 HH:MM 24 小时制**；如果初版写的是 "上午" 这种模糊词，按 09:00 / 14:00 这种合理值估一个，并 notes 里写明。
5. **place.location 必须是初版里逐字复制过来的 'lng,lat' 字符串**：
   - 初版里出现过该地点的坐标 → 原样复制，不要改数字、不要"估一个"。
   - 初版里没给坐标 → **留空字符串**。**绝对不许自己编造坐标**——曾有版本把锦里写到河北、宽窄巷子写到北京顺义，直接毁掉了地图标记与路线优化。
   - 宁可留空：下游会用地点名去高德重新查真实坐标。
6. **预算每项一个对象**：category 用初版里的项目名，amount 保留原表述（如 "1800 元/人"）。
7. **tips 列表**：每条一个完整短句，不要用换行。

## 输出
- **必须调用提供的 `TripPlan` 函数**返回结构化对象，不要直接输出 JSON 文本或 Markdown。
- 不要写"以下是修改版"等解释。
"""


def build_planner(model: str | None = None, temperature: float = 0.1):
    """返回一个绑定了 TripPlan 结构化输出的 ChatOpenAI。

    - 用 `function_calling` 而非 `json_schema`：DeepSeek 的 `response_format=json_schema`
      暂不可用（API 报 `This response_format type is unavailable now`）。
    - **强制 `thinking=disabled`**：DeepSeek 该账号所有模型默认开 thinking，
      thinking 模式不支持 function_calling / tool_choice（`Thinking mode does not
      support this tool_choice`），必须通过 `extra_body` 关掉才能让结构化工具调用跑通。
    """
    settings.require_llm()
    llm = ChatOpenAI(
        model=model or settings.deepseek_model,  # 默认用主力模型 pro
        api_key=settings.deepseek_api_key,
        base_url=settings.deepseek_base_url,
        temperature=temperature,
        timeout=90,
        max_retries=2,
        extra_body={"thinking": {"type": "disabled"}},
    )
    return llm.with_structured_output(TripPlan, method="function_calling")


async def plan_to_struct(llm, user_query: str, draft_answer: str, remind: bool = False) -> TripPlan:
    """把 Researcher 初版 Markdown 转成 TripPlan。失败时抛异常，调用方兜底。

    remind=True 时追加一段强提醒：模型偶尔会用自然语言作答而不调用工具，
    这时 `with_structured_output` 会返回 None（实测过），加提醒能显著提高重试成功率。
    """
    messages = [
        SystemMessage(content=PLANNER_SYSTEM_PROMPT),
        HumanMessage(
            content=(
                f"## 用户原始需求\n{user_query}\n\n"
                f"## Researcher 初版行程（Markdown）\n{draft_answer}"
                + (
                    "\n\n## 重要提醒\n"
                    "上一次你没有调用工具。这一次**必须调用 `TripPlan` 函数**返回结构化对象，"
                    "不要输出任何自然语言说明或 Markdown。"
                    if remind
                    else ""
                )
            )
        ),
    ]
    plan = await llm.ainvoke(messages)
    if plan is None:
        # 模型没调工具、直接回了文本时，LangChain 会给出 None
        raise RuntimeError("Planner 未返回结构化结果（模型没有调用 TripPlan 工具）")
    return plan