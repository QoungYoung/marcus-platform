#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ArkVol 每日签到脚本
执行时间：每天定时跑一次（见 config/tasks.yaml 的 arkvol_checkin 任务）

功能:
1. 复用现有 .env 中的 ArkVol 登录态（ARKVOL_COOKIE = access_token_cookie=<JWT>）
2. POST https://arkvol.com/user/checkin 完成每日签到
3. 打印签到结果（scheduler 自动捕获并推送到QQ）

说明:
- 与 arkvol_service.py 的登录态来源一致（ARKVOL_COOKIE 或 ~/.arkvol/arkvol-entry.json）
- g_state 为 Google 同意 Cookie，可选；若配置 ARKVOL_G_STATE 则一并带上
"""

import os
import sys
import json
import logging
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

CHECKIN_URL = "https://arkvol.com/user/checkin"


def _load_env():
    """与现有 jobs 脚本一致：从仓库根目录 .env 加载环境变量（不覆盖已存在的）"""
    env_file = Path(__file__).resolve().parent.parent / ".env"
    if not env_file.exists():
        logger.warning(".env not found: %s", env_file)
        return
    with open(env_file, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


def _read_config() -> dict:
    """读取 ArkVol 登录态（与环境变量同名逻辑，与 arkvol_service.py 保持一致）"""
    # 1) 环境变量
    env_cookie = os.environ.get("ARKVOL_COOKIE", "").strip()
    env_g_state = os.environ.get("ARKVOL_G_STATE", "").strip()

    # 2) 可选配置文件
    file_cookie, file_g_state = "", ""
    candidates = [
        Path.home() / ".arkvol" / "arkvol-entry.json",
        Path(os.environ.get("ARKVOL_CONFIG", "")) if os.environ.get("ARKVOL_CONFIG") else None,
    ]
    for cfg_path in candidates:
        if cfg_path is None or not cfg_path.is_file():
            continue
        try:
            payload = json.loads(cfg_path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                file_cookie = str(payload.get("cookie", "") or "")
                file_g_state = str(payload.get("g_state", "") or "")
            break
        except (OSError, ValueError) as exc:
            logger.warning("读取 ArkVol 配置文件失败 %s: %s", cfg_path, exc)

    cookie = env_cookie or file_cookie
    g_state = env_g_state or file_g_state
    return {"cookie": cookie, "g_state": g_state}


def _extract_access_token(cookie: str) -> str:
    """从 ARKVOL_COOKIE (形如 access_token_cookie=<JWT>) 中提取 JWT"""
    token = ""
    for part in cookie.split(";"):
        part = part.strip()
        if not part:
            continue
        if "=" in part:
            k, _, v = part.partition("=")
            if k.strip() == "access_token_cookie":
                token = v.strip()
    return token


def checkin() -> str:
    """执行 ArkVol 每日签到，返回可读结果"""
    cfg = _read_config()
    cookie = cfg["cookie"]
    g_state = cfg["g_state"]

    token = _extract_access_token(cookie)
    if not token:
        raise RuntimeError(
            "未获取到 ArkVol access_token_cookie。请设置 ARKVOL_COOKIE="
            "access_token_cookie=<JWT>（或 ~/.arkvol/arkvol-entry.json 的 cookie 字段）。"
        )

    # Cookie 头：g_state（可选）+ access_token_cookie
    cookie_parts = []
    if g_state:
        # g_state 本身形如 {"i_l":...}，需要放到 g_state= 后面
        cookie_parts.append(f"g_state={g_state}")
    cookie_parts.append(f"access_token_cookie={token}")
    cookie_header = "; ".join(cookie_parts)

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Cookie": cookie_header,
        # 空 body，content-length 自动为 0
    }

    req = Request(CHECKIN_URL, data=b"", headers=headers, method="POST")
    try:
        with urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            status = resp.status
    except HTTPError as exc:
        raw = ""
        try:
            raw = exc.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        status = exc.code
        # “今日已签到”属于正常/良性情况，不当作失败（避免每天误报 QQ 失败通知）
        # 服务端以 \uXXXX 转义返回 JSON，需先解码 msg 字段再判断
        try:
            body_json = json.loads(raw)
            err_msg = str(body_json.get("msg") or body_json.get("message") or raw)
        except (ValueError, TypeError):
            err_msg = raw
        if "已签到" in err_msg:
            return f"ArkVol 今日已签到（无需重复签到） (HTTP {status}): {err_msg}"
        raise RuntimeError(f"ArkVol 签到失败 (HTTP {status}): {err_msg}") from exc
    except URLError as exc:
        raise RuntimeError(f"无法连接 ArkVol: {exc.reason}") from exc

    # 尝试解析 JSON，尽量给出友好信息
    try:
        payload = json.loads(raw)
        msg = payload.get("msg") or payload.get("message") or payload.get("data") or raw
    except (ValueError, TypeError):
        msg = raw
    return f"ArkVol 签到成功 (HTTP {status}): {msg}"


def main():
    _load_env()
    try:
        result = checkin()
    except Exception as exc:
        logger.error("ArkVol 签到失败: %s", exc)
        print(f"❌ ArkVol 签到失败: {exc}")
        sys.exit(1)

    logger.info(result)
    print(f"✅ {result}")


if __name__ == "__main__":
    main()
