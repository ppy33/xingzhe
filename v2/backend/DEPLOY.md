# 后端部署指南

前端已可静态部署（CloudStudio 等），本文件讲**后端**怎么上云，让线上前端也能跑真实链路。

## 一、需要的环境变量

部署平台（Railway / Render / 云服务器）里配置这些环境变量（**不要把 .env 文件打进镜像**）：

| 变量 | 必填 | 说明 |
|---|---|---|
| `DEEPSEEK_API_KEY` | ✅ | LLM Key（或换成你的自定义模型，见下） |
| `DEEPSEEK_BASE_URL` | ❌ | 默认 `https://api.deepseek.com/v1` |
| `DEEPSEEK_MODEL` | ❌ | 默认 `deepseek-v4-pro` |
| `DEEPSEEK_MODEL_FAST` | ❌ | 默认 `deepseek-flash` |
| `AMAP_SERVER_KEY` | ✅ | 高德「Web服务」Key（后端查 POI/天气/路线） |
| `DB_PATH` | ❌ | 默认 `data/xingzhe.db`（容器内会随重启丢失，演示可接受） |

> 想接入别的模型？不用改代码——前端「模型设置」面板填 API Key/Base URL 即可，或在上表配置任意 OpenAI 兼容模型的环境变量。

## 二、三种部署方式（按推荐度）

### 方式 A：Railway（最省事，免费额度）

1. 把代码推到 GitHub（已推送：`ppy33/xingzhe`）
2. 打开 [railway.app](https://railway.app) 登录（GitHub 一键）
3. **New Project → Deploy from GitHub repo** → 选 `ppy33/xingzhe`
4. 设置 **Root Directory** = `v2/backend`，会自动识别 `Dockerfile`
5. **Variables** 里填上表的 `DEEPSEEK_API_KEY` + `AMAP_SERVER_KEY`
6. Deploy 完成后拿到后端 URL（形如 `https://xxx.up.railway.app`）
7. 本地前端 `.env.local` 改 `VITE_API_BASE=<后端URL>`，重新 `npm run build` 部署前端

### 方式 B：Render（免费层，会休眠）

类似 Railway：New Web Service → 连 GitHub → Root Directory `v2/backend` → 填环境变量 → Deploy。
免费层 15 分钟无访问会休眠，首次唤醒要 30 秒（演示前先点一次「唤醒」）。

### 方式 C：腾讯云/阿里云轻量服务器（国内最稳）

1. 买一台轻量服务器（学生价几块钱/月），选 Ubuntu 镜像
2. 安装 Docker：`curl -fsSL https://get.docker.com | sh`
3. 把代码 clone 上去，进 `v2/backend` 跑：
   ```bash
   docker build -t xingzhe .
   docker run -d -p 8000:8000 \
     -e DEEPSEEK_API_KEY=xxx -e AMAP_SERVER_KEY=xxx \
     xingzhe
   ```
4. 后端地址 `http://服务器公网IP:8000`（记得安全组放行 8000 端口）

## 三、前端对接

无论哪种方式，拿到后端 URL 后：

```bash
cd v2/frontend
# .env.local 里把 VITE_API_BASE 指到后端
VITE_API_BASE=https://你的后端地址
npm run build
```

重新构建后部署前端静态产物（CloudStudio 等），线上前端就会访问你的云端后端了。

## 四、注意

- **高德 API 对国外 IP 可能不友好**：Railway/Render 部署在海外，后端调高德/DeepSeek 可能延迟高或被限。若出现高德报错，换国内云（方式 C）。
- **密钥安全**：`DEEPSEEK_API_KEY` / `AMAP_SERVER_KEY` 只通过平台环境变量注入，绝不要提交到 Git 或写进 Dockerfile。
- **演示兜底**：答辩现场网络不稳定时，最稳的仍是**本地跑后端**（`uvicorn main:app`），前端用部署的静态站 + 现场把 `VITE_API_BASE` 指到本机 IP。
