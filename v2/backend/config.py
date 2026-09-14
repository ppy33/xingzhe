"""行者 v2 · 配置加载

所有密钥从 .env 读取，绝不硬编码进代码。
支持前端运行时覆盖 LLM 配置（api_key / base_url / model），实现模型自定义。
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI

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

    # ---- 持久化（M5）----
    # SQLite 文件路径（相对 backend 目录）。行程与 Agent 对话记忆都落这里
    db_path: str = os.getenv("DB_PATH", "data/xingzhe.db").strip()

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


# ---------------------------------------------------------------------------
# 运行时 LLM 覆盖（前端「设置」面板可自定义模型，优先级高于 .env）
# ---------------------------------------------------------------------------

_llm_override: dict[str, str] = {}


def set_llm_override(cfg: dict[str, Any]) -> dict[str, str]:
    """设置运行时 LLM 覆盖配置。空值忽略（回落到 .env 默认）。返回生效配置。"""
    global _llm_override
    _llm_override = {
        k: str(v).strip()
        for k, v in cfg.items()
        if k in ("api_key", "base_url", "model", "model_fast") and str(v or "").strip()
    }
    return llm_config()


def get_llm_override() -> dict[str, str]:
    """返回当前的覆盖配置（不含 .env 默认值）。"""
    return dict(_llm_override)


def llm_config() -> dict[str, str]:
    """当前生效的 LLM 配置：前端覆盖优先，否则 .env 默认。"""
    return {
        "api_key": _llm_override.get("api_key") or settings.deepseek_api_key,
        "base_url": (_llm_override.get("base_url") or settings.deepseek_base_url).rstrip("/"),
        "model": _llm_override.get("model") or settings.deepseek_model,
        "model_fast": _llm_override.get("model_fast") or settings.deepseek_model_fast,
    }


def build_llm(
    model: str | None = None,
    fast: bool = False,
    temperature: float = 0.3,
    timeout: int = 120,
    max_retries: int = 2,
) -> ChatOpenAI:
    """统一构建 LLM（用当前生效配置）。

    - model 为 None 时按 fast 选主力/快速模型；显式传入则覆盖
    - thinking 禁用仅在 DeepSeek 系 base_url 下追加（其他 OpenAI 兼容模型不认该参数）
    - 缺少 api_key 时抛明确错误，不静默失败
    """
    cfg = llm_config()
    if not cfg["api_key"]:
        raise RuntimeError(
            "缺少 LLM API Key：请在前端「设置」面板填写，或在 v2/backend/.env 中配置"
        )
    extra_body = None
    if "deepseek" in cfg["base_url"].lower():
        extra_body = {"thinking": {"type": "disabled"}}
    return ChatOpenAI(
        model=model or (cfg["model_fast"] if fast else cfg["model"]),
        api_key=cfg["api_key"],
        base_url=cfg["base_url"],
        temperature=temperature,
        timeout=timeout,
        max_retries=max_retries,
        extra_body=extra_body,
    )
