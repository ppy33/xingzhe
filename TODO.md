# TODO & 里程碑

按 10 层能力模型拆解，每条对应 `行者.html` 里的具体函数/区块。状态标记：⬜ 待办 / 🟡 进行中 / ✅ 已完成 / ⛔ 阻塞。

---

## Layer 1 · 用户理解与个性化 ✅

- ✅ 槽位抽取（预算/时间/出发地/同行人/语言/无障碍）
- ✅ 长期记忆（偏好/禁忌/节奏/常旅客）

---

## Layer 2 · 多模态交互 🟡

- ✅ 文字输入（textarea + Enter 发送）
- ✅ 语音输入（`webkitSpeechRecognition`，中文）
- 🟡 拍照识别（演示模板匹配，未接真实图像识别 API）
- 🟡 语音导览（TTS 朗读短语）
- ⬜ 情感识别（转人工触发）
- ⬜ 紧急话术自动转人工

---

## Layer 3 · 行前规划 ✅

- ✅ 需求澄清对话（缺啥补啥）
- ✅ 目的地知识卡
- ✅ 行程生成（分桶：白天/午餐/晚餐/夜间）
- ✅ 预算测算（门票/餐饮/通勤/总预算）
- ✅ 预订入口深链
- ✅ 清单提示（衣物/证件/插头/汇率）

---

## Layer 4 · 行中服务 🟡

- ✅ 导航示意（路线图绘制）
- ✅ 通勤时长估算
- ✅ 动态重规划（场馆闭馆/航班延误/排队过长）
- ✅ 讲解朗读（语音导览）
- ✅ 紧急资源（SOS 面板：报警/急救/火警/领事保护）
- ⬜ 实时路况集成
- ⬜ 多人协作（投票/分摊）

---

## Layer 5 · 行后与社交 ⚪

- ✅ 行程导出（Markdown 下载）
- ⬜ 复购提醒位
- ⬜ 多人投票
- ⬜ 费用分摊
- ⬜ 行程归档与回放

---

## Layer 6 · 数据与知识层 🟡

- ✅ POI 数据（101 条，7 城市）
- ✅ 气候信息
- ✅ 汇率（CNY↔USD/JPY/EUR/KRW）
- ✅ 紧急信息（电话/使领馆）
- ✅ 知识卡（带来源标注）
- 🟡 实时库存（模拟，需 OTA API）
- 🟡 实时排队（模拟，需景点 API）
- ⬜ 知识卡扩展（覆盖更多城市）

---

## Layer 7 · 技术架构 ✅

- ✅ Agent 编排（Step 协议：think/tool/result/bubble）
- ✅ 工具调用（POI/天气/路线/预订/SOS 等）
- ✅ 运筹优化（k-means 地理聚类 + 最近邻 + 开放时间约束）
- ✅ 记忆库（localStorage 持久化）
- ✅ 兜底通道（不经过模型）

---

## Layer 8 · 生态与工具集成 🟡

- ✅ OTA 深链入口（携程/Booking）
- ✅ 12306 深链
- ✅ 大众点评深链
- ⬜ 真实库存 API（需商务协议）
- ⬜ 真实价格 API
- ⬜ 真实排队 API

---

## Layer 9 · 安全合规可信 ✅

- ✅ 记忆可查看（用户画像面板）
- ✅ 记忆可删除（一键清空）
- ✅ 来源标注（每个工具调用都有 src）
- ✅ 估算显式声明（票价/排队/政策标记为估算）
- ✅ SOS 与人工兜底（独立通道）
- ✅ 密钥隔离（前端永远拿不到服务端密钥）

---

## Layer 10 · 商业与评估 🟡

- ✅ 任务完成率埋点
- ✅ 工具调用次数埋点
- ✅ 延迟埋点
- ✅ 成本埋点（按字符数估算）
- ✅ 幻觉自检项（实时量显式标注为估算）
- ⬜ 转化率（需线上数据）
- ⬜ 留存率（需线上数据）

---

## 跨层 TODO（架构演进）

### ⬜ 模块化拆分

当前 `行者.html` 是 3500+ 行单体，建议拆为：

```
行者/
├── src/
│   ├── core/
│   │   ├── agent.js        # Agent 编排与 Step 协议
│   │   ├── intent.js       # 意图识别 + 槽位抽取
│   │   └── memory.js       # 记忆库
│   ├── tools/
│   │   ├── weather.js
│   │   ├── poi.js
│   │   ├── route.js
│   │   ├── price.js
│   │   ├── booking.js
│   │   └── safety.js       # SOS + 安全校验
│   ├── data/
│   │   ├── cities.js       # 7 城市 + POI
│   │   ├── phrases.js      # 多语言短语
│   │   └── emergency.js
│   └── ui/
│       ├── stream.js       # 对话流
│       ├── itinerary.js    # 行程面板
│       ├── map.js          # 路线图
│       └── metrics.js      # 可观测性
├── index.html
└── server.js               # 数据网关（许涵松维护）
```

### ⬜ 接真实 LLM

```js
// 当前
function parseIntent(text) { /* 正则 */ }

// 替换为
async function parseIntent(text) {
  const res = await llm.chat({
    model: 'gpt-4o',
    tools: [intentSchema, slotSchema],
    messages: [{ role: 'user', content: text }]
  });
  return res;
}
```

### ⬜ OR-Tools VRPTW 替换

```js
// 当前：k-means + 最近邻
function optimizeRoute(pois) { /* 启发式 */ }

// 替换为：服务侧求解
const res = await fetch('/api/optimize', {
  body: JSON.stringify({ pois, timeWindows, duration })
});
```

---

## 待用户与许涵松确认

- [ ] 拆分粒度：模块化方案 1（按功能）vs 方案 2（按数据流）？
- [ ] LLM 选型：GPT-4o / Claude / Qwen-Max / DeepSeek？
- [ ] 后端语言：Node.js（与当前 server.js 一致）vs Python（用户更熟）？
- [ ] 部署目标：演示用静态站 vs 完整 SaaS？
- [ ] 商业模式：免费 MVP / 增值 API / 接入 OTA 分成？

---

## 里程碑（建议）

| 里程碑 | 目标 | 预计 |
|---|---|---|
| M1 | 模块化拆分（先拆 core/tools/data 三层） | TBD |
| M2 | 接真实 LLM（意图+槽位走模型，规则保底） | TBD |
| M3 | 接真实 POI API（高德/Mapbox） | TBD |
| M4 | OR-Tools 路线优化 | TBD |
| M5 | 多人协作（投票/分摊） | TBD |