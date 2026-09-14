"""Critic Agent —— M3 深入版（审查 + 修订闭环）

流程：
- review_report()：结构化审查报告 ReviewReport（4 维度：天气/预算/时间/地理）
  - passed=True  → 直接进 Planner
  - passed=False → 交给 revise_plan() 带意见修订，然后复审（最多 MAX_REVIEW_ROUNDS 轮）
- revise_plan()：拿初版 + 问题清单，输出修订后的 Markdown（不调工具，纯文本重排）

设计取舍：
- 审查用 deepseek-flash（便宜快），修订用主模型 pro（质量优先）
- 两者都强制 thinking=disabled：该账号默认开 thinking，与 function calling 冲突
- 修订轮不调工具：Researcher 已拿到全部真实数据，修订只做重排/文案/预算校准，
  不允许新增 POI，避免编造
"""
from __future__ import annotations

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import ValidationError

from agents.schemas import ReviewReport, WhitelistCheck
from config import settings

CRITIC_SYSTEM_PROMPT = """你是「行者」系统的 Critic（审查官）。Researcher 已生成一份初版行程 Markdown，你的任务是**审查**它并输出结构化审查报告。

## 审查 4 个维度（按顺序检查）
1. **weather 天气冲突**：户外景点是否被排在雨天？雨天的项目是否安排在室内？每天的天气描述与该天安排是否一致？
2. **budget 预算可行性**：分项相加是否在用户预算内？往返大交通是否被正确计入/排除总预算？
3. **time 时间冲突**：
   - 景点营业时间是否覆盖排定的时间段？（博物馆/寺庙常 17:00 关门，周一闭馆）
   - 同一天景点之间是否有足够交通时间？是否出现"上午 9 点 A、10 点 30 公里外 B"的赶场？
   - 用餐时间是否卡在餐厅营业时间内？
4. **geo 地理顺路**：同一天的地点是否大致同片区？是否出现跨城/跨半个城的跳跃？

## 判定规则
- **passed 必须严格按规则**：只要 issues 里存在任何一个 severity=high 的问题，passed 就必须是 false（哪怕该问题被标注为"风险"或"需核实"）。只有当所有 high 问题都已解决、剩余均为 low/medium 时才 passed=true。
- 无法核实的信息（初版没给营业时间等）不要当成问题，最多标 low。
- issues 可以为空列表；summary 一句话总评。

## 输出
必须调用提供的 `ReviewReport` 函数返回结构化对象，不要输出 Markdown。
"""

CRITIC_REVISION_PROMPT = """你是「行者」系统的 Reviser（修订官）。Researcher 的初版行程经 Critic 审查后发现了问题，你的任务是**修复这些问题**并输出修订后的完整 Markdown。

## 严格约束（违反任何一条都是失败）
1. **只修复 Critic 指出的问题**，其余内容原样保留（表格结构、emoji、措辞都不要动）。
2. **地点白名单（优先级最高）**：输出中出现的每一个景点/餐厅/酒店名，必须优先在初版文本里能找到原文。修复手段只用三种：
   - 调整顺序/时间段
   - 删减或降级为"可选"
   - 在初版已有的地点之间重新分配时间
   **唯一的例外**：若按问题清单删掉硬伤地点后（如跨城错误、闭馆），当天剩余的初版地点已不足以构成合理行程（少于半天），允许补充**该城市最知名的少量**替代地点，但每个新增地点必须在该行说明末尾标注 `※修订新增`，供系统审计。绝不允许无标注地引入新地点。
3. 修复时间冲突用「调整顺序/时间段/删减可选项目」，不要编造新景点。
4. 修复地理跳跃用「把跨城/跨片区的项移到别的天或删掉」，不要换成新的"更近"地点。
5. 预算问题用调整分项数字/备注的方式修复，并在该行末尾加 `<!-- fix: 理由 -->` 注释（用户不可见）。
6. 保持初版的 Markdown 结构：行程概览表 / 每天表格 / 交通建议 / 预算估算 / 温馨提示。
7. 全文中文，直接输出修订后的 Markdown 全文，不要写"以下是修订版"等解释。
"""


WHITELIST_CHECK_PROMPT = """你是行程修订的审计员。Reviser 被要求只修复问题、不引入初版没有的新地点，但模型可能违规。请对比初版和修订版，找出修订版中出现、而初版中完全没有的**具体地点名**（景点/博物馆/餐厅/酒店，不含城市名、行政区名、街道泛称如"古街"）。

规则：
- 初版里已有（哪怕只在交通建议里提到）的地点不算新地点。
- 城市名（成都/北京）、行政区（武侯区）、泛称（市区、古镇）不算。
- 拿不准的不算。宁可漏报不要误报。
- 全部合规就返回空列表。
"""


def build_whitelist_checker(model: str | None = None):
    """构造白名单核验器：flash + WhitelistCheck 结构化输出。"""
    settings.require_llm()
    llm = ChatOpenAI(
        model=model or settings.deepseek_model_fast,
        api_key=settings.deepseek_api_key,
        base_url=settings.deepseek_base_url,
        temperature=0,
        timeout=60,
        max_retries=2,
        extra_body={"thinking": {"type": "disabled"}},
    )
    return llm.with_structured_output(WhitelistCheck, method="function_calling")


async def check_new_places(llm, original: str, revised: str) -> list[str]:
    """核验修订稿是否引入了初版没有的地点。返回新地点名列表（空=合规）。

    这是程序化兜底：提示词层的白名单约束在极端输入下守不住，
    用一次便宜的 flash 调用做最终把关，调用方负责对违规结果打审计标记。
    """
    messages = [
        SystemMessage(content=WHITELIST_CHECK_PROMPT),
        HumanMessage(
            content=(
                f"## 初版行程\n{original}\n\n## 修订版行程\n{revised}"
            )
        ),
    ]
    result = await llm.ainvoke(messages)
    return result.new_places or []


def build_critic(model: str | None = None, temperature: float = 0.1):
    """构造 Critic：绑定 ReviewReport 结构化输出的 flash。"""
    settings.require_llm()
    llm = ChatOpenAI(
        model=model or settings.deepseek_model_fast,
        api_key=settings.deepseek_api_key,
        base_url=settings.deepseek_base_url,
        temperature=temperature,
        timeout=90,
        max_retries=2,
        extra_body={"thinking": {"type": "disabled"}},
    )
    return llm.with_structured_output(ReviewReport, method="function_calling")


def build_reviser(model: str | None = None, temperature: float = 0.2):
    """构造 Reviser：主模型 pro，纯文本输出（不调工具不结构化）。"""
    settings.require_llm()
    return ChatOpenAI(
        model=model or settings.deepseek_model,
        api_key=settings.deepseek_api_key,
        base_url=settings.deepseek_base_url,
        temperature=temperature,
        timeout=120,
        max_retries=2,
        extra_body={"thinking": {"type": "disabled"}},
    )


async def review_report(llm, user_query: str, draft_answer: str) -> ReviewReport:
    """让 Critic 审查行程，返回结构化报告。失败时抛异常，调用方兜底。"""
    messages = [
        SystemMessage(content=CRITIC_SYSTEM_PROMPT),
        HumanMessage(
            content=(
                f"## 用户原始需求\n{user_query}\n\n"
                f"## 待审查行程（Markdown）\n{draft_answer}"
            )
        ),
    ]
    return await llm.ainvoke(messages)


async def revise_plan(llm, user_query: str, draft_answer: str, report: ReviewReport) -> str:
    """按审查报告修订行程，返回修订后的 Markdown。失败时抛异常。"""
    issues_text = "\n".join(
        f"{i + 1}. [{issue.dimension}/{issue.severity}] {issue.description}"
        + (f"（建议：{issue.suggestion}）" if issue.suggestion else "")
        for i, issue in enumerate(report.issues)
    )
    messages = [
        SystemMessage(content=CRITIC_REVISION_PROMPT),
        HumanMessage(
            content=(
                f"## 用户原始需求\n{user_query}\n\n"
                f"## Critic 发现的问题\n{issues_text or '（无具体问题，仅微调）'}\n\n"
                f"## 待修订行程（Markdown）\n{draft_answer}"
            )
        ),
    ]
    resp = await llm.ainvoke(messages)
    return (resp.content or "").strip()


def report_brief(report: ReviewReport) -> dict:
    """把报告压成给前端 step 用的紧凑 dict。"""
    return {
        "passed": report.passed,
        "issue_count": len(report.issues),
        "summary": (report.summary or "")[:120],
        "issues": [
            {
                "dimension": it.dimension,
                "severity": it.severity,
                "description": it.description[:100],
            }
            for it in report.issues[:6]
        ],
    }


__all__ = [
    "build_critic",
    "build_reviser",
    "build_whitelist_checker",
    "review_report",
    "revise_plan",
    "check_new_places",
    "report_brief",
]
