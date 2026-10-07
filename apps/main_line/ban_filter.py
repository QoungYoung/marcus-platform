# -*- coding: utf-8 -*-
"""ban_filter.py — G3「卖出后删票」黑名单的**统一读取口**（语料 2025-02-06 / 2025-04-03）

语料：
  · 2025-02-06「卖出然后删票」
  · 2025-04-03「破之前新低的直接删票」
实现：`backend/app/services/wolf_ticket_ban.py`（TTL 默认 13 交易日，只挡买入侧）。
但 2026-09-21 用户追问「天津普林和苏州科达这种的能挡住吗」时实测发现两个**静默失效**：

  ① **账户键写死**：`rotation_switch_arm._banned_from_service()` 与 `wolf_confirm_pick._banned_from_service()`
     都写死 `banned_symbols(["stock"])`。回测臂的账户是 `drabt2/drabt3/…`，名单键写的是 `drabt3:SH603660`
     ⇒ **回测臂读不到自己刚写进去的禁令**（生产账户恰好叫 stock 所以生产没坏，只有回测一直是坏的）。
     实测后果：天津普林 `drabt3:SZ002134` 于 20260116 被禁，T3 在 **0128 又买了 1400 股**。
  ② **那条腿根本不读名单**：名单只接在 `pick_buy`(pathA) 与 `pick_v2`(pathB)；
     `switch_builder.active_stocks_by()`（253/254 **低吸腿**的候选域）**零处引用**
     ⇒ 苏州科达 `drabt3:SH603660` 于 20260115 被禁，T3 在 **0122/0123 继续买**。
     实测这两只到 0128 的已实现亏损：天津普林 −1,452、苏州科达 −1,759 元。

本模块把"该查哪个账户 / 怎么判"收敛成一份实现，供各选股路径共用。
开关：`WOLF_TICKET_BAN_FIX=1`（库内默认 **0** ⇒ 生产零影响；回测 pins 置 1）。
      关着时行为与修复前**逐位一致**（调用方仍走老逻辑）。
"""
from __future__ import annotations

import os
import sys
from typing import Any, Dict, Optional, Set

ENV = "WOLF_TICKET_BAN_FIX"


def enabled() -> bool:
    return str(os.getenv(ENV, "0")).strip().lower() in ("1", "true", "yes", "on")


def current_account() -> str:
    """本次运行属于哪个账户。优先级：T_MONITOR_ACCOUNT → T_EXEC_ALLOWED_ACCOUNTS 第一个 → stock。

    回测里 runner 会导出 `T_MONITOR_ACCOUNT=drabt3`（见 .dsh-tmp/wolfbt/_run_t*.sh）；
    生产没设时回落到 `stock`（与修复前的硬编码一致 ⇒ 生产口径不变）。
    """
    a = (os.getenv("T_MONITOR_ACCOUNT") or "").strip()
    if not a:
        a = (os.getenv("T_EXEC_ALLOWED_ACCOUNTS") or "").split(",")[0].strip()
    return a or "stock"


def accounts_to_read() -> list:
    """要读哪些账户的名单。

    开着修复 ⇒ **本账户 ∪ "stock"**。为什么是并集：实测回测沙箱里同一份 `wolf_ticket_ban.json`
      两种键并存（`drabt3:SZ002134` 与 `stock:SH603660` 都在），因为写名单的代码路径有的用
      `T_MONITOR_ACCOUNT`、有的沿用默认 `stock`。只读本账户会漏掉一半禁令。
      生产里 current_account() 就是 `stock` ⇒ 并集退化成 `["stock"]`，**生产口径完全不变**。
    关着 ⇒ 保持历史行为 `["stock"]`（修复前逐位一致）。
    """
    if enabled():
        return sorted({current_account(), "stock"})
    return ["stock"]


def _ban_mod():
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    for _p in (root, os.path.join(root, "backend")):
        if _p and _p not in sys.path:
            sys.path.insert(0, _p)
    import importlib
    return importlib.import_module("app.services.wolf_ticket_ban")


def banned_map(today8: Optional[str] = None) -> Dict[str, Dict[str, Any]]:
    """{symbol(前缀式, 如 SZ002134): state}；任何异常 ⇒ 空 dict（fail-open，与历史一致）。"""
    try:
        return dict(_ban_mod().banned_symbols(accounts_to_read(), today8) or {})
    except Exception as e:
        print("[ban_filter] 删票名单读取失败(fail-open): %s" % str(e)[:100], flush=True)
        return {}


def _codes(sym: Any) -> Set[str]:
    """把各种写法归一成可比较的集合：6 位代码 + 前缀式 + 后缀式。"""
    s = str(sym or "").strip().upper()
    out = set()
    if not s:
        return out
    if "." in s:
        c, m = s.split(".")[0][:6], s.split(".")[-1]
        out |= {c, m + c, c + "." + m}
    elif len(s) == 8 and s[:2] in ("SH", "SZ", "BJ"):
        out |= {s[2:], s, s[2:] + "." + s[:2]}
    elif len(s) == 6 and s.isdigit():
        out.add(s)
        for m in ("SH", "SZ", "BJ"):
            out |= {m + s, s + "." + m}
    else:
        out.add(s)
    return out


def banned_codes(today8: Optional[str] = None) -> Set[str]:
    """所有"被删票"的 6 位代码集合（含各种写法的归一）。"""
    out: Set[str] = set()
    for k, st in banned_map(today8).items():
        for c in _codes(st.get("symbol") or k.split(":")[-1]):
            if len(c) == 6 and c.isdigit():
                out.add(c)
    return out


def is_banned(sym: Any, today8: Optional[str] = None) -> bool:
    if not enabled():
        return False
    c6 = str(sym or "").strip().upper()
    if "." in c6:
        c6 = c6.split(".")[0]
    if c6[:2] in ("SH", "SZ", "BJ") and len(c6) == 8:
        c6 = c6[2:]
    return c6 in banned_codes(today8)
