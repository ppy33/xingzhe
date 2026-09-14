# -*- coding: utf-8 -*-
"""M6 场景回归套件：跑真实请求，产出评估报告。

用法：
    python run_eval.py            # 跑 scenarios/scenarios.yaml 里的全部场景
    python run_eval.py normal     # 只跑指定场景 id

产出：
    - 控制台实时进度
    - docs/eval_report.md（评估报告，含指标表）

注意：
    - 会消耗 LLM + 高德配额，每个场景约 60–120 秒，请手动触发，不要进 CI
    - 评估请求写入独立库 data/eval.db（不污染真实 data/xingzhe.db）
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

# 评估专用库，隔离真实数据（必须在 import main 之前设置，config 在 import 时读环境变量）
os.environ["DB_PATH"] = "data/eval.db"

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import yaml  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402

SCENARIOS_PATH = Path(__file__).parent / "scenarios" / "scenarios.yaml"
REPORT_PATH = Path(__file__).parent.parent / "docs" / "eval_report.md"


def run_scenario(client: TestClient, sc: dict) -> dict:
    """跑一个场景，返回结构化指标。"""
    t0 = time.perf_counter()
    resp = client.post(
        "/api/chat",
        json={"message": sc["query"], "thread_id": sc["id"]},
    )
    elapsed_ms = int((time.perf_counter() - t0) * 1000)

    if resp.status_code != 200:
        return {
            "id": sc["id"],
            "name": sc["name"],
            "elapsed_ms": elapsed_ms,
            "error": f"HTTP {resp.status_code}",
            "tool_calls": 0,
            "plan_json": False,
            "review_passed": None,
            "review_issues": None,
            "optimize_saving": None,
        }

    data = resp.json()
    steps = data.get("steps", [])
    tool_calls = sum(1 for s in steps if s.get("type") == "tool_call")
    review = data.get("review") or {}
    optimize = data.get("optimize") or {}

    return {
        "id": sc["id"],
        "name": sc["name"],
        "elapsed_ms": elapsed_ms,
        "tool_calls": tool_calls,
        "plan_json": bool(data.get("plan_json")),
        "review_passed": review.get("passed"),
        "review_issues": review.get("issue_count"),
        "optimize_saving": optimize.get("saving_pct"),
        "error": None,
    }


def render_report(results: list[dict]) -> str:
    """把结果渲染成 markdown 评估报告。"""
    ok = [r for r in results if not r["error"]]
    err = [r for r in results if r["error"]]

    lines: list[str] = [
        "# 行者 v2 · 场景回归评估报告",
        "",
        f"> 生成时间：{time.strftime('%Y-%m-%d %H:%M:%S')} ｜ 场景数：{len(results)}",
        "",
        "## 指标总览",
        "",
        "| 指标 | 值 |",
        "|---|---|",
    ]
    if ok:
        avg_ms = sum(r["elapsed_ms"] for r in ok) / len(ok)
        avg_tools = sum(r["tool_calls"] for r in ok) / len(ok)
        plan_ok = sum(1 for r in ok if r["plan_json"])
        reviewed = [r for r in ok if r["review_passed"] is not None]
        hard_fail = [r for r in reviewed if r["review_passed"] is False]
        opt = [r for r in ok if r["optimize_saving"] is not None]
        opt_applied = [r for r in opt if (r["optimize_saving"] or 0) > 0]

        lines += [
            f"| 场景通过率 | {len(ok)}/{len(results)}（错误 {len(err)}） |",
            f"| 平均端到端耗时 | {avg_ms/1000:.1f} 秒 |",
            f"| 平均工具调用数 | {avg_tools:.1f} |",
            f"| 结构化行程产出率 | {plan_ok}/{len(ok)} |",
            f"| Critic 硬伤拦截率 | {len(hard_fail)}/{len(reviewed)}（passed=false 即拦截） |",
            f"| 路线优化生效率 | {len(opt_applied)}/{len(opt)}（saving>0） |",
            "",
        ]

    lines += ["## 逐场景明细", "", "| 场景 | 耗时(s) | 工具数 | plan_json | review.passed | issues | 优化降幅 |", "|---|---|---|---|---|---|---|"]
    for r in results:
        if r["error"]:
            lines.append(f"| {r['name']} | {r['elapsed_ms']/1000:.1f} | — | — | — | — | **{r['error']}** |")
        else:
            saving = f"{r['optimize_saving']:.1f}%" if r["optimize_saving"] is not None else "—"
            passed = "✓" if r["review_passed"] else ("✕" if r["review_passed"] is False else "—")
            lines.append(
                f"| {r['name']} | {r['elapsed_ms']/1000:.1f} | {r['tool_calls']} | "
                f"{'✓' if r['plan_json'] else '✕'} | {passed} | {r['review_issues'] if r['review_issues'] is not None else '—'} | {saving} |"
            )

    lines += ["", "## 说明", "", "- 评估请求写入独立库 `data/eval.db`，不污染真实数据", "- 跑分会消耗 LLM + 高德配额，建议在需要展示「评估体系」时再跑", ""]
    return "\n".join(lines)


def main_run(target: str | None = None) -> None:
    raw = yaml.safe_load(SCENARIOS_PATH.read_text(encoding="utf-8"))
    scenarios: list[dict] = raw["scenarios"]
    if target:
        scenarios = [s for s in scenarios if s["id"] == target]
        if not scenarios:
            print(f"未找到场景 id={target}")
            sys.exit(1)

    results: list[dict] = []
    with TestClient(main.app) as client:
        for sc in scenarios:
            print(f"[{sc['id']}] {sc['name']} …", flush=True)
            r = run_scenario(client, sc)
            results.append(r)
            if r["error"]:
                print(f"  ✕ {r['error']}")
            else:
                print(
                    f"  ✓ {r['elapsed_ms']/1000:.1f}s · {r['tool_calls']} 工具 · "
                    f"plan_json={'有' if r['plan_json'] else '无'} · "
                    f"review.passed={r['review_passed']} · 优化降幅={r['optimize_saving']}"
                )

    report = render_report(results)
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(report, encoding="utf-8")
    print(f"\n报告已写入：{REPORT_PATH}")


if __name__ == "__main__":
    main_run(sys.argv[1] if len(sys.argv) > 1 else None)
