# 行者 v2 · 后端（M1）

「行者 · 智能旅游助手」的生产化版本。M1 目标：跑通
**用户自然语言 → LLM 选工具 → 调真实高德数据 → 汇总成可执行行程** 这条完整链路。

---

## 快速开始

```bash
cd v2/backend

# 1. 装依赖（国内建议用镜像）
pip install -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.txt

# 2. 配密钥
cp .env.example .env      # 然后填入 AMAP_SERVER_KEY / DEEPSEEK_API_KEY

# 3. 命令行跑一版行程
python cli.py "从上海出发，9月14日到成都玩3天，两个人，总预算3000元，喜欢美食和人文"

# 4. 或者起服务
python -m uvicorn main:app --host 127.0.0.1 --port 8000 --reload
```

---

## 目录结构

```
v2/backend/
├── .env                 # 密钥（不进 git）
├── .env.example         # 模板
├── config.py            # 配置加载 + 缺失校验
├── requirements.txt
├── cli.py               # 命令行调试入口
├── main.py              # FastAPI 服务
├── tools/
│   ├── __init__.py
│   └── amap.py          # 7 个高德工具 + 限流/重试/错误护栏
└── agents/
    ├── __init__.py
    └── researcher.py    # LangGraph ReAct Agent（M1 单 Agent）
```

---

## 7 个工具（tools/amap.py）

| 工具名 | 作用 | 关键参数 |
|---|---|---|
| `poi_search` | 按关键词搜景点/餐厅/酒店 | keywords, city, types, limit |
| `poi_around` | 按坐标搜周边 | location, keywords, radius |
| `poi_detail` | 单个 POI 详情（营业时间/评分/人均） | poi_id |
| `weather_now` | 实时天气 | city |
| `weather_forecast` | 未来 1-4 天预报 | city, days |
| `geo_geocode` | 地址 → 经纬度（设施名自动回落到 POI 搜索） | address, city |
| `route_plan` | 路径规划（驾车/步行/骑行/公交） | origin, destination, mode, city |

**返回的都是裁剪过的紧凑 JSON**，只保留 LLM 排行程需要的字段（名称/坐标/评分/营业时间/人均），避免把高德的冗长响应烧进上下文。

---

## 工程护栏（踩过的坑）

### 1. 高德 QPS 限制 → 串行限流 + 退避重试

个人开发者 Key 约 3 QPS。LangGraph 的 ToolNode 会**并行**执行同一轮里的多个工具调用，
一次发 5 个请求直接触发 `10021 CUQPS_HAS_EXCEEDED_THE_LIMIT`。

解决（`tools/amap.py`）：
- 全局 `asyncio.Lock` 串行化 + 最小间隔 `AMAP_MIN_INTERVAL`（默认 0.4s ≈ 2.5 QPS）
- 命中限流码（10003/10019/10020/10021/10022）时退避重试，最多 `AMAP_MAX_RETRY` 次

### 2. 工具报错会崩掉整个图 → `@resilient` 护栏

工具抛异常时 LangGraph 默认直接中断整个 Agent。用 `@resilient` 装饰器把异常
转成 `{"error": ..., "hint": ...}` 返回给 LLM，LLM 看到后能自己换关键词重试。

> 注意装饰器顺序：`@tool` 必须在 `@resilient` **上面**（`functools.wraps` 保留了
> 签名和 docstring，所以 `@tool` 仍能正确生成参数 schema）。

### 3. 步行/驾车没有距离耗时 → 字段在 `paths[0]` 里

高德 `/v3/direction/walking` 的 `distance` / `duration` 不在 `route` 顶层，
而在 `route.paths[0]` 里。公交模式才是顶层 `route.distance` + `route.transits[]`。

### 4. 地理编码只到市级 → POI 搜索兜底

`GeoJSON` 解「成都东站」这类设施名时可能只返回「四川省成都市」。
现在 `geo_geocode` 和 `route_plan` 内部都会检查 `level`，若为 省/市/区县
则回落到 `place/text` 拿精确坐标。

---

## API 契约（给前端）

### `GET /health`
```json
{"status": "ok", "amap_key_configured": true, "llm_configured": true, "model": "deepseek-v4-pro"}
```

### `POST /api/chat`
```json
// 请求
{"message": "从上海去成都玩3天，喜欢美食", "thread_id": "可选，不传自动生成"}

// 响应
{
  "thread_id": "abc123",
  "answer": "### 行程概览 ...",
  "steps": [
    {"type": "tool_call",   "tool": "poi_search", "args": {"keywords": "宽窄巷子", "city": "成都"}},
    {"type": "tool_result", "tool": "poi_search", "content": "{\"count\":3,...}"}
  ]
}
```

`thread_id` 相同即视为同一会话（Agent 用 InMemorySaver 保存上下文），
支持「帮我改成 4 天」这类追问。

### `POST /api/chat/stream`（SSE）
前端 Step 面板实时展示 Agent 的工具调用过程：

```
event: start      data: {"thread_id": "..."}
event: step       data: {"type": "tool_call", "tool": "weather_forecast", "args": {...}}
event: step       data: {"type": "tool_result", "tool": "weather_forecast", "content": "..."}
event: review     data: {"passed": true, "issue_count": 5, "rounds": 2, "revisions": 1, "new_places": []}
event: plan_json  data: {"title": "...", "days": [...], "budget": [...]}
event: token      data: {"content": "### 行程概览 ..."}
event: done       data: {"thread_id": "..."}
event: error      data: {"message": "..."}
```

---

## M3 多 Agent 流水线（当前形态）

```
Researcher（ReAct + 高德工具，产出 Markdown 初版）
   ↓
Critic（结构化审查报告 ReviewReport：weather/budget/time/geo 四维）
   ↓ passed=false 且未达轮次上限
Reviser（按问题清单修订 Markdown，不调工具）
   ↓
Critic 复审（最多 MAX_REVIEW_ROUNDS=2 轮，第 2 轮无论通过与否都放行）
   ↓
Planner（结构化输出 TripPlan，失败自动重试一次）
   ↓
Markdown + plan_json + review 三路输出
```

### 抗幻觉：修订内容的地点白名单

Reviser 只允许「调序 / 删减 / 重分配时间」，不允许引入初版没有的 POI。
实测提示词层的「绝对禁止」在极端输入下守不住（若初版多个地点是硬伤，
删完后一天排不满，模型就会自行补著名景点）。因此加了**程序化兜底**：

- `build_whitelist_checker()`：flash + `WhitelistCheck` 结构化输出，对比初版/修订版列出新地点
- 检出违规时，在答案末尾自动追加「**修订审计**」段，并在 SSE `review.new_places` 里透传，
  前端审查卡以黄色横幅展示（提示用户这些地点未经工具数据核实）
- 代价：只在真正发生修订时才多一次 flash 调用

### 关键踩坑（M3 新增）

- `with_structured_output` 必须用 `method="function_calling"`，DeepSeek 的
  `response_format=json_schema` 不可用
- 该账号所有模型默认开 thinking，与 function calling 冲突 →
  必须 `extra_body={"thinking": {"type": "disabled"}}`
- Critic 的判定规则要写成「**只要存在任一 high 问题，passed 必须为 false**」；
  原措辞「只存在 high 时 passed=false」有歧义，模型会带 high 问题放行
- Planner 结构化输出偶发失败（超时/JSON 截断），已在 `_run_planner` 内加一次重试

---

## 已验证效果

输入：`从上海出发，9月14日到成都玩3天，两个人，总预算3000元，喜欢美食和人文，住经济型酒店`

Agent 自主完成了：
1. 查成都 9/14-9/16 天气 → 发现 15、16 日有阵雨
2. 搜索宽窄巷子/武侯祠/锦里/杜甫草堂/文殊院/大慈寺等景点并查详情（评分、开放时间、门票）
3. 搜索火锅/串串/钟水饺/建设路小吃街等餐饮
4. 搜索春熙路一带经济型酒店并比价
5. 用 `route_plan` 算成都东站→春熙路、武侯祠→杜甫草堂等实际交通耗时
6. **因雨天把博物馆/寺庙等室内行程排到 15、16 日，户外集中到 14 日**
7. 输出带表格的分日行程 + 交通建议 + 分项预算 + 温馨提示
8. 主动指出「3000 元若含往返大交通会很紧张」并给了两套方案

---

## M1 已知限制（M2/M3 要解决）

| 限制 | 计划 |
|---|---|
| 行程只是 Markdown 文本，没有结构化 JSON | ✅ M3 已解决：Planner 输出 TripPlan |
| 前端拿不到真实流式 token | M2 改 `stream_mode="messages"` |
| 单 Agent 串行跑 20+ 次工具，耗时约 100 秒 | 部分改善：Critic 用 flash，可进一步并行/缓存 |
| 没有 Critic 审查（行程合理性/疲劳度） | ✅ M3 已解决：Critic ↔ Reviser 闭环 + 前端审查卡 |
| 记忆只在内存，重启丢失 | M2 换 SQLite checkpointer（未做） |
| 无埋点统计（turns/latency/cost） | M5 加可观测性 |
