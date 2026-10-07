# -*- coding: utf-8 -*-
"""universe_clean.py — 选股域卫生过滤（退市 / ST / 北交所），**as-of 正确**

用户 2026-09-21：「过滤里去掉退市，ST，北交所的票吧」。

为什么单独一个模块：这套过滤此前散在三处、口径还不一样 ——
  · `rotation_switch_arm.board_ok()` 只管**账户权限板块**（cyb/bj/kcb），不管 ST/退市；
  · `backend/app/services/t_build._fetch_all_a_symbols()` 管 ST/退市/北交所，但走**实时 tushare**
    且只在生产 T 扫描里用，回测通道取不到；
  · `wolf_confirm_pick` 里又有一份 `is_st=1 OR name LIKE 'ST%'` 的 SQL。
⇒ 本模块收敛成一个判据，选股侧（trend_channel 等）与任何新通道都用它。

**as-of 纪律（用户反复强调）**：不能拿"今天的名字"去过滤 1 月的票 —— 一只票可能 6 月才戴帽，
用今天的 ST 状态过滤历史 = 未来函数，而且方向上是"提前躲开后来的输家"，会虚高回测。
所以名称走 `tushare namechange`（曾用名历史，带 start_date/end_date）重建**当日真实简称**：
  实测 000004.SZ @20260108 = **ST 国华**（原文带星号）（今天叫国华网安）—— 用今天的名字根本判不出来。
北交所用**代码规则**（920/430/83/87/88 + BJ 后缀），确定性、天然无未来函数。
退市用 `stock_basic(list_status='D')` 的 `delist_date`：只有 `delist_date <= 当日` 才算"已退市"。

开关：WOLF_UNIVERSE_CLEAN=0（库内默认关 ⇒ 生产零影响；回测 pins 置 1）
      WOLF_UNIVERSE_CLEAN_BJ=1      去掉北交所（确定性问题，默认项）
      WOLF_UNIVERSE_CLEAN_ST=1      去掉 ST/*ST（按 as-of 名称）
      WOLF_UNIVERSE_CLEAN_DELIST=1  去掉退市整理期/已退市
      WOLF_UNIVERSE_CLEAN_SNAPSHOT=0  **无 as-of 名称时是否回退用当前快照**
        —— 默认 0（严格 as-of：没有 as-of 名称就**放行**，绝不拿今天的名字判历史）；
           置 1 更"干净"但引入轻度未来函数（提前躲开后来戴帽的票）⇒ 回测里别开。
            例外：**没有 WOLF_FUND_TAG（= 不在回测里）时快照就是"当下真实名称"**，直接用它
            （生产口径正确，不是穿越未来）。
"""
from __future__ import annotations

import os
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


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


ENV = "WOLF_UNIVERSE_CLEAN"
NAME_KW = ("ST", "退")            # *ST / ST / 退市整理期简称里都带这两个字
_MEMO: Dict[str, Any] = {}


def enabled() -> bool:
    return str(os.getenv(ENV, "0")).strip().lower() in ("1", "true", "yes", "on")


def _on(name: str, dflt: str = "1") -> bool:
    return str(os.getenv(name, dflt)).strip().lower() in ("1", "true", "yes", "on")


def clean_bj() -> bool:
    return _on("WOLF_UNIVERSE_CLEAN_BJ", "1")


def clean_st() -> bool:
    return _on("WOLF_UNIVERSE_CLEAN_ST", "1")


def clean_delist() -> bool:
    return _on("WOLF_UNIVERSE_CLEAN_DELIST", "1")


def use_snapshot() -> bool:
    """默认 0：无 as-of 名称 ⇒ 放行（宁漏勿穿越未来）。"""
    return _on("WOLF_UNIVERSE_CLEAN_SNAPSHOT", "0")


def _pair(sym: Any) -> Tuple[str, str]:
    """统一 (前缀式, 后缀式)：'SH600895' / '600895.SH' / '600895' 都能吃。"""
    s = str(sym or "").strip().upper()
    if not s:
        return "", ""
    if "." in s:
        c, _m = s.split(".")[0], s.split(".")[-1]
        c = c[:6]
        return _m + c, c + "." + _m
    if len(s) == 8 and s[:2] in ("SH", "SZ", "BJ"):
        return s, s[2:] + "." + s[:2]
    if len(s) == 6 and s.isdigit():
        m = ("SH" if s[0] in "56" else "SZ" if s[0] in "03" else "BJ")
        return m + s, s + "." + m
    return s, s


def is_bj(sym: Any) -> bool:
    """北交所：前缀式 BJ / 后缀 .BJ / 代码段 920 / 430 / 83x / 87x / 88x（含 4/8 开头的三板段）。"""
    pre, suf = _pair(sym)
    if not pre:
        return False
    if pre.startswith("BJ") or suf.endswith(".BJ"):
        return True
    c = pre[2:8]
    return c.startswith(("920", "430", "83", "87", "88")) or c[:1] in ("4", "8")


def name_bad(name: Optional[str]) -> Tuple[bool, str]:
    """名称是否属于「ST / 退市」类。空名 ⇒ 不判（fail-open）。"""
    nm = (name or "").strip()
    if not nm:
        return False, ""
    up = nm.upper()
    if "ST" in up:
        return True, "ST:%s" % nm
    if "退" in nm:
        return True, "退市:%s" % nm
    return False, ""


def _snapshot() -> Dict[str, Dict[str, Any]]:
    """当前快照（DB stock_pool，退化为本地 sqlite）——只在显式允许时用作兜底。"""
    if "snap" in _MEMO:
        return _MEMO["snap"]
    out: Dict[str, Dict[str, Any]] = {}
    try:
        import psycopg2
        conn = psycopg2.connect(os.getenv("DATABASE_URL",
                                          "postgresql://marcus:marcus123@postgres:5432/marcus_trading"))
        cur = conn.cursor()
        cur.execute("select ts_code, name, is_st from stock_pool")
        for c, n, st in cur.fetchall():
            out[str(c)] = {"name": str(n or ""), "is_st": int(st or 0)}
        cur.close(); conn.close()
    except Exception:
        out = {}
    if not out:
        try:
            import sqlite3
            data = os.environ.get("DATA_DIR", "data")
            con = sqlite3.connect(os.path.join(data, "stock_pool.db"))
            for c, n in con.execute("select ts_code, name from stock_pool"):
                out[str(c)] = {"name": str(n or ""), "is_st": 0}
            con.close()
        except Exception:
            out = {}
    _MEMO["snap"] = out
    return out


def in_backtest() -> bool:
    """是否处于「有 as-of 参考缓存」的回测上下文（= 不能用当前名称判历史）。"""
    tag = (os.getenv("WOLF_FUND_TAG") or os.getenv("SZ_TAG") or "").strip()
    F = _fund(tag)
    if F is None:
        return False
    try:
        return os.path.isdir(os.path.join(F.ROOT, getattr(F, "REF_TAG", "_ref"), "namechange"))
    except Exception:
        return False


def bad_symbols() -> set:
    """**一次性**返回应剔除的全集（用**当前快照** ⇒ 生产口径正确）。

    生产里并进 `rotation_switch_arm.bad_set()` 即可让**所有**选股路径自动继承，
    不必逐条路径改判据（这是生产最小改动接法）。
    ⚠️ 回测里**禁用**：快照是"今天的名字"，拿去判 1 月的票就是未来函数 ⇒ 回测请逐票
       `judge(sym, day)`（as-of 名称）。这里检测到回测上下文就直接返回空集。
    """
    if not enabled():
        return set()
    if in_backtest():
        print("[universe_clean] 回测上下文 ⇒ bad_symbols() 返回空集（请用 judge(sym, day) 走 as-of）", flush=True)
        return set()
    out = set()
    for ts, info in (_snapshot() or {}).items():
        try:
            if clean_bj() and is_bj(ts):
                out.add(ts); continue
            if clean_st():
                bad, _why = name_bad(info.get("name"))
                if bad or int(info.get("is_st") or 0) == 1:
                    out.add(ts); continue
            if clean_delist():
                bad, _why = name_bad(info.get("name"))
                if bad:
                    out.add(ts)
        except Exception as _e_sil1:
            _silent_alert("universe_clean.py:175", _e_sil1)
            continue
    return out


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
    except Exception as _e_sil2:
        _silent_alert("universe_clean.py:202", _e_sil2)


def _fund(tag: Optional[str]):
    if tag is None:
        tag = (os.getenv("WOLF_FUND_TAG") or os.getenv("SZ_TAG") or "").strip()
    if not tag:
        return None
    if "fund" in _MEMO and _MEMO["fund"][0] == tag:
        return _MEMO["fund"][1]
    try:
        import importlib
        try:
            m = importlib.import_module("bt_fund_asof")
        except ImportError:
            import pathlib, sys
            for p in pathlib.Path(__file__).resolve().parents:
                if (p / "jobs" / "bt_fund_asof.py").exists():
                    sys.path.insert(0, str(p / "jobs"))
                    break
            m = importlib.import_module("bt_fund_asof")
    except Exception:
        m = None
    if m is not None:
        try:
            _fix_fund_root(m)
        except Exception as _e_sil3:
            _silent_alert("universe_clean.py:229", _e_sil3)
    _MEMO["fund"] = (tag, m)
    return m


# ────────────────────────────────── 判据 ──────────────────────────────────
def judge(symbol: Any, day: Optional[str] = None, tag: Optional[str] = None) -> Tuple[bool, str]:
    """返回 (是否可留, 原因)。原因非空 = 被剔除的理由（便于日志与审计）。"""
    if not enabled():
        return True, ""
    pre, suf = _pair(symbol)
    if not pre:
        return True, ""
    if clean_bj() and is_bj(symbol):
        return False, "北交所"
    nm, src = None, ""
    tag_eff = (tag if tag is not None
               else (os.getenv("WOLF_FUND_TAG") or os.getenv("SZ_TAG") or "").strip())
    F = _fund(tag_eff)
    if day is None:
        # 没有显式日期 ⇒ 用"当天"。回测里 bt_run_pinned 把 clock 钉在 as-of 那天,
        # 生产里就是真今天 ⇒ 两条路径都拿到正确的 as-of 日期。
        try:
            import datetime as _dt
            day = _dt.date.today().strftime("%Y%m%d")
        except Exception:
            day = None
    if F is not None and day and tag_eff:
        try:
            nm = F.name_as_of(suf, str(day), tag_eff)
            src = "as-of名称"
        except Exception:
            nm = None
    # 快照可用条件：显式允许，或**根本没在回测里**。
    # 「在回测里」的判据不能只看 tag：生产也可能恰好有 SZ_TAG —— 真正的信号是
    # **本地有没有 namechange 参考缓存**（回测才预热它）。没有缓存 ⇒ 当下名称就是"当下真实"，口径正确。
    snap_ok = use_snapshot() or (not in_backtest())
    if clean_st():
        if nm is not None:
            bad, why = name_bad(nm)
            if bad:
                return False, why
        elif snap_ok:
            snap = _snapshot().get(suf) or {}
            bad, why = name_bad(snap.get("name"))
            if bad:
                return False, why + "(快照)"
            if int(snap.get("is_st") or 0) == 1:
                return False, "is_st=1(快照)"
    if clean_delist():
        if F is not None and day:
            try:
                if F.delisted_by(suf, str(day), (tag or os.getenv("WOLF_FUND_TAG") or os.getenv("SZ_TAG") or "")):
                    return False, "已退市"
            except Exception as _e_sil4:
                _silent_alert("universe_clean.py:284", _e_sil4)
        if nm is None and snap_ok:
            bad, why = name_bad((_snapshot().get(suf) or {}).get("name"))
            if bad:
                return False, why + "(快照)"
    return True, ""


def filter_symbols(symbols: Sequence[Any], day: Optional[str] = None,
                   tag: Optional[str] = None) -> Tuple[List[Any], Dict[str, int]]:
    """批量过滤；返回 (保留列表, {原因: 条数})。开关关时原样返回（零开销）。"""
    if not enabled():
        return list(symbols), {}
    kept: List[Any] = []
    dropped: Dict[str, int] = {}
    for s in symbols:
        ok, why = judge(s, day=day, tag=tag)
        if ok:
            kept.append(s)
        else:
            key = why.split(":")[0].split("(")[0]
            dropped[key] = dropped.get(key, 0) + 1
    return kept, dropped
