# -*- coding: utf-8 -*-
"""gate_alarm.py — 闸门**异常**的统一报警（2026-09-25 用户拍板方向）。

背景（用户原话）：「像这种 bug 类的能不能直接抛到日志里，我们复盘就能看到，或者直接发给你
  （DSH）触发修复，而不是放行？」—— 本项目大量闸门用 `except Exception: pass/print` 兜底，
  一旦**代码级 bug**（如变量名写错、属性缺失）发生，就会**静默 fail-open**（放行），
  最典型：HPV 低吸路径判定用错变量名 ⇒ NameError 被吞 ⇒ 102~103 行"异常(放行)" ⇒ 补丁形同虚设 ✗

本模块提供**三件事**：
  ① **落盘**：每次异常写一行 JSONL 到 `<DATA_DIR>/gate_failures.jsonl`（可复盘、可统计）；
  ② **醒目**：stderr 打 `[GATE-ALARM] <where> …`（扫日志一眼可见）；
  ③ **默认不放行**（可选）：开关 `WOLF_GATE_FAIL_CLOSED=1` 时，对**安全方向是"拦"**的闸门
     返回 `True`（= 调用方按"命中"处理 ⇒ 拦住这条腿），而不是默默放行。

⚠️ 口径：默认（开关 0）**逐字保持旧行为**（放行）⇒ 生产零影响；回测 pins 置 1 以暴露问题。
   对"放行才安全"的闸门（例如"取不到数据就不停做T"）调用方应显式传 `fail_closed=False`。
"""
from __future__ import annotations

import json
import os
import sys
import time


def _silent_alert(where, exc=None):
    """静默点统一出口（账本 §9.545）：原来 `except …: pass/continue` 什么都不留 ⇒ 至少留痕。

    优先 `alert_hub.note_silent`（落盘 alerts.jsonl；QQ 受去重/限流约束）；不可用时退回 print。**绝不抛** ✓。
    """
    try:
        from app.services import alert_hub as _ah
        _ah.note_silent(where, exc)
    except Exception:
        try:
            print("[silent:%s] %s: %s" % (where, type(exc).__name__ if exc is not None else "",
                  str(exc)[:110] if exc is not None else ""), flush=True)
        except Exception:
            pass


def fail_closed_on() -> bool:
    """库内默认 0 ⇒ 逐字旧行为（异常放行）。回测由 pins 置 1。

    ⚠️ 必须**字面量**写 `os.getenv("WOLF_GATE_FAIL_CLOSED", "0")`：仓库的 guard_defaults_check
    是按字面量扫描"库内默认关"的 ✓（用常量间接读会被漏检 ✗ —— 本模块首版就是这么写的，测试当场抓到）。
    """
    return str(os.getenv("WOLF_GATE_FAIL_CLOSED", "0")).strip().lower() in ("1", "true", "yes", "on")


def _sink_path() -> str:
    d = os.environ.get("DATA_DIR") or os.path.join(os.getcwd(), "data")
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        return os.path.join(os.getcwd(), "gate_failures.jsonl")
    return os.path.join(d, "gate_failures.jsonl")


def alarm(where: str, exc: BaseException, ctx: dict = None, fail_closed: bool = True) -> bool:
    """记录一次闸门异常，返回**是否应停止后续判断**（True = 按"拦"处理）。

    · 落盘 + stderr 永远执行（复盘可见 ✓）；
    · 返回值：`fail_closed_on()` 且 `fail_closed=True` ⇒ True（拦住）；否则 False（放行，旧行为）。
    """
    rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "where": str(where)[:80],
           "exc_type": type(exc).__name__, "exc": str(exc)[:200],
           "fail_closed_on": fail_closed_on(), "ctx": ctx or {}}
    try:
        with open(_sink_path(), "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception as _e_sil1:
        _silent_alert("gate_alarm.py:56", _e_sil1)
    try:
        sys.stderr.write("[GATE-ALARM] %s | %s: %s | fail_closed=%s\n"
                         % (rec["where"], rec["exc_type"], rec["exc"], rec["fail_closed_on"]))
    except Exception as _e_sil2:
        _silent_alert("gate_alarm.py:61", _e_sil2)
    return bool(fail_closed and fail_closed_on())


def note(where: str, exc: BaseException, ctx: dict = None) -> None:
    """**只报警、绝不改行为**（等价于 `alarm(..., fail_closed=False)`）。

    用途：把仓库里大量「`except …: pass`（静默放行）」的兜底换成"**落盘可见**"，
    从而在不改变任何控制流的前提下，让复盘能看到"这里出过多少次异常、什么异常" ✓
    （用户 2026-09-25：「bug 类能不能直接抛到日志里，我们复盘就能看到」）
    """
    alarm(where, exc, ctx=ctx, fail_closed=False)
