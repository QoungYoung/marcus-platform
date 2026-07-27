# -*- coding: utf-8 -*-
"""ArkVol HTTP 客户端 — 封装 arkvol.com 数据接口调用。"""

import json
import os
import logging
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)

ARKVOL_BASE_URL = "https://arkvol.com"

PAGE_ENDPOINTS = {
    "alla": "/api/data/alla",
    "alla-tech": "/api/data/alla-tech",
    "funds-greed": "/api/data/funds-greed",
    "gll": "/api/data/gll",
    "greed-mid-term": "/api/data/greed/mid-term",
    "us7-rotation": "/api/data/us7-rotation",
    "global-capital-flow": "/api/data/global-capital-flow",
    "debt": "/api/data/debt",
    "low-52w-leverage": "/api/data/low-52w-leverage",
}

POST_ENDPOINTS = {
    "ai-summary": "/api/funds-greed/alla/ai-summary",
}


class ArkvolServiceError(RuntimeError):
    pass


def _read_api_key() -> str:
    env_key = os.environ.get("ARKVOL_API_KEY", "").strip()
    if env_key:
        return env_key

    config_paths = [
        Path.home() / ".arkvol" / "arkvol-entry.json",
        Path(os.environ.get("ARKVOL_CONFIG", "")) if os.environ.get("ARKVOL_CONFIG") else None,
    ]
    for cfg_path in config_paths:
        if cfg_path is None or not cfg_path.is_file():
            continue
        try:
            payload = json.loads(cfg_path.read_text(encoding="utf-8"))
            key = payload.get("api_key", "") if isinstance(payload, dict) else ""
            if key.strip():
                return key.strip()
        except (OSError, ValueError) as exc:
            logger.warning("读取 ArkVol 配置文件失败 %s: %s", cfg_path, exc)

    raise ArkvolServiceError(
        "未配置 ArkVol API Key。请设置环境变量 ARKVOL_API_KEY，"
        "或将 Key 写入 ~/.arkvol/arkvol-entry.json 的 api_key 字段。"
        "获取 Key: https://arkvol.com → 头像 → API Key"
    )


def _request_json(api_key: str, path: str, timeout: int = 30) -> Dict[str, Any]:
    url = f"{ARKVOL_BASE_URL}{path}"
    req = Request(url, headers={
        "X-API-Key": api_key,
        "X-Arkvol-Skill-Version": "0.3.1",
        "Accept": "application/json",
    }, method="GET")

    try:
        with urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except HTTPError as exc:
        try:
            body = json.loads(exc.read().decode("utf-8"))
        except Exception:
            body = {}
        msg = body.get("msg", "")
        if exc.code in (401, 403):
            raise ArkvolServiceError(msg or f"ArkVol API Key 无效或无权访问 (HTTP {exc.code})") from exc
        raise ArkvolServiceError(msg or f"ArkVol 返回 HTTP {exc.code}") from exc
    except URLError as exc:
        raise ArkvolServiceError(f"无法连接 ArkVol: {exc.reason}") from exc
    except (ValueError, UnicodeDecodeError) as exc:
        raise ArkvolServiceError("ArkVol 返回数据无法解析") from exc

    if not isinstance(payload, dict) or payload.get("code") != 0:
        raise ArkvolServiceError(payload.get("msg", "ArkVol 数据查询失败") if isinstance(payload, dict) else "响应格式错误")
    return payload


def _request_post_json(api_key: str, path: str, body: Optional[Dict] = None, timeout: int = 30) -> Dict[str, Any]:
    """POST 请求，用于 ai-summary 等端点。"""
    url = f"{ARKVOL_BASE_URL}{path}"
    data = json.dumps(body or {}).encode("utf-8")
    req = Request(url, data=data, headers={
        "X-API-Key": api_key,
        "X-Arkvol-Skill-Version": "0.3.1",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }, method="POST")

    try:
        with urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except HTTPError as exc:
        try:
            body_raw = json.loads(exc.read().decode("utf-8"))
        except Exception:
            body_raw = {}
        msg = body_raw.get("msg", "")
        if exc.code in (401, 403):
            raise ArkvolServiceError(msg or f"ArkVol API Key 无效或无权访问 (HTTP {exc.code})") from exc
        raise ArkvolServiceError(msg or f"ArkVol 返回 HTTP {exc.code}") from exc
    except URLError as exc:
        raise ArkvolServiceError(f"无法连接 ArkVol: {exc.reason}") from exc
    except (ValueError, UnicodeDecodeError) as exc:
        raise ArkvolServiceError("ArkVol 返回数据无法解析") from exc

    if not isinstance(payload, dict) or payload.get("code") != 0:
        raise ArkvolServiceError(payload.get("msg", "ArkVol 数据查询失败") if isinstance(payload, dict) else "响应格式错误")
    return payload


class ArkvolService:
    """ArkVol 数据服务单例。"""

    def __init__(self, api_key: Optional[str] = None):
        self._api_key = api_key

    @property
    def api_key(self) -> str:
        if self._api_key is None:
            self._api_key = _read_api_key()
        return self._api_key

    def fetch_page(self, page_id: str) -> Dict[str, Any]:
        """获取指定页面的完整数据（GET）。"""
        if page_id not in PAGE_ENDPOINTS:
            raise ArkvolServiceError(f"未知页面: {page_id}，可用: {list(PAGE_ENDPOINTS)}")
        endpoint = f"{PAGE_ENDPOINTS[page_id]}?view=full"
        payload = _request_json(self.api_key, endpoint)
        data = payload.get("data", {})
        if not isinstance(data, dict):
            raise ArkvolServiceError("ArkVol 返回数据格式异常")
        return data

    def fetch_ai_summary(self) -> Dict[str, Any]:
        """获取 AI 摘要（POST，轻量，不返回历史 series）。"""
        endpoint = POST_ENDPOINTS["ai-summary"]
        payload = _request_post_json(self.api_key, endpoint)
        data = payload.get("data", {})
        if not isinstance(data, dict):
            raise ArkvolServiceError("ArkVol ai-summary 返回数据格式异常")
        return data
