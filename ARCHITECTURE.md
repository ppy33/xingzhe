# 架构说明

> 提取自 `行者.html` 的架构面板（右侧「架构」标签页），便于离线查阅与协作沟通。

---

## 一、Agent 循环（每轮对话走一遍）

```
意图理解 → 槽位补全 → 知识检索 → 工具调用 → 结果校验 → 运筹优化 → 安全兜底 → 记忆写回
```

任一环节异常都会回落到「安全兜底 → 人工客服」，不把不确定结果直接抛给用户。

---

## 二、Step 协议

Agent 输出按四类 step 组织：

| 类型 | 用途 | 视觉 |
|---|---|---|
| `think` | 推理（意图/槽位/决策） | 灰色小气泡 |
| `tool` | 工具调用卡片（可展开看输入输出） | 左侧工具卡 |
| `result` | 工具返回数据 | 工具卡内 |
| `bubble` | 给用户的回复 | 主对话气泡 |

每条 step 都有 `summary`（一句话摘要）+ `data`（结构化结果）+ `sources`（来源条数）+ 可选 `ui`（渲染指令）。

---

## 三、状态机

`state` 对象跟踪：

- `state.user` — 用户画像（偏好/禁忌/节奏/常旅客）
- `state.slots` — 当前轮槽位（出发地/目的地/天数/同行人/预算/偏好/禁忌）
- `state.city` — 当前目的地城市对象（含 POI 库、汇率、紧急信息）
- `state.plan` — 当前行程（含 days/items/transit/safety/stats/version）
- `state.metrics` — 可观测性埋点（turns/tools/latency/sources/cost/replans）

---

## 四、意图分类

```js
const intents = {
  PLAN:      /行程|规划|安排|计划|几天|去哪|去哪儿|推荐|攻略|做一|排一|玩什么|路线/,
  CHANGE:    /改|换|删|加|轻松|紧凑|慢|快|预算/,
  INFO:      /.*/,  // 默认信息类
  TRANSLATE: /翻译|怎么说|短语|日语|粤语|英文怎么说/,
  SOS:       /紧急|报警|丢失|被盗|急救|使领馆|护照/,
};
```

未匹配 → 走信息类，走知识库检索。

---

## 五、运筹优化

### 当前实现（启发式）

```js
// 白天桶按最近邻顺序排 → 插午餐 → 插晚餐 → 夜间桶收尾
function schedule(pois, day) {
  // 1. 按地理聚类（k-means）分桶
  // 2. 每桶内最近邻排序
  // 3. 插餐（避开横穿城市）
  // 4. 开放时间约束
  // 5. 替换规则：户外→室内（雨天）、贵→平（压预算）
}
```

### 生产实现（OR-Tools VRPTW）

```python
from ortools.constraint_solver import routing_enums_pb2

def solve(pois, time_windows, duration_matrix):
    routing = pywrapcp.RoutingModel(...)
    # 时间窗约束 + 通勤时长最小化 + 必访约束
    return solution
```

---

## 六、记忆库

### 当前（localStorage）

```js
// Key: 'traveler.mem.v1'
{
  user: { pace: 'slow', dislikes: ['辣'], preferredCats: ['美食', '自然'] },
  trips: [ /* 历史行程，可回放 */ ],
  prefs: { /* 跨行程偏好 */ }
}
```

### 生产（向量库 + 关系库）

```python
# Neo4j: 用户-偏好-目的地-行程 关系图
# Pinecone: 行程语义检索（"上次去成都那种风格"）
```

---

## 七、工具调用接口签名

| 工具 | 输入 | 输出 |
|---|---|---|
| `weather.forecast` | `{city, days}` | `[{date, hi, lo, rain, tip}]` |
| `poi.search` | `{city, cats, tags, exclude[]}` | `[{name, lat, lng, dur, open, price, rating}]` |
| `route.optimize` | `{pois, pace, method}` | `[{day, items: [{time, poi}]}]` |
| `price.estimate` | `{plan, pax}` | `{tickets, meals, transit, total}` |
| `booking.deepLink` | `{poi, date, pax}` | `{url}` |
| `safety.check` | `{plan, city, profile}` | `{level, items[]}` |
| `memory.write` | `{key, value}` | `{ok}` |
| `memory.read` | `{key}` | `{value}` |

接口签名保持稳定，**实现层可替换**（mock → 真实 API）。

---

## 八、事件驱动（主动服务）

当前是模拟事件，未来接真实事件流：

| 事件 | 触发工具 | 重排类型 |
|---|---|---|
| 场馆临时闭馆 | `poi.status` | 替换景点 |
| 航班延误 | `flight.status` | 接驳+住宿重排 |
| 排队过长 | `poi.queue` | 换序 |
| 同行人不适 | `companion.status` | 降强度 |

---

## 九、安全兜底通道

不走模型，独立通道：

- **紧急关键词检测** → 立即切换兜底通道，输出可用资源（报警/急救/火警/领事）
- **实时量显式标注** → 票价/排队/政策标为估算区间，不编造确切数字
- **记忆可查可删** → 用户随时查看/清空自己的画像
- **密钥隔离** → API key 永远在服务端，前端拿不到

---

## 十、可观测性埋点

| 指标 | 用途 |
|---|---|
| `turns` | 对话轮次 |
| `tools` | 工具调用次数 |
| `latency` | 平均工具延迟 |
| `sources` | 引用来源条数 |
| `cost` | 估算模型成本（按字符数） |
| `replans` | 动态重规划次数 |

所有指标实时显示在「架构」面板，方便调试与产品决策。