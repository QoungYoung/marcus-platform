# -*- coding: utf-8 -*-
"""Shared API configuration (DeepSeek, Tushare) — 统一从项目根 .env 读取"""
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

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_API_HOST = os.getenv("DEEPSEEK_API_HOST", "api.deepseek.com")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
TUSHARE_TOKEN = os.getenv("TUSHARE_TOKEN", "")