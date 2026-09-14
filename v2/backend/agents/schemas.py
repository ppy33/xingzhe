"""M3 结构化行程的 Pydantic Schema。

设计目标：
- 与 Markdown 行程一一对应（天/项/地点/预算/提示），但机器可解析
- 前端可拿来驱动拖动排序、预算实时计算、单日地图聚焦等交互
- 字段尽量从 Researcher 已经查到的工具结果里抽，不强求新增
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class PlaceRef(BaseModel):
    """一个地点的引用。location 是 'lng,lat' 字符串，方便地图直接用。"""

    name: str = Field(..., description="地点名称（必须来自工具返回）")
    category: str = Field(
        default="other",
        description="sight/food/stay/transit/other",
    )
    address: str = Field(default="", description="地址")
    location: str = Field(default="", description="经纬度 'lng,lat'")
    open_time: str = Field(default="", description="营业时间原文")
    cost: Optional[float] = Field(default=None, description="人均花费（元）")
    rating: str = Field(default="", description="评分")


class DayItem(BaseModel):
    """一天里的一项安排。"""

    time: str = Field(..., description="开始时间，HH:MM 格式")
    title: str = Field(..., description="一句话标题，如 '天安门广场'")
    place: Optional[PlaceRef] = Field(default=None, description="关联地点（可空，例如交通段）")
    duration_min: Optional[int] = Field(default=None, description="预计停留分钟数")
    notes: str = Field(default="", description="备注 / 玩法 / 注意事项")
    transport_to_next: str = Field(default="", description="到下一项的交通方式与耗时")


class Day(BaseModel):
    """一天的安排。"""

    date: str = Field(..., description="YYYY-MM-DD")
    weekday: str = Field(default="", description="周几，如 '二'")
    weather: str = Field(default="", description="白天天气 + 温度，如 '晴 17~28℃'")
    theme: str = Field(default="", description="当天主题一句话")
    items: list[DayItem] = Field(default_factory=list)
    summary: str = Field(default="", description="当天小结 1-2 句")


class BudgetItem(BaseModel):
    category: str = Field(..., description="分项名，如 '往返高铁'")
    amount: str = Field(..., description="金额字符串，如 '1800 元/人'")


class ReviewIssue(BaseModel):
    """Critic 发现的一个问题。"""

    dimension: str = Field(..., description="问题维度：weather/budget/time/geo/other")
    severity: str = Field(default="medium", description="严重程度：low/medium/high")
    description: str = Field(..., description="问题描述，一句话说清哪里有问题")
    suggestion: str = Field(default="", description="修改建议，可直接执行")


class ReviewReport(BaseModel):
    """Critic 的结构化审查报告。"""

    passed: bool = Field(..., description="是否通过审查（无 high 严重度问题即通过）")
    issues: list[ReviewIssue] = Field(default_factory=list, description="发现的问题列表")
    summary: str = Field(default="", description="一句话总评")


class WhitelistCheck(BaseModel):
    """Reviser 修订稿的白名单核验结果（程序化兜底，不靠提示词自觉）。"""

    new_places: list[str] = Field(
        default_factory=list,
        description="修订稿中出现、但初版文本中没有的景点/餐厅/酒店名；全部合规时为空列表",
    )


class TripPlan(BaseModel):
    """完整行程结构化对象。"""

    title: str = Field(..., description="行程标题，如 '上海 → 成都 3 天 2 晚'")
    overview: str = Field(default="", description="行程总览，2-4 句")
    days: list[Day] = Field(default_factory=list)
    transportation: str = Field(default="", description="交通建议整段")
    budget: list[BudgetItem] = Field(default_factory=list)
    tips: list[str] = Field(default_factory=list, description="温馨提示列表")

    def to_markdown(self) -> str:
        """把结构化对象渲染成 Markdown，用于前端显示。"""
        lines: list[str] = [f"# {self.title}", ""]
        if self.overview:
            lines += [self.overview, ""]
        # 概览表
        if self.days:
            lines += [
                "## 行程概览",
                "| 天数 | 日期 | 天气 | 主题 |",
                "|---|---|---|---|",
            ]
            for i, d in enumerate(self.days, 1):
                lines.append(
                    f"| Day {i} | {d.date}（{d.weekday}） | {d.weather} | {d.theme} |"
                )
            lines.append("")

        # 每天
        for i, d in enumerate(self.days, 1):
            head = f"## Day {i} · {d.date}（{d.weekday}） · {d.weather}"
            lines += [head, ""]
            if d.theme:
                lines += [f"> {d.theme}", ""]
            if d.items:
                lines += [
                    "| 时间 | 安排 | 说明 | 交通 |",
                    "|---|---|---|---|",
                ]
                for it in d.items:
                    place_name = it.place.name if it.place else "—"
                    cell_title = f"**{place_name}** · {it.title}" if place_name != it.title else it.title
                    lines.append(
                        f"| {it.time} | {cell_title} | {it.notes or '—'} | {it.transport_to_next or '—'} |"
                    )
                lines.append("")
            if d.summary:
                lines += [f"**小结**：{d.summary}", ""]

        if self.transportation:
            lines += ["## 交通建议", self.transportation, ""]

        if self.budget:
            lines += [
                "## 预算估算",
                "| 项目 | 人均费用 |",
                "|---|---|",
            ]
            for b in self.budget:
                lines.append(f"| {b.category} | {b.amount} |")
            lines.append("")

        if self.tips:
            lines.append("## 温馨提示")
            for t in self.tips:
                lines.append(f"- {t}")

        return "\n".join(lines).rstrip() + "\n"