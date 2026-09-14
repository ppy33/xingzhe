# 开发规范

> 本文是「行者 v2」的开发约定。**动手写代码前先读一遍**。
>
> 开发者：**彭云** ｜ v1 原型作者：许涵松（`行者.html`，只读参考）
>
> **当前是单人开发**，所以下面分两类看（详见「零、单人开发怎么用这份文档」）：
> - 仍然要守：提交规范 · 密钥纪律 · 接口契约 · 踩坑表
> - 可以简化：分支 + PR + 互评

---

## 零、单人开发怎么用这份文档

单人开发最大的风险不是「和别人冲突」，而是**三个月后的自己看不懂两个月前的自己**。所以：

| 章节 | 单人模式 |
|---|---|
| 三、分支模型 | **主干开发为主**：小改动直接提交到 `main`；大功能/会改坏现有链路的实验，开短分支再合回来 |
| 四、提交规范 | **照常遵守**，而且更重要——`git log` 就是你自己的开发日志 |
| 五、PR 流程 | 只在「自己 review 自己」时用：开 PR 让 CI 先跑一遍再合。**不要去点上「要求 1 人 approve」，单人会被锁死**（GitHub 不允许作者批准自己的 PR） |
| 五、契约变更流程 | **照常遵守**。前后端都是你写的，更容易改完一边忘了另一边，契约文档就是防这个的 |
| 六、密钥纪律 | **照常遵守**，这条和几个人无关 |
| 七、常见坑 | **照常**，踩过的坑别再踩第二次 |
| 九、节奏 | 改成：每周固定一个时间回顾进度 + 更新 `开发计划.md` |

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

## 二、仓库结构导航

```
行者_智能旅游助手/
├── README.md              # 项目总览（v1 能力模型）
├── V2_ROADMAP.md          # v2 路线图 + 里程碑（M1–M7）
├── ARCHITECTURE.md        # Agent 循环 / Step 协议 / 状态机
├── DATA.md                # POI 数据规范
├── TODO.md                # 待办池
├── 开发计划.md            # 进度与下一步计划
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

| 模块 | 说明 |
|---|---|
| `agents/` · `main.py` · `tools/` | 后端与 Agent 编排 |
| `frontend/` | 前端界面 |
| `行者.html` | v1 原型（许涵松），**只读参考，不直接改** |
| `接口契约.md` · `schemas.py` | 契约型文件，改动要三处同步（见第五节） |

---

## 三、分支模型

**单人模式：主干开发为主。** 小改动直接提交到 `main`；只有下面两种情况才开分支：

- 要动的链路可能改坏（改主流程、改 Critic/Planner 的输出契约）
- 实验性尝试，不确定能不能成（想试个新方案，但不想把 main 弄乱）

| 分支前缀 | 用途 | 示例 |
|---|---|---|
| `feat/` | 新功能 | `feat/ortools-optimizer` |
| `fix/` | 修 bug | `fix/planner-retry-timeout` |
| `refactor/` | 重构（不改行为） | `refactor/critic-prompt-split` |
| `docs/` | 只改文档 | `docs/api-contract-v1` |
| `chore/` | 依赖、配置、脚本 | `chore/add-pytest-ci` |

命名带**模块名**，方便一眼看出影响面：`feat/optimizer-vrptw`、`fix/frontend-sse-review`。

```bash
git checkout -b feat/ortools-optimizer
# ...写代码、提交...
git checkout main
git merge --no-ff feat/ortools-optimizer   # 保留分支痕迹，方便日后回看
git branch -d feat/ortools-optimizer
git push
```

> **分支别开太久**。超过 3 天没合并的分支，回头合并时冲突会成倍增长。

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

## 五、改动的自检流程

**单人模式下不必走 PR**，但「开 PR 让 CI 先跑一遍再合」仍然值得用于**改主流程、改契约**这类高风险改动。两种方式选一个：

**方式 A：直接开发（日常）**

1. 提交前跑一遍自检清单（见下）
2. 直接提交到 `main` 并推送
3. 推送后去仓库的 **Actions** 页看 CI 是否绿——**CI 是你唯一的守门人**，红了立刻修

**方式 B：开 PR 自审（高风险改动）**

1. 开 PR 并填模板（模板里的清单就是给你自己看的）
2. 等 CI 绿
3. **不要开启「Require approvals」**——GitHub 不允许作者批准自己的 PR，开了你会永远合不进去
4. Squash 合并

> ⚠️ **上一版文档让你在分支保护里勾「Require approvals: 1」——单人开发下请务必不要勾**，那会把你锁死在 main 之外。要么不开分支保护，要么只勾「Require status checks」（要求 CI 通过）。

### 提交前自检清单

- [ ] **有没有破坏接口契约**（`schemas.py` / SSE 事件类型 / REST 字段）——破坏了要同一次改完后端 + 前端 + `接口契约.md`
- [ ] **有没有把密钥写进代码或文档**（含注释、示例、日志）——本地钩子会自动拦，但别绕过它
- [ ] **提示词改动有没有记下「改前 / 改后」表现**——Prompt 是这项目的核心资产，改一个字都可能让 Critic 失效，记在提交信息里，否则过后完全不记得为什么改
- [ ] 新增工具调用是否复用了 `tools/amap.py` 里的限流锁（否则会被高德限流 10021 打爆）
- [ ] 前端改动是否在 `?demo=1` 模式下确认过效果
- [ ] 真实链路改动是否至少跑通一次（改 UI 可以只跑 demo，改 Agent 必须跑真链路）

### 契约变更流程（重要）

`schemas.py` 里的字段、SSE 事件类型是**前后端共同依赖的契约**。单人开发更要注意——两边都是你写的，最容易改完一边忘了另一边：

1. **加字段**：可以，但必须给默认值（`Field(default=...)`），不破坏老客户端 ✅
2. **改字段名 / 删字段 / 改事件名**：一次改完**后端 + 前端 + `接口契约.md`** 三处，别分几次提交，否则中间态是坏的
3. 每次契约变更，`接口契约.md` 顶部的**契约版本号** +0.1，并在变更日志里记一行

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
| `git push` 报 `CRYPT_E_NO_REVOCATION_CHECK` 或 `unable to get local issuer certificate` | **加速器在中间人劫持 github.com** | 见下方「GitHub 推不上去」 |
| `git push` 报 `could not read Username ... terminal prompts disabled` | 本机没缓存 GitHub 凭据 | 在自己终端里直接跑 `git push`，让凭据管理器弹窗登录 |

### GitHub 推不上去：先看证书是谁签的

```bash
echo | openssl s_client -connect github.com:443 -servername github.com 2>/dev/null | openssl x509 -noout -issuer
```

- 输出含 **`SteamTools Certificate` / `BeyondDimension`** → 是 **Watt Toolkit（Steam++）之类加速器**在对 GitHub 做 HTTPS 中间人加速。它替换了证书，Git 自带的 CA 包不认，于是校验失败。两种解法：

  **① 关掉加速器的「GitHub 加速」功能（或退出该工具）——推荐**
  证书恢复成真证书，最干净，而且不用担心账号凭据经过第三方。

  **② 改用读 Windows 证书库的后端**（Windows 证书库里已有它装的根证书）：
  ```bash
  git config http.sslBackend schannel
  git config http.schannelCheckRevoke false   # 该网络下拉不到吊销列表，不关仍会失败
  ```
  ⚠️ 这条路等于**你的 GitHub 账号 / Token 会经过那个加速器**（本仓库为跑通推送已采用此配置，介意的话走 ①）。

- 输出是 `CN=*.github.com` 之类正常证书 → 不是劫持问题，去查系统代理设置。

---

## 八、改完东西要更新哪里

| 改了 | 顺手更新 |
|---|---|
| 新增/修改接口、Schema、SSE 事件 | `接口契约.md`（含版本号 + 变更日志） |
| 完成一个里程碑 | `V2_ROADMAP.md` 的里程碑表、`TODO.md` |
| 完成一个里程碑 | `开发计划.md` 进度表 + `V2_ROADMAP.md` |
| 新增一个高德工具 | `v2/backend/README.md` 的工具表 |
| 踩到新坑并解决了 | 本文第七节 + `v2/backend/README.md` 的踩坑记录 |

---

## 九、节奏

单人开发，节奏靠**固定的回顾点**，不靠别人催：

- **每周固定一次**（建议周日晚）：更新 `开发计划.md` 的进度表，写下「本周做完 / 卡在哪 / 下周做啥」
- **每完成一个里程碑**：更新 `V2_ROADMAP.md` 和 `README.md` 的进度标注，顺手截几张图存 `v2/docs/`
- **卡住先自己查文档**：本文第七节 + `v2/backend/README.md` 的踩坑记录，八成坑已经记录过
- **架构级改动**（新增 Agent、换 LLM、改主流程）：先写一段设计说明（放 `TODO.md` 或单独 md），再动手。**没人 review 时，写下来就是你的 review**
- **写不下去了**：把「当前卡点 + 已试过什么」写进 `开发计划.md`，下次接上不用重新想
