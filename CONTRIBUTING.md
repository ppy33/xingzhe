# 协作开发指南

> 本文是「行者 v2」团队的协作约定。**动手写代码前先读一遍**，能省掉 90% 的互相踩踏。
>
> 团队：用户（主导开发）· 许涵松（原型 / 架构）

---

## 一、5 分钟跑起来

### 1. 克隆仓库

```bash
git clone <仓库地址> && cd 行者_智能旅游助手
```

### 2. 配置密钥（找负责人私下拿值）

```bash
# 后端
cd v2/backend
cp .env.example .env          # 然后把真实值填进去

# 前端
cd ../frontend
cp .env.example .env.local    # 然后把真实值填进去
```

> ⚠️ `.env` 和 `.env.local` 已被 `.gitignore` 拦截，**永远不要提交**。见第六节密钥纪律。

### 3. 起后端（Windows 下务必先清代理）

```bash
cd v2/backend
pip install -r requirements.txt
unset HTTP_PROXY HTTPS_PROXY ALL_PROXY    # ← 不清会报 httpx Connection error
python -m uvicorn main:app --host 127.0.0.1 --port 8000
```

验证：浏览器打开 <http://127.0.0.1:8000/health>，应返回 `{"status":"ok",...}`

### 4. 起前端

```bash
cd v2/frontend
npm install
npm run dev        # http://127.0.0.1:5173
```

两个调试入口：

- `http://127.0.0.1:5173/?demo=1` —— **离线演示模式**，不调后端、不烧 token，改 UI 时用这个
- `http://127.0.0.1:5173/` —— 真实链路（会调用 DeepSeek + 高德，一次请求约 60–120 秒）

---

## 二、仓库结构：改哪里，找谁

```
行者_智能旅游助手/
├── README.md              # 项目总览（v1 能力模型）
├── V2_ROADMAP.md          # v2 路线图 + 里程碑（M1–M7）
├── ARCHITECTURE.md        # Agent 循环 / Step 协议 / 状态机
├── DATA.md                # POI 数据规范
├── TODO.md                # 待办池
├── 分工.md                # 认领与进度
├── 接口契约.md            # ★ 前后端契约（改动需走流程）
├── CONTRIBUTING.md        # 本文件
├── 行者.html              # v1 原型（只读参考，不再修改）
└── v2/
    ├── backend/
    │   ├── main.py        # FastAPI 入口 + 多 Agent 编排主流程
    │   ├── config.py      # .env 加载
    │   ├── cli.py         # 命令行调试入口
    │   ├── agents/
    │   │   ├── schemas.py     # ★ 契约型文件：Pydantic Schema
    │   │   ├── researcher.py  # ReAct + 高德工具
    │   │   ├── critic.py      # 审查 + 修订 + 白名单核验
    │   │   └── planner.py     # 结构化输出
    │   └── tools/amap.py  # 7 个高德工具（限流/重试/护栏）
    └── frontend/
        ├── src/App.vue            # 状态中枢 + SSE 处理
        ├── src/components/        # 地图 / 对话 / 行程 / 轨迹
        ├── src/composables/       # 高德加载、SSE 客户端
        └── docs/                  # 截图与演示素材
```

### 模块归属

| 模块 | 主责 | 说明 |
|---|---|---|
| `agents/` · `main.py` · `tools/` | 用户 | 后端与 Agent 编排 |
| `frontend/` | 用户 | 前端界面（可邀请协作者） |
| 原型与运筹设计参考 | 许涵松 | `行者.html` 只读参考，不直接改 |
| `接口契约.md` · `schemas.py` | **共同** | 改动必须双方确认（见第五节） |

---

## 三、分支模型

**`main` 是受保护分支，任何人不直接推。** 所有改动走分支 + PR。

| 分支前缀 | 用途 | 示例 |
|---|---|---|
| `feat/` | 新功能 | `feat/ortools-optimizer` |
| `fix/` | 修 bug | `fix/planner-retry-timeout` |
| `refactor/` | 重构（不改行为） | `refactor/critic-prompt-split` |
| `docs/` | 只改文档 | `docs/api-contract-v1` |
| `chore/` | 依赖、配置、脚本 | `chore/add-pytest-ci` |

命名带**模块名**，方便一眼看出影响面：`feat/optimizer-vrptw`、`fix/frontend-sse-review`。

```bash
git checkout main && git pull
git checkout -b feat/ortools-optimizer
# ...写代码...
git push -u origin feat/ortools-optimizer
```

> **分支别开太久**。超过 3 天没合并的分支，合并冲突会成倍增长。大功能用「小步多次 PR」拆开。

---

## 四、提交规范

采用 Conventional Commits 的**中文**写法：

```
<类型>(<模块>): <一句话说清做了什么>

<可选：为什么这么做 / 有什么影响>
```

类型：`feat` `fix` `refactor` `docs` `chore` `test` `perf`

**好的例子**：

```
feat(optimizer): 接入 OR-Tools VRPTW 求解单日最优顺序

用营业时间做时间窗、停留时长做服务时间，交替使用
PathCheapestArc + GuidedLocalSearch 保证 5 秒内出解。
前端加了优化前后里程对比条。
```

```
fix(critic): 修正判定规则歧义导致带着 high 问题放行

原文「只存在 high 时 passed=false」被模型理解为
「仅有 high 才 false」，改为「只要存在任一 high 必须 false」。
```

**避免**：`更新`、`修好了`、`111`、一次提交改 15 个文件说不清做了什么。

**一个提交只做一件事**：改代码和改文档如果逻辑无关，分两次提交。

---

## 五、PR 流程

1. **开 PR**：用仓库自带的 PR 模板填（`feat` 分支推上去后 GitHub 会自动带上）
2. **等 CI 绿**：CI 会跑后端语法检查、前端构建、**密钥泄漏扫描**。红了先修
3. **找对方 review**：至少 1 人 approve 才能合并
4. **Squash 合并**：保持 main 上每个提交都是一个完整特性
5. **合完删分支**

### 评审时重点看什么

- [ ] **有没有破坏接口契约**（`schemas.py` / SSE 事件类型 / REST 字段）——破坏了要在 PR 描述里写明并 @ 对方
- [ ] **有没有把密钥写进代码或文档**（含注释、示例、日志）
- [ ] **提示词改动有没有说明理由**——Prompt 是这项目的核心资产，改一个字都可能让 Critic 失效，必须附「改前表现 / 改后表现」
- [ ] 新增工具函数是否复用了 `tools/amap.py` 里的限流锁（否则会被高德限流 10021 打爆）
- [ ] 前端改动是否在 `?demo=1` 模式下能看出效果（便于评审者本地验证）

### 契约变更流程（重要）

`schemas.py` 里的字段、SSE 事件类型是**前后端共同依赖的契约**。变更规则：

1. **加字段**：可以，但必须给默认值（`Field(default=...)`），不破坏老客户端 ✅
2. **改字段名 / 删字段 / 改事件名**：**禁止直接改**。先开 issue 讨论 → 双方确认 → 同一次 PR 里同步改后端 + 前端 + `接口契约.md`
3. 每次契约变更，`接口契约.md` 顶部的**契约版本号**要 +1，并在变更日志里记一行

---

## 六、密钥纪律（红线）

| 规则 | 说明 |
|---|---|
| 真实密钥只写本地 `.env` / `.env.local` | 这两个文件在 `.gitignore` 里，CI 也会扫 |
| 文档、注释、截图里不能出现真实密钥 | 包括 `V2_ROADMAP.md` 这类会被提交的文件 |
| **后端 Key 绝不能进前端** | 它能直接调高德 Web 服务，泄漏等于配额被刷爆 |
| 前端 Key 要去高德控制台**绑域名白名单** | 前端 Key 构建后必然可见，白名单是唯一防护 |
| 怀疑泄漏就立即重建 | 控制台删旧 Key → 建新 Key → 通知各人更新本地 `.env` |

仓库自带密钥扫描：

- **CI**：每次 PR 自动扫描，命中关键词直接失败
- **本地钩子**（可选但强烈建议）：

```bash
git config core.hooksPath .githooks
```

装完后每次 `git commit` 前会自动扫一遍暂存区。

---

## 七、本地开发常见坑

| 现象 | 原因 | 解法 |
|---|---|---|
| `httpx Connection error` | 系统代理残留（Clash 7897 已关） | 启动后端前 `unset HTTP_PROXY HTTPS_PROXY ALL_PROXY` |
| 前端地图空白 / JSAPI 加载失败 | 系统代理拦截 | 关系统代理，或给 `webapi.amap.com` 加直连规则 |
| 高德报 `10021` 限流 | 工具被并发调用，个人 Key 约 3 QPS | 复用 `tools/amap.py` 的 `asyncio.Lock` 串行 + 0.4s 间隔 |
| 结构化输出报 `Thinking mode does not support this tool_choice` | DeepSeek 默认开 thinking | 构造 LLM 时加 `extra_body={"thinking": {"type": "disabled"}}` |
| `response_format=json_schema` 报不可用 | DeepSeek 限制 | 用 `with_structured_output(..., method="function_calling")` |
| 一次真实请求要 1–2 分钟 | 单 Agent 串行跑 20+ 次工具 | 正常现象；改 UI 时用 `?demo=1` 模式 |
| `npm run build` 报 safe-delete 失败 | Windows 删 dist 被拦 | 先 `rm -rf dist` 再 `npm run build` |

---

## 八、改完东西要更新哪里

| 改了 | 顺手更新 |
|---|---|
| 新增/修改接口、Schema、SSE 事件 | `接口契约.md`（含版本号 + 变更日志） |
| 完成一个里程碑 | `V2_ROADMAP.md` 的里程碑表、`TODO.md` |
| 认领/完成模块 | `分工.md` 的进度表 |
| 新增一个高德工具 | `v2/backend/README.md` 的工具表 |
| 踩到新坑并解决了 | 本文第七节 + `v2/backend/README.md` 的踩坑记录 |

---

## 九、节奏

- **同步**：每周日晚 20:00（线上，15 分钟，各自说「做了啥 / 卡在哪 / 下周做啥」）
- **卡住超过 2 小时**：直接问，别硬扛
- **架构级改动**（新增 Agent、换 LLM、改主流程）：先开 issue 讨论，别先写代码
