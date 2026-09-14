# 行者 v2 后端镜像
FROM python:3.13-slim

# 减少镜像体积，避免 apt 缓存
WORKDIR /app

# 先装依赖（利用 Docker 层缓存：requirements 不变则跳过重装）
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 拷贝代码（不含 .env / data/，见 .dockerignore）
COPY . .

EXPOSE 8000

# 生产用多 worker 更稳；单 worker 足够个人演示
CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
