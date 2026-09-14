# 行者 v2 · 后端（M5）

「行者 · 智能旅游助手」的生产化版本。当前已跑通
**自然语言 → 多 Agent 调研/审查/结构化 → 运筹优化 → 主动感知体检** 全链路。

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
  "plan_json": {"title": "...", "days": [...], "budget": [...]},
  "review": {"passed": true, "rounds": 2, "revisions": 1, "issues": [...]},
  "optimize": {
    "applied": true, "solver": "ortools-tspTW", "solve_ms": 4632,
    "before_min": 80.9, "after_min": 72.5, "saving_pct": 10.3,
    "before_km": 9.0, "after_km": 8.0,
    "days": [
      {"date": "2026-09-14", "points": 7, "applied": true, "mode": "strict",
       "order_before": ["武侯祠", "锦里", "..."], "order_after": ["武侯祠", "锦里小吃街", "..."],
       "saving_pct": 10.3,
       "geo": {"city": "成都市", "from_tools": ["武侯祠", "锦里"], "regenerated": [], "outliers": []}}
    ]
  },
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
event: optimize   data: {"applied": true, "before_min": 80.9, "after_min": 72.5, "saving_pct": 10.3, "days": [...]}
event: token      data: {"content": "### 行程概览 ..."}
event: done       data: {"thread_id": "..."}
event: error      data: {"message": "..."}
```

事件顺序：`start` → 若干 `step`（Researcher 工具）→ 若干 `step`（critic_review / revise_plan）
→ `review` → `step`（planner_struct）→ `step`（optimizer_route）→ `optimize` → `plan_json`
→ `token` → `done`。

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
Planner（结构化输出 TripPlan，失败自动重试最多 3 次，后两次带强提醒）
   ↓
Optimizer（OR-Tools VRPTW 单日最优排序 + 坐标体检，M4）
   ↓
Markdown + plan_json + review + optimize 四路输出
```

行程生成后，还可对结果做 **M5 主动感知体检**（独立接口，不塞进主链）：

```
POST /api/trip/check（或 /stream）
   ↓
查天气（可 mock）→ 规则巡检（闭馆/雨天户外/营业时间越界）
   ↓ 有 issue 且 auto_fix
自动修复（雨天换室内、闭馆日调整，新增地点带审计标记）
   ↓ reoptimize
重跑路线优化
   ↓
inspection + changes + plan_json + optimize 输出
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
- Planner 结构化输出偶发失败：**根因是模型有时不调工具、直接回自然语言**，
  LangChain 的 `with_structured_output` 此时返回 `None`（不是抛异常），
  原先会炸成 `'NoneType' object has no attribute 'days'`。
  现在 `plan_to_struct` 显式校验 + 最多重试 3 次（后两次追加「必须调用工具」强提醒）

---

## M4 运筹优化（OR-Tools）

单日行程的**带时间窗最短路**（TSPTW）：给定当天一组 POI（含坐标、营业时间、停留时长），
求从当天首个地点出发、把所有点走完、且不撞闭馆的最短顺序。

### 实现要点

| 环节 | 做法 |
|---|---|
| 时间矩阵 | 高德 `/v3/distance` 批量接口：一次传多个 origin，n 个点只需 **n 次调用**（而非 n²-n） |
| 限流 | 复用 `tools/amap.py` 的 `_amap_get`（串行锁 + 0.4s 间隔 + 超限退避），实测无 10021 |
| 矩阵缓存 | 进程内 `(origin, dest) → (分钟, 公里)`，同一天多次求解 / 多次请求不重复打接口 |
| 建模 | OR-Tools routing，单车辆 + `Time` 维度（时间窗 + 停留时长）+ 弧成本 = 通行耗时 |
| 求解 | `PATH_CHEAPEST_ARC` 首解 → `GUIDED_LOCAL_SEARCH`，限时 **2.5 秒**（`FromMilliseconds`，`FromSeconds` 只吃整数） |
| 线程 | OR-Tools 是阻塞的 C++ 调用，必须 `asyncio.to_thread`，否则卡死事件循环 |
| 失败降级 | 三级：严格（到达+停留 ≤ 关门）→ 宽松（只要求到达 ≤ 关门）→ 压缩停留时长排序（不回写），全失败才保原顺序 |

### 关键：坐标体检（不修会直接让优化失效）

实测 Planner 会把坐标**编造**出来：锦里被写到河北邢台、宽窄巷子写到北京顺义、
人民公园写到承德——5 个点里 4 个是假的。脏坐标会让距离矩阵彻底失真（武侯祠→锦里算出 1020 分钟），
路线优化必然无解，前端地图也会标到别的省。

修复思路是**用权威数据源覆盖 LLM 坐标**，取信顺序：

1. **工具轨迹里的真实坐标**（`poi_index_from_steps`）：Researcher 调高德 POI 检索拿到的原始值，
   零额外接口开销，命中率最高（实测 7 个点中有 6 个直接从轨迹取回）
2. **按地点名重新检索**（带城市限定，避免重名地点查错）
3. 行程里原有的坐标：只在「有可靠参照且同城」时才保留

两个踩过的坑：

- 工具结果返回前端时会被截断到 2000 字符，**截断后的 JSON 解析必然失败** →
  用正则抠 `"name"…"location"` 对，不依赖完整 JSON
- **绝不能用行程里的坐标投票推城市**：坏点占多数时会凑出一个「假城市簇」
  （实测推出「廊坊市」），反而把好坐标全改坏。没有权威参照时就只补空坐标、不乱动已有坐标

### 坐标来源（最终形态）

```
Researcher 高德检索（真实坐标，权威）
   ↓ 工具轨迹 steps
Optimizer 坐标体检 → 覆盖 Planner 可能编造的坐标
   ↓
plan_json（前端地图）+ 距离矩阵（优化器）
```

---

## M5 主动感知（行程体检）

行程生成后，用**规则巡检**主动发现「闭馆 / 雨天户外 / 营业时间越界」三类问题，
可选自动修复并重排路线。核心在 `agents/inspector.py`，数据落 `storage.py`（SQLite）。

### 巡检规则（程序化，不靠提示词）

- **闭馆**：`monday_closed_risk()` 匹配「周一闭馆」类地点的营业文案，命中周一则报 issue
- **天气冲突**：`is_rainy()` 判断雨天，雨天撞上 `sight` 且无室内属性的项 → 建议换室内
- **营业时间越界**：`parse_open_window()` 解析营业时间窗，比对 `DayItem.time` 是否落在门外

### 自动修复（fixer Agent）

命中 issue 时，调 `fix_trip()`：flash 模型 + 结构化输出，按问题清单修订 TripPlan；
雨天户外项会从 `fetch_indoor_candidates()` 拿高德检索的**室内候选点**替换。
修复产生的差异用 `diff_plans()` 生成 `PlanChange[]`（`before/after/reason/added`），
前端据此高亮；`added=true` 的新地点带审计标记（未经工具核实）。

### 踩坑（M5 新增）

- fixer 也犯过 Planner 的「不调工具直接回自然语言」老毛病 → 同样加显式校验 + 重试
- 城市推断失灵：行程 `address` 常为空，`guess_city()` 推不出城市 →
  新增 `infer_city()`，用 POI 坐标反查城市（`regeo`）兜底
- 天气接口失败不致命：`weather` 置空，跳过天气维度，体检照常出闭馆/营业时间问题

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

## 已知限制与里程碑对应

| 限制 | 状态 |
|---|---|
| 行程只是 Markdown 文本，没有结构化 JSON | ✅ M3：Planner 输出 TripPlan |
| 前端拿不到真实流式 token | 未做（当前一次性推完整 Markdown） |
| 单 Agent 串行跑 20+ 次工具，耗时约 100 秒 | 🟡 部分改善（Critic 用 flash、矩阵缓存），仍是最主要的耗时来源 |
| 没有 Critic 审查（行程合理性/疲劳度） | ✅ M3：Critic ↔ Reviser 闭环 + 前端审查卡 |
| 单日顺序靠 LLM 拍脑袋 | ✅ M4：OR-Tools VRPTW 最优排序 + 前后对比 |
| 地点坐标可能被 LLM 编造 | ✅ M4：坐标体检（工具轨迹为权威源） |
| 记忆只在内存，重启丢失 | ✅ M5：SQLite checkpointer（同一 thread_id 重启后可追问）+ 行程库落盘 |
| 行程生成后无主动预警（闭馆/雨天/营业时间） | ✅ M5：`/api/trip/check` 体检 + 自动修复 + 前端提示条 |
| 无埋点统计（turns/latency/cost） | M6 加可观测性 |
