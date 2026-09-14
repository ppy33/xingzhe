"""行者 v2 · 配置加载

所有密钥从 .env 读取，绝不硬编码进代码。
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
ENV_PATH = BASE_DIR / ".env"

load_dotenv(ENV_PATH, override=False)


class Settings:
    """运行时配置。缺失关键项时给出明确报错，而不是静默失败。"""

    # ---- 高德开放平台 ----
    amap_server_key: str = os.getenv("AMAP_SERVER_KEY", "").strip()
    amap_jsapi_key: str = os.getenv("AMAP_JSAPI_KEY", "").strip()
    amap_jsapi_secret: str = os.getenv("AMAP_JSAPI_SECRET", "").strip()
    amap_base: str = os.getenv("AMAP_BASE", "https://restapi.amap.com").rstrip("/")

    # ---- DeepSeek ----
    deepseek_api_key: str = os.getenv("DEEPSEEK_API_KEY", "").strip()
    deepseek_base_url: str = os.getenv(
        "DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1"
    ).rstrip("/")
    deepseek_model: str = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-pro").strip()
    deepseek_model_fast: str = os.getenv(
        "DEEPSEEK_MODEL_FAST", "deepseek-flash"
    ).strip()

    # ---- 运行参数 ----
    http_timeout: float = float(os.getenv("HTTP_TIMEOUT", "15"))
    max_agent_steps: int = int(os.getenv("MAX_AGENT_STEPS", "25"))

    def require_amap(self) -> str:
        if not self.amap_server_key:
            raise RuntimeError(
                "缺少 AMAP_SERVER_KEY。请在 v2/backend/.env 中填写高德 Web服务 Key。"
            )
        return self.amap_server_key

    def require_llm(self) -> str:
        if not self.deepseek_api_key:
            raise RuntimeError(
                "缺少 DEEPSEEK_API_KEY。请在 v2/backend/.env 中填写 DeepSeek Key。"
            )
        return self.deepseek_api_key


settings = Settings()
