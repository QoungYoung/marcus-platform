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

# ── Tushare 数据源（中继） ────────────────────────────────
# 2026-09-13：gzcloud 代理 token 已失效，取数统一改走 core/tushare_relay.py
# （datahubco 基础接口 + promax 聚合接口，GET + X-API-Key）。
TUSHARE_TOKEN = os.getenv("TUSHARE_TOKEN", "")
TUSHARE_API_URL = os.getenv("TUSHARE_API_URL", "")  # deprecated: 仅 TUSHARE_SOURCE=legacy 时使用


def _relay_module():
    """加载同目录下的 tushare_relay（backend/app/core/trading/_api_config.py 用同一份实现）。"""
    try:
        from . import tushare_relay  # type: ignore
        return tushare_relay
    except ImportError:
        import tushare_relay  # type: ignore  # core/ 在 sys.path 时
        return tushare_relay


def get_tushare_pro():
    """
    统一获取 Tushare 数据客户端（datahubco + promax 中继，替代已失效的 gzcloud 代理）。

    返回对象与 `tushare.pro.client.DataApi` 兼容（pro.daily(...) / pro.query(...)）。
    无可用数据源时抛 EnvironmentError。
    """
    return _relay_module().get_relay()


def tushare_relay_ready() -> bool:
    """是否至少配置了一个可用数据源（DATAHUBCO_API_KEY / PROMAX_API_KEY）。"""
    try:
        return bool(_relay_module().available_sources())
    except Exception:
        return False


def ensure_tushare_ready() -> None:
    """无可用数据源时抛 EnvironmentError。"""
    if not tushare_relay_ready():
        raise EnvironmentError(
            "未配置 Tushare 数据源：请在 .env 配置 DATAHUBCO_API_KEY 和/或 PROMAX_API_KEY"
        )
