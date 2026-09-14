# 行者 v2.0 升级路线图

> 目标：在行者 v1（3500 行 HTML 原型）基础上，升级为「生产级多 Agent 智能旅游助手」，
> 呈现效果对标 B 站 UP 主 czxgg0630/trip-planner 项目，并在三个维度上做到更"Agent"。

---

## 一句话定位

**行者 v2.0 = 主动感知 + 多 Agent 协同 + 运筹优化**

- UP 主项目：问答型单 Agent（你问，它答）
- 行者 v2.0：主动型多 Agent（盯着行程，主动找你）

---

## 与参考项目（czxgg0630/trip-planner）的对比

| 维度 | UP 主 | 行者 v2.0 |
|---|---|---|
| 架构 | 1 Agent (Researcher + Planner Node) | 5 Agent 协同 |
| 路线优化 | LLM 直接排 | OR-Tools VRPTW |
| 主动感知 | 无 | 事件流 + 自动重排 |
| 自我反思 | 无 | Critic Agent |
| 多模态 | 无 | 语音 / 图像 / 视频 |
| 长期记忆 | 无 | ChromaDB 向量记忆 |
| 可观测性 | 弱 | LangSmith + 自建埋点 |
| 人机协作 | 无 | Human-in-the-loop |
| 工具数量 | 4 个 | 10+ 个 |
| 评估体系 | 无 | 自动化场景测试 |

---

## 技术栈

### 后端
- **FastAPI** + Uvicorn（API 网关）
- **LangGraph** + LangChain（多 Agent 编排）
- **DeepSeek** Chat（主力 LLM）
- **OR-Tools**（VRPTW 路径优化）
- **Pydantic v2**（数据校验）
- **httpx**（异步 HTTP）

### 前端
- **Vue3** + TypeScript + Vite
- **Ant Design Vue 4**（UI 组件）
- **高德地图 JS API 2.0**（地图标注 + 路线）
- **html2canvas + jsPDF**（导出）
- **WebSocket / SSE**（流式输出）

### 数据 & 工具
- **高德开放平台**：POI / 路径规划 / 天气 / 实时交通
- **Unsplash API**：景点配图
- **ChromaDB**：向量记忆
- **SQLite / PostgreSQL**：行程持久化

### 可观测性
- **LangSmith**：Agent 链路追踪
- **自建埋点面板**：turns / tools / latency / cost / quality
- **Prometheus + Grafana**（可选，进阶）

---

## 多 Agent 架构

```
┌─────────────────────────────────────────────────┐
│ Frontend (Vue3)                                  │
│  - Map View / Timeline / Chat / Dashboard       │
└─────────────────────────────────────────────────┘
                     │ SSE/WebSocket
┌─────────────────────────────────────────────────┐
│ FastAPI Gateway                                  │
└─────────────────────────────────────────────────┘
                     │
┌─────────────────────────────────────────────────┐
│ LangGraph Multi-Agent Orchestration             │
│                                                 │
│  ┌──────────────┐  ┌──────────────┐             │
│  │ Researcher   │  │ Planner      │             │
│  │ (ReAct)      │→ │ (Constraint) │             │
│  └──────────────┘  └──────────────┘             │
│         ↑                ↓                       │
│  ┌──────────────┐  ┌──────────────┐             │
│  │ Critic       │← │ Optimizer    │             │
│  │ (Reflect)    │  │ (Re-plan)    │             │
│  └──────────────┘  └──────────────┘             │
│         ↖                ↙                       │
│  ┌──────────────────────────────────┐           │
│  │ Memory Agent                      │           │
│  │  - Short-term (session)            │           │
│  │  - Long-term (user profile)        │           │
│  │  - Vector (ChromaDB)              │           │
│  └──────────────────────────────────┘           │
└─────────────────────────────────────────────────┘
                     │
┌─────────────────────────────────────────────────┐
│ Tools / MCP                                      │
│  - search_attractions / search_hotels           │
│  - get_weather / get_route                      │
│  - search_restaurants / nearby_search           │
│  - vision_recognize / tts_speak                 │
│  - web_search / sos_alert                       │
│  - export_pdf / export_png                      │
└─────────────────────────────────────────────────┘
```

### 各 Agent 职责

| Agent | 职责 | 关键能力 |
|---|---|---|
| **Researcher** | 信息搜集 | ReAct + 工具调用，多轮搜索 |
| **Planner** | 行程生成 | 约束求解 + 时间感知 + 资源编排 |
| **Critic** | 质量审视 | Reflection Loop：合理性 / 冲突 / 疲劳度 / 预算 |
| **Optimizer** | 重排优化 | 触发重排：事件 / 反馈 / 评分 |
| **Memory** | 记忆管理 | 短期 / 长期 / 向量三层记忆 |

---

## 8 周路线图

| 周次 | 里程碑 | 产出 |
|---|---|---|
| W1 | M1 骨架 | FastAPI + LangGraph + DeepSeek 单 Agent 跑通 |
| W2 | M2 工具迁移 | 行者 7 工具 → LangGraph Tool Node |
| W3 | M2 完成 | 高德 API 全接入 + 实时数据 |
| W4 | M3 多 Agent | Planner / Critic / Optimizer 协同 |
| W5 | M4 前端 | Vue3 + Ant Design + 高德地图 |
| W6 | M5 观测 | LangSmith + 自建埋点面板 |
| W7 | M6 评估 | 10 场景测试 + 演示视频 |
| W8 | M7 文档 | README + 部署文档 + 展示页 |

---

## 启动决策（2026-09-13 已定稿 ✅）

| 事项 | 决定 |
|---|---|
| **团队分工** | 用户主导开发；前端 + 后端两人共同完成 |
| **LLM 选型** | **DeepSeek**（deepseek-chat 主力 + deepseek-reasoner 做 Critic），国内直连、无需代理、兼容 function calling |
| **高德 API Key** | 已申请到两个（见下方配置） |
| **后端语言** | Python（FastAPI + LangGraph） |
| **部署目标** | 待定（先本地 demo） |

---

## 密钥配置（.env）

> ⚠️ **安全红线：真实密钥一律不写进本文件，也不进 git。**
> 本文件是**公开文档**（会随仓库同步给协作者），只记录字段名和申请入口。
> 真实值写在各自的本地 `.env` / `.env.local` 里，这两个文件都已在 `.gitignore` 中。
>
> **新加入的协作者**：照着 `.env.example` 复制一份 `.env`，然后找项目负责人私下拿密钥值。

| 变量名 | 用途 | 说明 |
|---|---|---|
| `AMAP_SERVER_KEY` | 后端调高德 | Web服务 Key，申请入口 console.amap.com |
| `AMAP_JSAPI_KEY` | 前端地图 | Web端(JS API) Key，**必须绑定域名白名单** |
| `AMAP_JSAPI_SECRET` | 前端地图 | JS API 安全密钥 jscode，与上面成对 |
| `DEEPSEEK_API_KEY` | LLM | platform.deepseek.com/api_keys |
| `DEEPSEEK_BASE_URL` | LLM | `https://api.deepseek.com/v1` |
| `DEEPSEEK_MODEL` | LLM | `deepseek-v4-pro`（主力，编排 / function calling） |
| `DEEPSEEK_MODEL_FAST` | LLM | `deepseek-flash`（轻量任务 / Critic 审查） |

> 实测（2026-09）：该账号可见模型为 `deepseek-v4-pro` 与 `deepseek-flash`。

### 密钥安全须知

- **前端 Key 本质是公开的**（构建后必然出现在 JS 里），防护手段是**在高德控制台绑定域名白名单**——这一步必须做，否则别人的站点能白嫖你的配额
- **后端 Key 绝不能进前端**：它能直接调高德 Web 服务 API，泄漏等于配额被盗刷
- 一旦怀疑泄漏：立即去对应控制台**删除旧 Key 并重建**，再同步更新各人本地 `.env`

### LLM 选型说明（为什么换 DeepSeek）

| 维度 | GPT | DeepSeek |
|---|---|---|
| 网络 | 需代理（127.0.0.1:7897） | **国内直连** |
| 价格 | gpt-4o 量级费用 | **约为其 1/20** |
| Function calling | ✅ | ✅ |
| 中文能力 | 好 | **更好** |
| 注册充值 | 需国外卡 | **支付宝/微信** |
| 适合场景 | 全球化产品 | **国内项目、比赛、毕设** |

---

## 立刻可启动（M1）

**任务**：把行者.html 的工具接口抽成 LangGraph Tool Schema

**步骤**：
1. 解析行者.html 的 Step 协议部分（grep `step_` / `function call` 关键字）
2. 提取 7 个核心工具的输入输出 schema
3. 生成 LangGraph `@tool` 装饰器风格的 Python 文件
4. 跑通 FastAPI + 单 Agent 调用 DeepSeek + 一个工具

**预计产出**：
- `v2/backend/tools/` 目录
- `v2/backend/agents/researcher.py`
- `v2/backend/main.py`
- 端到端 demo：CLI 输入"上海去成都 4 天"，输出结构化行程

---

## 收益

### 项目本身
- 完整的 LangGraph 多 Agent 生产案例
- 真实的高德 API 集成经验
- 漂亮的 Vue3 前端作品

### 对保研 / 夏令营
- 写在「科研经历」或「项目经历」里，**有竞争力**
- 对标大厂 Agent 工程师 JD（阿里通义 / 字节扣子 / 腾讯元宝 / DeepSeek）
- 比赛 / 大创 / 挑战杯皆可包装

### 对长期
- Agent 是 AI 应用的下一个 5 年主战场
- 多 Agent / ReAct / Reflection 是核心范式
- 简历上比纯 CRUD 项目亮眼 10 倍

---

_本文件由 AI 起草，待与许涵松对齐后修订。_
_参考：B 站视频 BV1uptZ6DEn6（czxgg0630）+ GitHub czxgg0630/trip-planner_
