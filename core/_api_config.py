# -*- coding: utf-8 -*-
"""共享 API 配置 — 所有配置统一从项目根目录 .env 读取，与环境变量保持一致"""
import os
from pathlib import Path


def _load_env():
    """从项目根目录加载 .env 文件到 os.environ（不覆盖已有的环境变量）。"""
    current = Path(__file__).resolve().parent
    for _ in range(6):
        candidate = current / ".env"
        if candidate.exists():
            with open(candidate, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, _, value = line.partition("=")
                    key = key.strip()
                    value = value.strip().strip('"').strip("'")
                    if key and key not in os.environ:
                        os.environ[key] = value
            return
        current = current.parent


_load_env()

# ── DeepSeek API ──────────────────────────────────────────
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_API_HOST = os.getenv("DEEPSEEK_API_HOST", "api.deepseek.com")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")

# ── Tushare API ───────────────────────────────────────────
TUSHARE_TOKEN = os.getenv("TUSHARE_TOKEN", "")


def get_tushare_pro():
    """
    统一获取 Tushare pro_api 实例（Token 从环境变量 TUSHARE_TOKEN 读取）。

    所有调用 ts.pro_api() 的地方都应改用此函数，确保 Token 统一从 .env 控制。
    """
    import tushare as ts

    token = os.getenv("TUSHARE_TOKEN", "")
    if not token:
        raise EnvironmentError("TUSHARE_TOKEN 未在环境变量或 .env 中配置")
    return ts.pro_api(token)
