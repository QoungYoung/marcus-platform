# -*- coding: utf-8 -*-
"""alert_logging.py —— **把 logging 出口接到告警中心**（账本 §9.623 ✓）

## 为什么需要它 ✓（用户 2026-10-05 质问 ✓）

> 「不是说好全局异常做完了吗，怎么这么多没接的」

- 实测（AST 审计 ✓ `jobs/scan_unwired_failures.py`）：
  **1892 处"失败即降级"路径**（`except` 里只 `logger.warning` ✗／`if rc != 0` 只 print ✗）／396 个文件 ✗
- ⇒ ⇒ **逐点接线不现实** ✗（1,892 处 ✓）⇒ ⇒ ⇒ **正解：接"出口"** ✓

## 做法 ✓

装一个 **root logger handler** ⇒ 凡是 `logger.warning/error(...)` 的日志 ✓：
  · **WARNING** ⇒ `alert_hub.note_silent`（**落盘** ✓，可在看板「告警回看」看到 ✓；**不推 QQ** ✓ 防刷屏 ✓）
  · **ERROR**   ⇒ `alert_hub.note`（**落盘 ＋ 推 QQ** ✓，受去重 600s／限流约束 ✓）
**不需要改任何调用点** ✓ —— 一次覆盖全部 logging 用户 ✓

开关 ✓：`WOLF_ALERT_FROM_LOGGING=1`（**库内默认 0** ⇒ 生产零影响 ✓）
用法 ✓：`from core import alert_logging; alert_logging.install()`
"""
from __future__ import annotations
import logging
import os
import sys

_INSTALLED = False
# 噪音黑名单（这些 logger 名/前缀**不转发** ✗ —— 它们本身就是"已知无害"的降级 ✓）
_SKIP = ("uvicorn.access", "asyncio", "matplotlib", "urllib3", "PIL")


class _AlertHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:  # noqa: D102
        try:
            name = str(record.name or "")
            for s in _SKIP:
                if name.startswith(s):
                    return
            msg = "[%s] %s" % (name, str(record.getMessage())[:280])
            where = "log:%s:%s" % (record.pathname.rsplit("/", 1)[-1], record.lineno)
            try:
                from app.services import alert_hub as _ah
            except Exception:
                _ah = None
            if _ah is None:
                return
            if record.levelno >= logging.ERROR:
                _ah.note(where, msg=msg)          # 落盘 ＋ 推 QQ ✓
            else:
                _ah.note_silent(where, msg=msg)   # 只落盘 ✓（WARNING ⇒ 不推，防刷屏 ✓）
        except Exception as _e_em:
            # ★ 告警出口本身绝不抛 ✓，但**也要留痕** ✓（防回潮门要求 ✓；stderr 不经 logger ⇒ 不会递归 ✓）
            print("[alert_logging] emit 失败: %s" % str(_e_em)[:70], file=sys.stderr, flush=True)


def install(level: int = logging.WARNING) -> bool:
    """幂等安装 ✓；返回是否安装成功 ✓。"""
    global _INSTALLED
    if _INSTALLED:
        return True
    if str(os.getenv("WOLF_ALERT_FROM_LOGGING", "0")).strip().lower() not in ("1", "true", "yes", "on"):
        return False
    try:
        h = _AlertHandler(level=level)
        h.setFormatter(logging.Formatter("%(message)s"))
        logging.getLogger().addHandler(h)
        _INSTALLED = True
        print("[alert_logging] ✅ 已把 logging(WARNING+) 接到告警中心 ✓（ERROR ⇒ 推 QQ ✓）", file=sys.stderr, flush=True)
        return True
    except Exception as e:
        print("[alert_logging] 安装失败: %s" % str(e)[:80], file=sys.stderr, flush=True)
        return False


if __name__ == "__main__":
    print(install())
