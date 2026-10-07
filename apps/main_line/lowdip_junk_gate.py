# -*- coding: utf-8 -*-
"""lowdip_junk_gate.py — 低吸腿（253/254）「窄杂毛门」：**业绩 bad ∧ PE 高**（交集）

用户 2026-09-21：「低吸腿按『结构到位』选出来的就不能过滤杂毛了吗」→「如果是叠加 PE 呢，我看天津普林一月的 PE 挺高的」
量化结论（见台账「续5」节，低吸腿 episode 级已实现盈亏、545 条、11 个臂）：
  · 只用业绩门：影响面 23%，剔除集净 +7,721（=剔除会**少赚**），9 改善/2 恶化
  · 只用 PE>300：影响面 11%，净 −21,634（剔除会赚），但 5/6 臂、被 year 单臂主导
  · 业绩门 ∨ PE>300：影响面 ~30%，净 −4,697，8/3
  · **业绩 bad ∧ PE>300（本门）**：影响面 **4%（21/545）**，净 **−9,216（剔除会赚）**，**6/8 臂改善** ← 选定
命中票只有三只：天津普林 13 条 −8,987、国星光电 2 条 −1,195、格尔软件 6 条 +967。

设计要点：
  · **取交集不取并集** —— 并集会把「高 PE 但赚钱」的那群（PE 100–200 档，胜率 78%、+17,807）一起砍掉。
  · **fail-open**：取不到业绩或 PE ⇒ 放行（绝不能因为取数失败把候选全砍了）。
  · **as-of 正确**：业绩走 `ann_date ≤ 当日` 的版本；PE 走当日/最近可用交易日的 `daily_basic`。
  · 生产零影响：开关默认 **0**；生产没预热 `data/_bt_fund` ⇒ 取不到数据 ⇒ 放行。

开关：
  WOLF_LOWDIP_JUNK_GATE=0        总开关
  WOLF_LOWDIP_JUNK_MAX_PE=300    PE(ttm) 上限（严格大于才拦）
  WOLF_LOWDIP_JUNK_EARN=bad      要求业绩为 bad 才拦（可设 'bad,flat' 之类扩展）
"""
from __future__ import annotations

import os
import sys
from typing import Any, Dict, Optional, Tuple


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


ENV = "WOLF_LOWDIP_JUNK_GATE"


def enabled() -> bool:
    return str(os.getenv(ENV, "0")).strip().lower() in ("1", "true", "yes", "on")


def max_pe() -> float:
    try:
        return float(os.getenv("WOLF_LOWDIP_JUNK_MAX_PE", "300") or 300)
    except Exception:
        return 300.0


def earn_flags() -> Tuple[str, ...]:
    raw = str(os.getenv("WOLF_LOWDIP_JUNK_EARN", "bad") or "bad")
    return tuple(x.strip() for x in raw.split(",") if x.strip())


def _fix_fund_root(mod):
    """把取数层的缓存根兜底到**仓库**里那份 data/_bt_fund —— **只在当前 ROOT 落空时**。

    为什么必须兜底：回测每个子进程的 DATA_DIR 会被指到**当天沙箱目录** ⇒ bt_fund_asof.ROOT 会变成
    `<day>/_bt_fund`（不存在）⇒ 业绩/PE/名称全取不到 ⇒ 判据**静默 fail-open**（"开了没效果"）。
    为什么必须"只在落空时"：显式设过的 ROOT（如单测夹具的临时目录）**不能被覆盖**，
    否则测试与离线脚本会去读真实缓存、结论失真（2026-09-21 实际踩到，见台账 L 节）。
    """
    import os as _os
    try:
        cur = getattr(mod, "ROOT", None)
        if cur and _os.path.isdir(cur):
            return
        root = _os.getenv("WOLF_FUND_ROOT")
        if not root:
            for p in _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))),:
                cand = _os.path.join(p, "data", "_bt_fund")
                if _os.path.isdir(cand):
                    root = cand
                    break
        if root and _os.path.isdir(root):
            mod.ROOT = root
    except Exception as _e_sil1:
        _silent_alert("lowdip_junk_gate.py:71", _e_sil1)


def _fund(tag: Optional[str] = None):
    """惰性加载取数层（jobs/bt_fund_asof.py）。取不到 ⇒ None（调用方 fail-open）。"""
    tag = tag or (os.getenv("WOLF_FUND_TAG") or os.getenv("SZ_TAG") or "").strip()
    if not tag:
        return None
    import importlib
    try:
        m = importlib.import_module("bt_fund_asof")
        _fix_fund_root(m)
        return m
    except ImportError as _e_sil2:
        _silent_alert("lowdip_junk_gate.py:85", _e_sil2)
    import pathlib
    for p in pathlib.Path(__file__).resolve().parents:
        if (p / "jobs" / "bt_fund_asof.py").exists():
            sys.path.insert(0, str(p / "jobs"))
            try:
                m = importlib.import_module("bt_fund_asof")
                _fix_fund_root(m)
                return m
            except Exception:
                return None
    return None


def _day(day: Optional[str]) -> Optional[str]:
    if day:
        return str(day)
    try:                       # 回测里 bt_run_pinned 把 clock 钉在 as-of 那天 ⇒ date.today() 就是当日
        import datetime as _dt
        return _dt.date.today().strftime("%Y%m%d")
    except Exception:
        return None


def blocked(symbol: Any, day: Optional[str] = None,
            tag: Optional[str] = None) -> Tuple[bool, str]:
    """返回 (是否拦, 原因)。任何取数失败/数据缺失 ⇒ (False, '')（fail-open）。"""
    if not enabled():
        return False, ""
    d = _day(day)
    F = _fund(tag)
    if F is None or not d:
        return False, ""
    sym = str(symbol or "")
    if not sym:
        return False, ""
    try:
        e = F.earnings_state(sym, d, tag or (os.getenv("WOLF_FUND_TAG") or os.getenv("SZ_TAG") or ""))
    except Exception:
        return False, ""
    if not e or not e.get("ok"):
        return False, ""                      # 没业绩数据 ⇒ 放行
    flag = str(e.get("flag") or "")
    if flag not in earn_flags():
        return False, ""
    try:
        v = F.val_state(sym, d, tag or (os.getenv("WOLF_FUND_TAG") or os.getenv("SZ_TAG") or ""))
    except Exception:
        return False, ""
    if not v or not v.get("ok"):
        return False, ""                      # 没估值数据 ⇒ 放行
    pe = v.get("pe_ttm")
    if pe is None:
        return False, ""
    try:
        pe = float(pe)
    except Exception:
        return False, ""
    if pe <= 0:                                # 亏损股 PE 无意义 —— 交回业绩判据决定（此处不因 PE 拦）
        return False, ""
    if pe > max_pe():
        return True, "杂毛门: 业绩=%s ∧ PE=%.0f>%.0f" % (flag, pe, max_pe())
    return False, ""


def filter_symbols(symbols, day: Optional[str] = None, tag: Optional[str] = None):
    """批量过滤；返回 (保留, {原因: 条数})。开关关时原样返回（零开销）。"""
    if not enabled():
        return list(symbols), {}
    kept, dropped = [], {}
    for s in symbols:
        ok, why = blocked(s, day=day, tag=tag)
        if ok:
            dropped[why] = dropped.get(why, 0) + 1
        else:
            kept.append(s)
    return kept, dropped
