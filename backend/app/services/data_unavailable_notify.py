# -*- coding: utf-8 -*-
"""
数据不可用 → QQ 通知助手（fail-closed 配套）。

自动建仓通道（长期/短期候选池监控器）遇到 check_entry_filters 返回
data_unavailable 非空时调用：跳过建仓 + 推送 QQ 通知。
去重规则：同一交易日同一标的只推送一次（跨轮询/跨监控器都不重复）。
"""

import sys
import threading
from datetime import datetime
from typing import List

_lock = threading.Lock()
# key: "{YYYY-MM-DD}|{symbol}" → 首次推送时间；跨日自动清旧
_notified: dict = {}


def _cleanup(today: str) -> None:
    for k in [k for k in _notified if not k.startswith(today + "|")]:
        _notified.pop(k, None)


def notify_data_unavailable(symbol: str, name: str, missing: List[str]) -> bool:
    """数据不可用时调用（fail-closed 跳过前的通知）。

    Args:
        symbol: 标的代码（如 SH688072）
        name: 标的名称（可为空）
        missing: 缺失项列表，如 ["60分MA", "日内分位", "主力资金"]

    Returns:
        True=本次已推送；False=同日已推送过（去重，不再发）
    """
    now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    key = f"{today}|{symbol}"

    with _lock:
        _cleanup(today)
        if key in _notified:
            return False
        _notified[key] = now.isoformat(timespec="seconds")

    missing_str = "、".join(missing) if missing else "未知"
    msg = (
        "⚠️ 建仓被拦截：数据不可用（fail-closed）\n"
        f"标的：{name}({symbol})\n"
        f"缺失项：{missing_str}\n"
        f"时间：{now.strftime('%H:%M:%S')}\n"
        "处理：自动通道已跳过该标的，未建仓；数据恢复后自动重评"
    )
    try:
        from app.services.qqbot_service import send_qq_notification
        send_qq_notification(msg)
    except Exception as e:
        print(f"[数据不可用通知] {symbol} 推送失败: {e}", file=sys.stderr)
    return True
