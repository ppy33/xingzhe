# -*- coding: utf-8 -*-
"""离线复测：Reviser 地点白名单约束（不调工具，纯 Critic/Reviser 链路）

场景与上轮相同的刁钻坏行程：颐和园排雨天 + 都江堰跨城 + 故宫周一 + 预算矛盾。
验证点：
1. Critic 第 1 轮仍能抓出 high 问题
2. Reviser 修订后的文本中，所有地点名都能在初版里找到（地点白名单）
"""
import asyncio
import re
import sys

sys.path.insert(0, ".")

from agents.critic import build_critic, build_reviser, build_whitelist_checker, check_new_places, review_report, revise_plan

USER_QUERY = "9月14日到成都玩1天，两人，总预算500元，喜欢人文景点，住经济型酒店"

BAD_DRAFT = """# 成都 1 日行程

## 行程概览

| 日期 | 天气 | 主题 |
|---|---|---|
| 9月14日（周一） | 中雨 | 人文一日游 |

## 每日安排

### Day 1（9月14日 周一 · 中雨）

| 时间 | 安排 | 说明 |
|---|---|---|
| 09:00-12:00 | 颐和园 | 雨中游园，昆明湖泛舟 |
| 12:00-13:30 | 都江堰 | 跨城往返，游览水利工程 |
| 14:00-16:00 | 故宫博物院（北京） | 周一前往参观珍宝馆 |
| 17:00-19:00 | 武侯祠 | 雨中漫步锦里古街 |

## 交通建议

- 颐和园 → 都江堰：打车约 2 小时
- 都江堰 → 故宫：高铁 + 地铁

## 预算估算

| 项目 | 金额（2人） |
|---|---|
| 门票 | 300 元 |
| 餐饮 | 400 元 |
| 市内交通 | 100 元 |
| 都江堰往返高铁 | 300 元 |
| **合计** | **1100 元** |

## 温馨提示

- 雨天记得带伞
"""

# 已知地点池：初版出现过的 + 上轮 Reviser 违规引入过的（北海/天坛）
KNOWN_PLACES = ["颐和园", "都江堰", "故宫", "武侯祠", "锦里"]
VIOLATION_WATCH = ["北海", "天坛", "圆明园", "颐和", "金沙", "杜甫草堂", "宽窄巷子", "文殊院", "青城山", "人民公园", "大熊猫", "春熙路", "太古里", "环球中心", "四川省博物馆", "成都博物馆"]


def extract_places(text: str) -> list[str]:
    found = []
    for p in VIOLATION_WATCH + KNOWN_PLACES:
        if p in text and p not in found:
            found.append(p)
    return found


async def main():
    critic = build_critic()
    reviser = build_reviser()

    print("=" * 60)
    print("Round 1: Critic 审查坏行程")
    r1 = await review_report(critic, USER_QUERY, BAD_DRAFT)
    print(f"passed={r1.passed}, issues={len(r1.issues)}")
    for it in r1.issues:
        print(f"  [{it.dimension}/{it.severity}] {it.description[:80]}")

    if r1.passed:
        print("!! Critic 未判定不通过，测试前提不成立")
        return

    print("=" * 60)
    print("Reviser 修订（地点白名单约束）")
    revised = await revise_plan(reviser, USER_QUERY, BAD_DRAFT, r1)

    print("=" * 60)
    print("白名单检查（新地点必须带 ※修订新增 标注）")
    bad_places, marked_places = [], []
    for p in VIOLATION_WATCH:
        if p in revised and p not in BAD_DRAFT:
            if "※修订新增" in revised:
                marked_places.append(p)
            else:
                bad_places.append(p)
    known_kept = [p for p in KNOWN_PLACES if p in revised]
    print(f"初版地点保留: {known_kept}")
    if marked_places:
        print(f"已标注的新增地点（合规）: {marked_places}")
    if bad_places:
        print(f"!! 未标注的违规新地点: {bad_places}")
    else:
        print("OK 无未标注的违规新地点")

    print("=" * 60)
    print("程序化白名单核验（flash 兜底，生产链路同款）")
    checker = build_whitelist_checker()
    detected = await check_new_places(checker, BAD_DRAFT, revised)
    print(f"核验检出新地点: {detected or '（无）'}")
    if detected:
        print("OK 程序化核验能兜住提示词守不住的违规（生产链路会自动追加审计说明）")

    print("=" * 60)
    print("Round 2: 复审修订稿")
    r2 = await review_report(critic, USER_QUERY, revised)
    print(f"passed={r2.passed}, issues={len(r2.issues)}")
    for it in r2.issues:
        print(f"  [{it.dimension}/{it.severity}] {it.description[:80]}")

    print("=" * 60)
    print("修订稿全文（人工抽查用）")
    print(revised)


if __name__ == "__main__":
    asyncio.run(main())
